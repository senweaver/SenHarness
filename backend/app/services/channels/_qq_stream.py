"""QQ Bot stream — thin wrapper over the official ``qq-botpy`` SDK.

Tencent's QQ open platform exposes a WebSocket gateway. ``botpy``
handles auth, heartbeats, and reconnects internally; we feed it our
:class:`InboundDispatch` callable and translate its async events into
our normalized :class:`InboundMessage` shape.

For outbound replies, ``send_text`` uses the held-open client when
available; otherwise it falls back to a stateless REST POST. (See the
provider file for routing.)

**Passive reply tickets**. QQ Open Platform V2 distinguishes between
*active* and *passive* messages: a reply that does NOT carry the
originating ``msg_id`` is treated as active and goes through audit /
billing (in dev it's silently dropped). To stay on the free passive
path we cache each inbound's ``msg_id`` keyed by ``thread_key`` and
re-attach it in :func:`send_text`.

Tencent allows up to 5 passive replies per inbound and the ticket
expires after ~5 minutes. We track both: ``msg_seq`` is incremented
per reuse, and stale entries are discarded.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from typing import TYPE_CHECKING, Any

from app.services.channels.base import InboundDispatch, InboundMessage

if TYPE_CHECKING:
    from app.db.models.channel import Channel

log = logging.getLogger(__name__)


_ACTIVE_CLIENTS: dict[str, Any] = {}

_PASSIVE_TICKET_TTL_S = 4 * 60
_PASSIVE_TICKET_MAX_USES = 5


class _PassiveTicket:
    __slots__ = ("msg_id", "expires_at", "seq")

    def __init__(self, msg_id: str) -> None:
        self.msg_id = msg_id
        self.expires_at = time.monotonic() + _PASSIVE_TICKET_TTL_S
        self.seq = 0

    def consume(self) -> tuple[str, int] | None:
        if time.monotonic() > self.expires_at:
            return None
        if self.seq >= _PASSIVE_TICKET_MAX_USES:
            return None
        self.seq += 1
        return self.msg_id, self.seq


_PASSIVE_TICKETS: dict[str, _PassiveTicket] = {}


def _remember_inbound_ticket(thread_key: str, msg_id: str | None) -> None:
    if not msg_id:
        return
    _PASSIVE_TICKETS[thread_key] = _PassiveTicket(msg_id)
    if len(_PASSIVE_TICKETS) > 1024:
        now = time.monotonic()
        stale = [k for k, v in _PASSIVE_TICKETS.items() if v.expires_at < now]
        for k in stale:
            _PASSIVE_TICKETS.pop(k, None)


async def run_botpy_stream(
    *,
    channel: Channel,
    dispatch: InboundDispatch,
    stop: asyncio.Event,
) -> None:
    """Open a qq-botpy gateway connection and pump messages."""
    try:
        import botpy
        from botpy.message import C2CMessage, GroupMessage, Message  # noqa: F401
    except ImportError as e:  # pragma: no cover - SDK absent path
        raise RuntimeError(
            "qq-botpy extra missing; install with "
            "'pip install \".[channels-stream]\"'"
        ) from e

    plain = getattr(channel, "_plain_config", None) or (channel.config_json or {})
    app_id = str(plain.get("app_id") or "").strip()
    app_secret = str(plain.get("app_secret") or "").strip()
    sandbox = bool(plain.get("sandbox") or False)
    if not (app_id and app_secret):
        log.info("qq channel %s missing app_id/app_secret — idle", channel.id)
        await stop.wait()
        return

    intents = botpy.Intents(
        public_messages=True,
        direct_message=True,
        public_guild_messages=True,
    )

    class _Bot(botpy.Client):
        async def on_at_message_create(self, message):  # type: ignore[no-untyped-def]
            await _forward(message, kind="AT", channel=channel, dispatch=dispatch)

        async def on_group_at_message_create(self, message):  # type: ignore[no-untyped-def]
            await _forward(
                message, kind="GROUP_AT", channel=channel, dispatch=dispatch
            )

        async def on_c2c_message_create(self, message):  # type: ignore[no-untyped-def]
            await _forward(message, kind="C2C", channel=channel, dispatch=dispatch)

    client = _Bot(intents=intents, is_sandbox=sandbox)
    _ACTIVE_CLIENTS[str(channel.id)] = client

    run_task = asyncio.create_task(
        client.start(appid=app_id, secret=app_secret),
        name=f"qq-botpy-{str(channel.id)[:8]}",
    )
    try:
        await stop.wait()
    finally:
        try:
            await client.close()
        except Exception:  # pragma: no cover
            log.exception("qq botpy client.close failed")
        run_task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await run_task
        _ACTIVE_CLIENTS.pop(str(channel.id), None)


async def _forward(
    message: Any,
    *,
    kind: str,
    channel: Channel,
    dispatch: InboundDispatch,
) -> None:
    text = (getattr(message, "content", None) or "").strip()
    if not text:
        return
    if kind == "GROUP_AT":
        thread = (
            f"qq_group:{getattr(message, 'group_openid', None) or 'unknown'}"
        )
    elif kind == "C2C":
        author = getattr(message, "author", None)
        thread = (
            f"qq_c2c:"
            f"{getattr(author, 'user_openid', None) if author else 'unknown'}"
        )
    else:
        thread = (
            f"qq_guild:{getattr(message, 'channel_id', None) or 'unknown'}"
        )
    msg_id = getattr(message, "id", None)
    _remember_inbound_ticket(thread, msg_id)
    inbound = InboundMessage(
        thread_key=thread,
        user_text=text,
        external_user=str(getattr(message, "author", "qq_user")),
        raw={"kind": kind, "id": msg_id},
    )
    await dispatch(inbound)


async def send_text(
    *,
    channel_config: dict[str, Any],
    thread_key: str,
    text: str,
) -> None:
    """Outbound reply through the live botpy client when one's running.

    For group / C2C threads we attach the cached inbound ``msg_id`` so
    Tencent treats the reply as a *passive* message (free, no audit).
    Without that the platform silently drops the reply during dev.
    """
    try:
        prefix, target = thread_key.split(":", 1)
    except ValueError:
        log.warning("qq send_text: malformed thread_key %r", thread_key)
        return

    client = next(iter(_ACTIVE_CLIENTS.values()), None)
    if client is None:
        log.info("qq send_text: no active botpy client; dropping reply")
        return

    ticket = _PASSIVE_TICKETS.get(thread_key)
    consumed = ticket.consume() if ticket else None
    if consumed is None and prefix in ("qq_group", "qq_c2c"):
        log.warning(
            "qq send_text: no passive ticket for %s; "
            "QQ V2 requires msg_id to deliver group/c2c replies; "
            "dropping",
            thread_key,
        )
        return
    msg_id, msg_seq = consumed if consumed else (None, 0)

    body = text[:1000]
    log.info(
        "qq send_text: thread=%s len=%d msg_id=%s seq=%s",
        thread_key,
        len(body),
        msg_id,
        msg_seq,
    )
    try:
        if prefix == "qq_group":
            resp = await client.api.post_group_message(
                group_openid=target,
                msg_type=0,
                content=body,
                msg_id=msg_id,
                msg_seq=msg_seq,
            )
        elif prefix == "qq_c2c":
            resp = await client.api.post_c2c_message(
                openid=target,
                msg_type=0,
                content=body,
                msg_id=msg_id,
                msg_seq=msg_seq,
            )
        else:
            resp = await client.api.post_message(channel_id=target, content=body)
        log.info("qq send_text ok thread=%s resp=%r", thread_key, resp)
    except Exception:  # pragma: no cover
        log.exception("qq send_text failed thread=%s", thread_key)
