"""Inbound dispatch — the one path between IM events and an Agent run.

Both the webhook ingress (``app.api.v1.hooks``) and the streaming
ChannelRuntime feed into here. Keeping a single function avoids the
classic "forgot to commit / forgot to ensure session" drift between
two near-identical code paths.

The function:

    1. Reloads the channel row in a fresh DB session (caller is in a
       background task so it must own its own connection).
    2. When the workspace has opted into M3.6 cross-platform routing,
       resolves the inbound to a :class:`LogicalThread` so the same
       logical conversation can span multiple channels. Falls back to
       the legacy per-channel ``ensure_channel_session`` path
       otherwise — that path stays in force for every workspace that
       has not flipped the ``cross_platform_enabled`` flag.
    3. Runs the agent one-shot and posts the reply back through the
       provider's :meth:`send_text` (which falls back to
       :meth:`post_reply` for legacy webhook providers).

Errors are caught + logged; the IM provider's call site stays alive
even when an individual message fails to process.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import uuid
from typing import Any

from app.db.session import get_session_factory
from app.repositories.channel import ChannelRepository
from app.services import agent_runner as runner
from app.services import audit as audit_svc
from app.services import channel_routing
from app.services import logical_thread as logical_thread_svc
from app.services.channels import _presenter, get_provider
from app.services.channels._secret_box import decrypt_config
from app.services.channels._sender_filter import is_known_mode, is_sender_allowed
from app.services.channels.base import ChannelProvider, InboundMessage, OutboundMessage

log = logging.getLogger(__name__)


# Background drain tasks for cancelled processing indicators. The dispatcher
# cancels the indicator the moment the agent reply is ready and immediately
# moves on to ``send_text`` — otherwise the indicator's ``finally`` block
# (which posts a ``status=2`` clear via HTTPS) would synchronously add
# ~1.5–2s of TLS-handshake-plus-round-trip on top of the actual reply.
# We hold strong refs here so the event loop's weak task tracking does not
# GC the drain mid-flight before the cleanup HTTP lands.
_pending_indicator_drains: set[asyncio.Task[None]] = set()


async def _drain_cancelled_indicator(task: asyncio.Task[None]) -> None:
    with contextlib.suppress(BaseException):
        await task


def _drain_indicator_in_background(task: asyncio.Task[None]) -> None:
    """Cancel ``task`` and let its cleanup HTTP run in the background.

    Returns immediately. Caller never awaits the cancellation, so the real
    reply path is not held up by the indicator's ``status=2`` round-trip.
    """
    if task.done():
        return
    task.cancel()
    drain = asyncio.create_task(_drain_cancelled_indicator(task))
    _pending_indicator_drains.add(drain)
    drain.add_done_callback(_pending_indicator_drains.discard)


_DEFAULT_INDICATOR_TEXT_BY_KIND: dict[str, str] = {
    "wechat": "💭 正在思考，稍候…",
    "wecom": "💭 正在思考，稍候…",
}
_INDICATOR_FALLBACK_TEXT = "💭 正在思考，稍候…"


def _resolve_indicator(
    channel_metadata: dict[str, Any] | None,
    channel_kind: str,
) -> str | None:
    """Resolve the typing-indicator text for a channel, or None when disabled.

    The channel row's ``metadata_json.typing_indicator`` accepts three shapes:

        * ``False`` / ``{"enabled": false}`` — disabled, returns ``None``.
        * ``{"text": "..."}`` (enabled implicit) — uses the custom string.
        * absent / any other value — falls back to the kind's default.

    Providers without an override on :meth:`send_processing_indicator`
    just no-op, so this resolver does not need to gate on kind.
    """
    cfg = (channel_metadata or {}).get("typing_indicator")
    if cfg is False:
        return None
    if isinstance(cfg, dict):
        if cfg.get("enabled") is False:
            return None
        custom = cfg.get("text")
        if isinstance(custom, str) and custom.strip():
            return custom.strip()
    return _DEFAULT_INDICATOR_TEXT_BY_KIND.get(channel_kind, _INDICATOR_FALLBACK_TEXT)


async def _safe_send_indicator(
    *,
    provider: ChannelProvider,
    channel_config: dict[str, Any],
    thread_key: str,
    text: str,
) -> None:
    """Fire-and-forget wrapper around ``provider.send_processing_indicator``.

    Indicator is cosmetic — failure here must never propagate to the
    surrounding dispatch path. Errors land as a single warning so an
    operator watching logs can still tell when the indicator stops
    working at scale.
    """
    try:
        await provider.send_processing_indicator(
            channel_config=channel_config,
            thread_key=thread_key,
            text=text,
        )
    except asyncio.CancelledError:
        # Native typing-style indicators run until the dispatcher cancels
        # them once the real reply is ready; treat that path as routine
        # and let the provider's ``finally`` clean up before we exit.
        raise
    except Exception:  # pragma: no cover — best-effort by design
        log.warning("processing indicator send failed", exc_info=True)


def schedule_processing_indicator(
    *,
    channel_kind: str,
    channel_metadata: dict[str, Any] | None,
    channel_config: dict[str, Any],
    thread_key: str,
) -> asyncio.Task[None] | None:
    """Spawn a detached task that nudges the user with a 'thinking' line.

    Returns the task for visibility / tests; callers do not need to
    await it. When the channel has the indicator disabled or the
    provider doesn't override the base no-op, this is a cheap branch
    that still creates a task — the task just resolves immediately.
    """
    text = _resolve_indicator(channel_metadata, channel_kind)
    if text is None:
        return None
    provider = get_provider(channel_kind)
    return asyncio.create_task(
        _safe_send_indicator(
            provider=provider,
            channel_config=channel_config,
            thread_key=thread_key,
            text=text,
        )
    )


async def dispatch_inbound(
    *,
    channel_id: uuid.UUID,
    inbound: InboundMessage,
) -> None:
    """Route an inbound IM message through the standard agent run path.

    Mirrors what :func:`app.api.v1.hooks._run_and_reply` does for
    webhook traffic, except this version owns the session-bind +
    reply-post round-trip so streaming providers can pass it as a
    plain async callable.
    """
    factory = get_session_factory()
    async with factory() as db:
        try:
            ch = await ChannelRepository(db).get(channel_id)
            if ch is None or ch.deleted_at is not None:
                log.warning(
                    "dispatch_inbound: channel %s not found / deleted; dropping",
                    channel_id,
                )
                return
            if not ch.enabled:
                log.info("dispatch_inbound: channel %s disabled; dropping", channel_id)
                return

            # P0 multi-agent routing. ``bind_scope=agent`` (the default for
            # every pre-existing row, ``routing_config_json={}``) short-
            # circuits to the EXACT legacy path below — zero behaviour
            # change. Other scopes (workspace / user) go through the
            # routing core, which owns command handling, the candidate
            # pool, policy gates and reply attribution.
            routing = channel_routing.parse_routing_config(ch.routing_config_json or {})
            if routing.bind_scope != "agent":
                await _dispatch_multi_scope(db, ch=ch, inbound=inbound, routing=routing)
                return

            if ch.default_agent_id is None:
                log.warning(
                    "dispatch_inbound: channel %s has no default_agent; dropping",
                    channel_id,
                )
                return

            sender_id = (inbound.external_user or "anonymous").strip() or "anonymous"
            rules = ch.sender_allowlist_json or {}
            if not is_sender_allowed(rules, sender_id):
                await audit_svc.record(
                    db,
                    action="channel.sender_blocked",
                    actor_identity_id=None,
                    workspace_id=ch.workspace_id,
                    resource_type="channel",
                    resource_id=ch.id,
                    summary=f"sender {sender_id} blocked by allowlist (stream)",
                    metadata={
                        "channel_id": str(ch.id),
                        "external_user_id": sender_id,
                        "mode": rules.get("mode") or "allow_all",
                    },
                )
                try:
                    from app.services import notification_events as notif_events

                    await notif_events.emit_event(
                        db,
                        event_key="channel.sender_blocked",
                        workspace_id=ch.workspace_id,
                        cooldown_resource_id=str(ch.id),
                        payload={
                            "channel_id": str(ch.id),
                            "channel_name": ch.name,
                            "channel_kind": ch.kind,
                            "external_user_id": sender_id,
                            "mode": rules.get("mode") or "allow_all",
                            "ingress": "stream",
                        },
                    )
                except Exception:  # pragma: no cover
                    log.exception(
                        "notify channel.sender_blocked failed for channel=%s",
                        ch.id,
                    )
                await db.commit()
                return
            if not is_known_mode(rules):
                await audit_svc.record(
                    db,
                    action="channel.sender_filter_unknown_mode",
                    actor_identity_id=None,
                    workspace_id=ch.workspace_id,
                    resource_type="channel",
                    resource_id=ch.id,
                    summary="unknown sender_allowlist mode; treating as allow_all",
                    metadata={"channel_id": str(ch.id), "mode": rules.get("mode")},
                )
                await db.commit()

            # M3.6 cross-platform routing. ``find_or_create_thread_for_inbound``
            # returns ``None`` whenever the workspace has not opted in
            # (``cross_platform_enabled=False``) — the legacy
            # per-channel path below stays in force in that case so
            # existing deployments observe zero behaviour change. When
            # opted in, an existing binding wins and resumes the
            # logical thread; otherwise the dispatcher creates a fresh
            # unpaired thread + binding so the user can later finish
            # the 6-digit pairing handshake.
            session_obj = None
            identity_for_run: uuid.UUID | None = None
            thread_resolved = await logical_thread_svc.find_or_create_thread_for_inbound(
                db,
                workspace_id=ch.workspace_id,
                identity_id=None,
                agent_id=ch.default_agent_id,
                channel_id=ch.id,
                external_user_id=sender_id,
                title_hint=f"[{ch.kind}] {inbound.external_user}",
            )
            if thread_resolved is not None:
                thread, session_obj, _is_new = thread_resolved
                identity_for_run = thread.identity_id

            if session_obj is None:
                session_obj = await runner.ensure_channel_session(
                    db,
                    workspace_id=ch.workspace_id,
                    channel_id=ch.id,
                    thread_key=inbound.thread_key,
                    subject_id=ch.default_agent_id,
                    title_hint=f"[{ch.kind}] {inbound.external_user}",
                )
            # ``run_agent_one_shot`` opens its own short-lived session for the
            # inflight-run spine insert. That session is concurrent with this
            # one, so the channel session row must be visible (i.e. committed)
            # before the spine FK can resolve. Without this commit the spine
            # insert fails with a ForeignKeyViolationError on session_id and
            # the run still proceeds (the inflight register is best-effort),
            # but it spams the logs and leaves the run un-trackable.
            await db.commit()

            decrypted_config = decrypt_config(ch.config_json or {})
            indicator_task = schedule_processing_indicator(
                channel_kind=ch.kind,
                channel_metadata=ch.metadata_json,
                channel_config=decrypted_config,
                thread_key=inbound.thread_key,
            )

            try:
                result = await runner.run_agent_one_shot(
                    db,
                    workspace_id=ch.workspace_id,
                    agent_id=ch.default_agent_id,
                    session_id=session_obj.id,
                    identity_id=identity_for_run,
                    user_text=inbound.user_text,
                )
            finally:
                # Stream-style indicators (WeChat typing keepalive) loop
                # until cancelled. Cancel and drain in the background so
                # the indicator's clear signal (``status=2`` HTTPS round-
                # trip, ~1.5–2s of TLS handshake to ``ilinkai``) does not
                # block the real reply. If the message lands before the
                # indicator clear, iLink retires the indicator anyway —
                # the user sees at most a sub-second flicker.
                if indicator_task is not None:
                    _drain_indicator_in_background(indicator_task)
            await db.commit()

            reply = result.final_text or (
                f"⚠ agent run failed: {result.error}" if result.error else ""
            )
            if reply:
                provider = get_provider(ch.kind)
                await provider.send_text(
                    channel_config=decrypted_config,
                    thread_key=inbound.thread_key,
                    text=reply,
                )
        except Exception:  # pragma: no cover
            log.exception("channel_dispatch failure (channel=%s)", channel_id)


async def _dispatch_multi_scope(
    db: Any,
    *,
    ch: Any,
    inbound: InboundMessage,
    routing: channel_routing.RoutingConfig,
) -> None:
    """Dispatch for ``bind_scope in {workspace, user}``.

    Delegates the routing decision to :func:`channel_routing.resolve_route`
    and handles the three outcomes:

    * ``drop`` — sender blocked; routing already wrote the audit row.
    * ``direct`` — command / welcome / switch / policy reply; answered by
      the presenter, never enters an agent.
    * ``run`` — execute the resolved ``(workspace, agent)`` **as the
      resolved identity**, scoped to the target workspace, then present the
      reply (attribution + occasional footer) back through the provider.

    The outbound ``thread_key`` is always the provider's original inbound
    key (WeChat packs ``to_user``/``context_token`` into it); the Session
    is keyed separately per ``(peer, agent)`` in the target workspace so
    each routed agent keeps its own conversation memory.
    """
    decrypted_config = decrypt_config(ch.config_json or {})

    decision = await channel_routing.resolve_route(
        db, channel=ch, inbound=inbound, routing=routing
    )

    if decision.action == "drop":
        await db.commit()
        return

    if decision.action == "direct":
        await db.commit()
        if decision.reply_text:
            provider = get_provider(ch.kind)
            # A menu (welcome / agents list) renders quick-reply buttons on
            # capable channels; everything else is plain text.
            if decision.menu_options:
                menu = _presenter.render_menu(
                    kind=ch.kind,
                    menu_style=decision.menu_style,
                    text=decision.reply_text,
                    options=decision.menu_options,
                )
                message = OutboundMessage(text=menu.text, buttons=menu.buttons)
            else:
                message = OutboundMessage(text=decision.reply_text)
            await _send_out(
                provider,
                channel_config=decrypted_config,
                thread_key=inbound.thread_key,
                message=message,
            )
        return

    # action == "run"
    target_ws = decision.target_workspace_id
    agent_id = decision.target_agent_id
    if target_ws is None or agent_id is None:  # pragma: no cover - defensive
        await db.commit()
        return

    session_key = f"route:{decision.peer_key}:{agent_id}"
    session_obj = await runner.ensure_channel_session(
        db,
        workspace_id=target_ws,
        channel_id=ch.id,
        thread_key=session_key,
        subject_id=agent_id,
        title_hint=f"[{ch.kind}] {decision.peer_key}",
    )
    await db.commit()

    indicator_task = schedule_processing_indicator(
        channel_kind=ch.kind,
        channel_metadata=ch.metadata_json,
        channel_config=decrypted_config,
        thread_key=inbound.thread_key,
    )
    try:
        result = await runner.run_agent_one_shot(
            db,
            workspace_id=target_ws,
            agent_id=agent_id,
            session_id=session_obj.id,
            identity_id=decision.identity_id,
            user_text=decision.user_text or inbound.user_text,
        )
    finally:
        if indicator_task is not None:
            _drain_indicator_in_background(indicator_task)
    await db.commit()

    raw_reply = result.final_text or (
        f"⚠ agent run failed: {result.error}" if result.error else ""
    )
    if raw_reply:
        presented = _presenter.render_reply(
            kind=ch.kind,
            text=raw_reply,
            agent_name=decision.agent_name or "",
            team_name=decision.team_name,
            attribution=decision.attribution,
            lang=decision.lang,
            # Footer is the "occasional" nudge — shown right after a switch
            # rather than on every reply, so we don't spam the chat.
            show_footer=decision.switched,
        )
        provider = get_provider(ch.kind)
        await _send_out(
            provider,
            channel_config=decrypted_config,
            thread_key=inbound.thread_key,
            message=OutboundMessage(text=presented.text, identity=presented.identity),
        )


async def _send_out(
    provider: Any,
    *,
    channel_config: dict[str, Any],
    thread_key: str,
    message: OutboundMessage,
) -> None:
    """Send a presenter-rendered message, preferring the rich path.

    A plain message (no buttons / identity) always goes out via
    ``send_text`` — back-compat for every provider and the lightweight
    test stubs. Rich messages use ``send_message`` only when the provider
    actually implements it; the base ``ChannelProvider.send_message``
    falls back to ``send_text`` so real providers degrade gracefully too.
    """
    is_rich = bool(message.buttons or message.identity)
    if is_rich and hasattr(provider, "send_message"):
        await provider.send_message(
            channel_config=channel_config,
            thread_key=thread_key,
            message=message,
        )
    else:
        await provider.send_text(
            channel_config=channel_config,
            thread_key=thread_key,
            text=message.text,
        )
