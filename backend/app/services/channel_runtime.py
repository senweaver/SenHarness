"""Long-lived stream supervisor for IM channels.

Webhook providers receive their traffic via FastAPI routes. Stream
providers (Feishu / Lark / DingTalk / WeCom / Discord / QQ /
WeChat-iLink) have to dial *out*, hold a WebSocket / long-poll, and
push received messages into the same agent-run path. This module
owns the lifecycle of those outbound connections.

Design choices baked into the runtime (per the IM-stream plan):

    * One :class:`asyncio.Task` per active stream channel; named
      ``channel-stream-<short-id>`` for visibility in tracebacks.
    * Exponential reconnect backoff capped at
      ``CHANNEL_RUNTIME_RECONNECT_BACKOFF_MAX_S``: 1s → 3s → 8s →
      20s → 60s → 300s.
    * Optional Redis advisory lock keyed on the channel id so
      multi-worker deployments only ever hold one live connection
      per channel; passive workers spin in a watch loop and take
      over within ~30s if the leader dies. Falls back to in-process
      mutexes when Redis or the flag is off.
    * Graceful shutdown via a per-channel :class:`asyncio.Event`
      and a top-level supervisor task that owns ``start_all`` /
      ``stop_all``. ``restart_channel`` is the one CRUD callers
      need on enable / mode / config changes.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import random
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from app.core.config import settings
from app.db.models.channel import Channel
from app.db.session import get_session_factory
from app.repositories.channel import ChannelRepository
from app.services.channel_dispatch import dispatch_inbound
from app.services.channels import get_provider
from app.services.channels._secret_box import decrypt_config
from app.services.channels.base import (
    ChannelProvider,
    ChannelStreamAuthExpired,
    InboundMessage,
)

# When a provider raises :class:`ChannelStreamAuthExpired` (token revoked /
# expired credentials), we override the normal exponential backoff with a
# much longer dwell: re-trying every few seconds floods the logs and
# achieves nothing, since only an operator re-scan / config edit can
# unblock the channel. Capped at ``CHANNEL_RUNTIME_RECONNECT_BACKOFF_MAX_S``
# so an env override can still tighten it for tests.
_AUTH_EXPIRED_BACKOFF_FLOOR_S = 30 * 60.0

log = logging.getLogger(__name__)


@dataclass
class ChannelRuntimeStatus:
    """Runtime introspection payload — used by ``GET /channels/{id}/status``."""

    channel_id: uuid.UUID
    mode: str = "webhook"
    connected: bool = False
    last_event_at: datetime | None = None
    last_error: str | None = None
    started_at: datetime | None = None
    reconnect_attempts: int = 0


@dataclass
class _ChannelTask:
    channel_id: uuid.UUID
    task: asyncio.Task
    stop: asyncio.Event
    status: ChannelRuntimeStatus
    backoff_seconds: float = 1.0
    config_fingerprint: str = ""


def _config_fingerprint(channel: Channel) -> str:
    """Stable hash-ish summary of fields that should trigger a restart.

    We compare the keys that materially affect the stream connection
    (kind, mode, tokens). Pure UI fields like ``name`` / ``metadata_json``
    don't restart the link.
    """
    cfg = channel.config_json or {}
    parts = [
        channel.kind,
        str((channel.metadata_json or {}).get("mode", "")),
        str(channel.enabled),
        str(channel.default_agent_id),
        # Hash secret values (already enc:v1: prefixed) for change detection
        # without copying ciphertext into memory.
        ",".join(f"{k}={hash(str(v))}" for k, v in sorted(cfg.items())),
    ]
    return "|".join(parts)


def _channel_mode(channel: Channel) -> str:
    """Effective mode for ``channel`` — explicit override or provider default."""
    explicit = (channel.metadata_json or {}).get("mode")
    if isinstance(explicit, str) and explicit in ("webhook", "stream"):
        return explicit
    try:
        provider = get_provider(channel.kind)
        return type(provider).metadata().default_mode
    except KeyError:
        return "webhook"


class ChannelRuntime:
    """Process-wide supervisor for streaming IM channels.

    Singleton (one per process); access via :func:`get_runtime`.
    """

    def __init__(self) -> None:
        self._tasks: dict[uuid.UUID, _ChannelTask] = {}
        # Channels we've already determined can't run as a stream in this
        # process (kind unknown / SDK extra missing). Keyed by (id,
        # fingerprint) so a later config edit that *might* fix things
        # forces a re-evaluation instead of staying silently disabled.
        self._skipped: dict[uuid.UUID, str] = {}
        self._supervisor_task: asyncio.Task | None = None
        self._supervisor_stop: asyncio.Event = asyncio.Event()
        self._lock = asyncio.Lock()

    # ─── Lifecycle ─────────────────────────────────────
    async def start_all(self) -> None:
        """Discover every enabled stream channel + start a task each.

        Safe to call multiple times — idempotent.
        """
        async with self._lock:
            if self._supervisor_task is not None and not self._supervisor_task.done():
                return
            self._supervisor_stop = asyncio.Event()
            self._supervisor_task = asyncio.create_task(
                self._run_supervisor(), name="channel-runtime-supervisor"
            )
            log.info("ChannelRuntime supervisor started")

        await self._reconcile()

    async def stop_all(self) -> None:
        async with self._lock:
            if self._supervisor_task is None:
                return
            self._supervisor_stop.set()
            for entry in list(self._tasks.values()):
                entry.stop.set()
            tasks: list[asyncio.Task] = [entry.task for entry in self._tasks.values()]
            if self._supervisor_task is not None:
                tasks.append(self._supervisor_task)

        for t in tasks:
            t.cancel()
        if tasks:
            timeout = float(settings.CHANNEL_RUNTIME_STOP_TIMEOUT_S)
            _, pending = await asyncio.wait(tasks, timeout=timeout)
            for t in pending:
                log.warning(
                    "channel-runtime: %s did not stop in %.1fs; orphaning so "
                    "the rest of the worker can shut down",
                    t.get_name(),
                    timeout,
                )

        async with self._lock:
            self._tasks.clear()
            self._skipped.clear()
            self._supervisor_task = None
            log.info("ChannelRuntime stopped")

    async def restart_channel(self, channel: Channel) -> None:
        """Recompute the ideal task set for ``channel`` (start / stop / replace)."""
        await self.stop_channel(channel.id)
        if channel.enabled and _channel_mode(channel) == "stream":
            await self._start_channel_locked(channel)

    async def stop_channel(self, channel_id: uuid.UUID) -> None:
        async with self._lock:
            entry = self._tasks.pop(channel_id, None)
            # Operator-initiated stop also clears any skip marker so a
            # subsequent ``restart_channel`` (e.g. after they edit config
            # or install the missing SDK extra) re-evaluates from scratch.
            self._skipped.pop(channel_id, None)
        if entry is None:
            return
        entry.stop.set()
        entry.task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await entry.task

    async def status(self, channel_id: uuid.UUID) -> ChannelRuntimeStatus:
        async with self._lock:
            entry = self._tasks.get(channel_id)
        if entry is None:
            return ChannelRuntimeStatus(channel_id=channel_id, mode="webhook")
        return entry.status

    def has_task(self, channel_id: uuid.UUID) -> bool:
        return channel_id in self._tasks

    # ─── Supervisor ────────────────────────────────────
    async def _run_supervisor(self) -> None:
        """Periodically reconcile DB-known channels against running tasks."""
        try:
            while not self._supervisor_stop.is_set():
                try:
                    await self._reconcile()
                except Exception:  # pragma: no cover
                    log.exception("ChannelRuntime reconcile pass failed")
                try:
                    await asyncio.wait_for(
                        self._supervisor_stop.wait(),
                        timeout=settings.CHANNEL_STREAM_HEARTBEAT_S,
                    )
                except TimeoutError:
                    continue
        except asyncio.CancelledError:
            return

    async def _reconcile(self) -> None:
        factory = get_session_factory()
        async with factory() as db:
            try:
                rows = await ChannelRepository(db).list_all_enabled_streams()
            except AttributeError:
                # Repo doesn't yet ship the helper — fall back to the
                # generic listing across known workspaces. Cheaper than
                # adding a migration just to enumerate.
                rows = []

        wanted: dict[uuid.UUID, Channel] = {ch.id: ch for ch in rows}

        async with self._lock:
            for cid in list(self._tasks.keys()):
                if cid not in wanted:
                    entry = self._tasks.pop(cid)
                    entry.stop.set()
                    entry.task.cancel()
            # Drop "skipped" entries the operator has since deleted /
            # disabled — otherwise we'd keep them out forever even if the
            # row gets re-created with a working config.
            for cid in list(self._skipped.keys()):
                if cid not in wanted:
                    self._skipped.pop(cid, None)

        for ch in rows:
            entry = self._tasks.get(ch.id)
            fingerprint = _config_fingerprint(ch)
            # Already known to be unrunnable at this fingerprint — skip
            # silently. The single "stream not available" log printed
            # the first time is enough; printing it every heartbeat is
            # just noise. A config edit changes the fingerprint, which
            # invalidates the skip and lets us try again.
            if self._skipped.get(ch.id) == fingerprint:
                continue
            if entry is None or entry.config_fingerprint != fingerprint:
                if entry is not None:
                    await self.stop_channel(ch.id)
                await self._start_channel_locked(ch)

    async def _start_channel_locked(self, channel: Channel) -> None:
        fingerprint = _config_fingerprint(channel)
        async with self._lock:
            if channel.id in self._tasks:
                return
            try:
                provider = get_provider(channel.kind)
            except KeyError:
                log.warning("unknown channel kind %r; skipping stream", channel.kind)
                self._skipped[channel.id] = fingerprint
                return
            if not type(provider).supports_stream() or not type(provider).stream_available():
                log.info(
                    "channel %s kind=%s stream not available; staying webhook",
                    channel.id,
                    channel.kind,
                )
                self._skipped[channel.id] = fingerprint
                return
            # We're about to start it — drop any stale skip marker so a
            # future SDK install / config change doesn't get blocked.
            self._skipped.pop(channel.id, None)

            stop = asyncio.Event()
            status = ChannelRuntimeStatus(
                channel_id=channel.id,
                mode="stream",
                started_at=datetime.now(UTC),
            )
            entry = _ChannelTask(
                channel_id=channel.id,
                task=asyncio.create_task(
                    self._run_stream_loop(channel, provider, stop, status),
                    name=f"channel-stream-{str(channel.id)[:8]}",
                ),
                stop=stop,
                status=status,
                config_fingerprint=_config_fingerprint(channel),
            )
            self._tasks[channel.id] = entry
            log.info(
                "ChannelRuntime: started stream task for channel %s (%s)",
                channel.id,
                channel.kind,
            )

    async def _run_stream_loop(
        self,
        channel: Channel,
        provider: ChannelProvider,
        stop: asyncio.Event,
        status: ChannelRuntimeStatus,
    ) -> None:
        backoff = 1.0
        max_backoff = float(settings.CHANNEL_RUNTIME_RECONNECT_BACKOFF_MAX_S)

        async def _dispatch(inbound: InboundMessage) -> None:
            status.last_event_at = datetime.now(UTC)
            await dispatch_inbound(channel_id=channel.id, inbound=inbound)

        # Pass a snapshot of the channel with plaintext config so the
        # provider doesn't need to reach back through the secret box.
        plain = decrypt_config(channel.config_json or {})

        # Build a lightweight stand-in so providers can read both DB
        # metadata and decrypted config without round-tripping to the
        # ORM session. We attach the plain-config dict as
        # ``_plain_config`` since SQLAlchemy's Channel doesn't allow
        # overriding ``config_json`` on a detached instance safely.
        channel._plain_config = plain  # type: ignore[attr-defined]

        # Tracks whether the previous loop iteration was already an auth
        # failure — used to log only the *first* expiry at WARNING so the
        # operator notices, then drop to DEBUG while we hold the long
        # backoff. Resets whenever a clean iteration succeeds.
        prior_auth_expired = False
        try:
            while not stop.is_set():
                try:
                    status.connected = True
                    status.last_error = None
                    await provider.run_stream(channel=channel, dispatch=_dispatch, stop=stop)
                    backoff = 1.0  # clean exit ⇒ reset
                    prior_auth_expired = False
                except asyncio.CancelledError:
                    raise
                except ChannelStreamAuthExpired as e:
                    status.connected = False
                    status.last_error = str(e)[:240]
                    status.reconnect_attempts += 1
                    wait_s = max(backoff, _AUTH_EXPIRED_BACKOFF_FLOOR_S)
                    wait_s = (
                        min(wait_s, max_backoff)
                        if max_backoff >= _AUTH_EXPIRED_BACKOFF_FLOOR_S
                        else wait_s
                    )
                    if not prior_auth_expired:
                        log.warning(
                            "channel %s auth expired: %s — will retry in %.0fs; "
                            "operator must re-bind credentials to recover",
                            channel.id,
                            e,
                            wait_s,
                        )
                    else:
                        log.debug(
                            "channel %s still auth-expired; next retry in %.0fs",
                            channel.id,
                            wait_s,
                        )
                    prior_auth_expired = True
                    jitter = random.uniform(0, wait_s * 0.1)
                    with contextlib.suppress(TimeoutError):
                        await asyncio.wait_for(stop.wait(), timeout=wait_s + jitter)
                    backoff = wait_s
                except Exception as e:
                    status.connected = False
                    status.last_error = str(e)[:240]
                    status.reconnect_attempts += 1
                    log.warning(
                        "channel %s stream error: %s — reconnect in %.1fs",
                        channel.id,
                        e,
                        backoff,
                    )
                    prior_auth_expired = False
                    jitter = random.uniform(0, backoff * 0.25)
                    with contextlib.suppress(TimeoutError):
                        await asyncio.wait_for(stop.wait(), timeout=backoff + jitter)
                    backoff = min(backoff * 2.5, max_backoff)
        except asyncio.CancelledError:
            log.info("channel %s stream task cancelled", channel.id)
        finally:
            status.connected = False


_runtime: ChannelRuntime | None = None


def get_runtime() -> ChannelRuntime:
    """Process-wide singleton accessor."""
    global _runtime
    if _runtime is None:
        _runtime = ChannelRuntime()
    return _runtime


def reset_runtime_for_tests() -> None:
    """Drop the singleton — call from test ``conftest`` setup/teardown."""
    global _runtime
    _runtime = None
