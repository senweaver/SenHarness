"""Public webhook ingress — Channels (IM) + Flows trigger endpoints.

These routes **do not** require a bearer token; authentication is via the
per-channel / per-flow ``?token=`` shared secret, which admins generate and
paste into the IM provider config.
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status

from app.api.deps import DBSession
from app.api.helpers import INGRESS_TOKEN_HEADER, resolve_ingress_token
from app.core.errors import RateLimited
from app.core.rate_limit import Quota, check_rate_limit, rate_limit
from app.db.models.flow import FlowTriggerKind
from app.db.models.message import MessageRole
from app.db.session import get_session_factory
from app.repositories.channel import ChannelRepository
from app.repositories.flow import FlowRepository, FlowRunRepository
from app.schemas.channel import ChannelIngressAck
from app.schemas.flow import FlowRunRead
from app.services import agent_runner as runner
from app.services import audit as audit_svc
from app.services import flow as flow_svc
from app.services import session as sess_svc
from app.services.channel_dispatch import schedule_processing_indicator
from app.services.channels import get_provider
from app.services.channels._secret_box import decrypt_config
from app.services.channels._sender_filter import is_known_mode, is_sender_allowed
from app.services.channels.base import SignatureInvalid
from app.services.channels.slack import SlackProvider

log = logging.getLogger(__name__)

router = APIRouter(prefix="/hooks", tags=["hooks"])

# Strong refs to detached background tasks so the GC doesn't eat them.
_BACKGROUND_TASKS: set[Any] = set()


# Webhook ingress rate limits are intentionally generous — real IM
# providers (Slack, Feishu, Discord) burst-deliver events during active
# channels. 60/minute per IP is enough headroom for normal traffic while
# still stopping a leaked token from flooding us.
@router.post(
    "/ingress/{channel_id}",
    dependencies=[
        Depends(rate_limit("hook_ingress", limit=60, period_seconds=60))
    ],
)
async def channel_ingress(
    channel_id: uuid.UUID,
    db: DBSession,
    request: Request,
    token: str | None = Query(None, min_length=16, max_length=128),
    x_senharness_token: str | None = Header(
        None, alias=INGRESS_TOKEN_HEADER, min_length=16, max_length=128
    ),
) -> Any:
    """Receive an inbound message from an IM provider.

    Auth: the provider must pass the channel's ``inbound_token`` via the
    ``X-Senharness-Token`` HTTP header. Legacy providers that only support
    query string auth may still pass ``?token=...``; that path works but
    emits a deprecation warning on every request because tokens in URLs
    end up in proxy logs, browser history, and APM samples.

    We look up the channel by id AND token — mismatches return 403 without
    leaking whether the id exists.
    """
    supplied = resolve_ingress_token(x_senharness_token, token)
    ch = await ChannelRepository(db).get(channel_id)
    if ch is None or ch.inbound_token != supplied or ch.deleted_at is not None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="invalid_token")
    if not ch.enabled:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="channel_disabled"
        )

    # Read the raw body ONCE — signature verification needs the exact bytes
    # Slack / Discord signed, and JSON parsers re-serialize whitespace which
    # would break the HMAC.
    raw_body = await request.body()
    try:
        payload = json.loads(raw_body.decode("utf-8") or "{}") if raw_body else {}
    except (json.JSONDecodeError, UnicodeDecodeError):
        payload = {}

    provider = get_provider(ch.kind)

    # Channel config_json may carry envelope-encrypted secret fields
    # (``enc:v1:...``); decrypt once for the duration of this request so
    # the provider sees plaintext bot_tokens / signing secrets.
    plaintext_config = decrypt_config(ch.config_json or {})

    # M0.8 — every signature failure (and the special generic-webhook
    # "secret not configured yet" case) lands as an audit row so admins
    # can see when an external IM platform stops working.
    try:
        provider.verify_signature(
            channel_config=plaintext_config,
            headers=dict(request.headers),
            body=raw_body,
        )
    except SignatureInvalid as e:
        # Generic webhook with verify_signatures defaulting to True
        # but no hmac_secret yet: keep the channel alive (don't auto-
        # disable) but block this request and surface the gap.
        action = (
            "channel.signature_required_but_unset"
            if e.code == "webhook.hmac_secret_unset"
            else "channel.signature_failed"
        )
        await audit_svc.record(
            db,
            action=action,
            actor_identity_id=None,
            workspace_id=ch.workspace_id,
            resource_type="channel",
            resource_id=ch.id,
            summary=f"signature check failed for {ch.kind} channel {ch.name!r}",
            metadata={"channel_id": str(ch.id), "kind": ch.kind, "code": e.code},
            request=request,
        )
        if action == "channel.signature_failed":
            try:
                from app.services import notification_events as notif_events

                await notif_events.emit_event(
                    db,
                    event_key="security.signature_failed",
                    workspace_id=ch.workspace_id,
                    cooldown_resource_id=str(ch.id),
                    payload={
                        "channel_id": str(ch.id),
                        "channel_name": ch.name,
                        "channel_kind": ch.kind,
                        "code": e.code,
                    },
                    request=request,
                )
            except Exception:  # pragma: no cover
                log.exception(
                    "notify security.signature_failed failed for channel=%s",
                    ch.id,
                )
        await db.commit()
        log.warning("channel %s signature invalid: %s", ch.id, e.code)
        raise HTTPException(
            status_code=(
                status.HTTP_401_UNAUTHORIZED
                if action == "channel.signature_required_but_unset"
                else status.HTTP_403_FORBIDDEN
            ),
            detail={"code": e.code, "message": str(e)},
        ) from e

    # Slack ``expected_team_id`` pinning (optional). Sits next to the
    # signature check because it has the same security purpose
    # (refuse a webhook that arrived from the wrong source) but
    # depends on the parsed payload, not the raw bytes.
    if isinstance(provider, SlackProvider):
        try:
            provider.assert_team_id(channel_config=plaintext_config, payload=payload)
        except SignatureInvalid as e:
            await audit_svc.record(
                db,
                action="channel.slack_team_mismatch",
                actor_identity_id=None,
                workspace_id=ch.workspace_id,
                resource_type="channel",
                resource_id=ch.id,
                summary="slack team_id mismatch",
                metadata={
                    "channel_id": str(ch.id),
                    "expected_team_id": plaintext_config.get("expected_team_id"),
                    "actual_team_id": payload.get("team_id"),
                },
                request=request,
            )
            await db.commit()
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"code": e.code, "message": str(e)},
            ) from e

    # Handshake events (Slack url_verification, Feishu challenge, Discord PING).
    hs = provider.handshake_response(payload)
    if hs is not None:
        return hs

    inbound = provider.parse_inbound(payload, dict(request.headers))
    if inbound is None:
        # Silently ack — event was real but not something we need to reply to
        # (e.g. bot echo, message_changed, unknown interaction type).
        return ChannelIngressAck(accepted=False, reason="ignored")

    # M0.8 per-sender / per-channel rate limit. Per-sender keeps one
    # noisy user from saturating the agent's queue; per-channel
    # backstop keeps a sock-puppet army from doing the same with
    # rotated user ids. Both buckets fail-open if Redis is unreachable
    # (matches the global rate-limit posture).
    sender_id = (inbound.external_user or "anonymous").strip() or "anonymous"
    try:
        await check_rate_limit(
            identifier=f"channel:{ch.id}:sender:{sender_id}",
            path="hook_sender",
            quota=Quota(limit=20, period_seconds=60),
        )
    except RateLimited as e:
        await audit_svc.record(
            db,
            action="channel.rate_limited",
            actor_identity_id=None,
            workspace_id=ch.workspace_id,
            resource_type="channel",
            resource_id=ch.id,
            summary="per-sender rate limit hit",
            metadata={
                "channel_id": str(ch.id),
                "sender_id": sender_id,
                "limit_kind": "per_sender",
            },
            request=request,
        )
        await db.commit()
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={"code": "channel.rate_limited", "message": str(e.detail)},
        ) from e

    try:
        await check_rate_limit(
            identifier=f"channel:{ch.id}:total",
            path="hook_channel",
            quota=Quota(limit=200, period_seconds=60),
        )
    except RateLimited as e:
        await audit_svc.record(
            db,
            action="channel.rate_limited",
            actor_identity_id=None,
            workspace_id=ch.workspace_id,
            resource_type="channel",
            resource_id=ch.id,
            summary="per-channel rate limit hit",
            metadata={"channel_id": str(ch.id), "limit_kind": "per_channel"},
            request=request,
        )
        await db.commit()
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={"code": "channel.rate_limited", "message": str(e.detail)},
        ) from e

    # M0.8 sender allowlist gate. ``allow_all`` (default) lets every
    # sender through so existing channels behave like before; admins
    # opt in to ``allow_listed`` / ``deny_listed`` from the UI.
    rules = ch.sender_allowlist_json or {}
    if not is_sender_allowed(rules, sender_id):
        await audit_svc.record(
            db,
            action="channel.sender_blocked",
            actor_identity_id=None,
            workspace_id=ch.workspace_id,
            resource_type="channel",
            resource_id=ch.id,
            summary=f"sender {sender_id} blocked by allowlist",
            metadata={
                "channel_id": str(ch.id),
                "external_user_id": sender_id,
                "mode": rules.get("mode") or "allow_all",
            },
            request=request,
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
                    "ingress": "webhook",
                },
                request=request,
            )
        except Exception:  # pragma: no cover
            log.exception(
                "notify channel.sender_blocked failed for channel=%s", ch.id
            )
        await db.commit()
        return ChannelIngressAck(accepted=False, reason="sender_blocked")
    if not is_known_mode(rules):
        # Fail-open but warn so the operator notices the bad config.
        await audit_svc.record(
            db,
            action="channel.sender_filter_unknown_mode",
            actor_identity_id=None,
            workspace_id=ch.workspace_id,
            resource_type="channel",
            resource_id=ch.id,
            summary="unknown sender_allowlist mode; treating as allow_all",
            metadata={
                "channel_id": str(ch.id),
                "mode": rules.get("mode"),
            },
            request=request,
        )
        await db.commit()

    if ch.default_agent_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="channel_has_no_default_agent (squads routing is P2)",
        )

    session_obj = await runner.ensure_channel_session(
        db,
        workspace_id=ch.workspace_id,
        channel_id=ch.id,
        thread_key=inbound.thread_key,
        subject_id=ch.default_agent_id,
        title_hint=f"[{ch.kind}] {inbound.external_user}",
    )
    await db.commit()  # ensure session is persisted before background run

    # Schedule the agent run asynchronously so the provider gets a fast 200.
    # We spawn a detached task that opens its own DB session.
    import asyncio

    task = asyncio.create_task(
        _run_and_reply(
            channel_id=ch.id,
            session_id=session_obj.id,
            workspace_id=ch.workspace_id,
            agent_id=ch.default_agent_id,
            user_text=inbound.user_text,
            external_user=inbound.external_user,
            thread_key=inbound.thread_key,
        )
    )
    _BACKGROUND_TASKS.add(task)
    task.add_done_callback(_BACKGROUND_TASKS.discard)

    return ChannelIngressAck(
        accepted=True,
        session_id=session_obj.id,
        reason=f"queued for agent {ch.default_agent_id}",
    )


async def _run_and_reply(
    *,
    channel_id: uuid.UUID,
    session_id: uuid.UUID,
    workspace_id: uuid.UUID,
    agent_id: uuid.UUID,
    user_text: str,
    external_user: str,
    thread_key: str,
) -> None:
    """Background task: run the Agent and post the reply back to the IM
    provider. Opens its own short-lived DB session."""
    factory = get_session_factory()
    async with factory() as db:
        try:
            ch = await ChannelRepository(db).get(channel_id)
            if ch is None:
                return
            decrypted_config = decrypt_config(ch.config_json or {})
            schedule_processing_indicator(
                channel_kind=ch.kind,
                channel_metadata=ch.metadata_json,
                channel_config=decrypted_config,
                thread_key=thread_key,
            )
            result = await runner.run_agent_one_shot(
                db,
                workspace_id=workspace_id,
                agent_id=agent_id,
                session_id=session_id,
                identity_id=None,
                user_text=user_text,
            )
            await db.commit()

            reply = result.final_text or (
                f"⚠ agent run failed: {result.error}" if result.error else ""
            )
            if reply:
                provider = get_provider(ch.kind)
                await provider.post_reply(
                    channel_config=decrypted_config,
                    thread_key=thread_key,
                    text=reply,
                )
        except Exception:  # pragma: no cover
            log.exception("channel background run failed")


@router.post(
    "/flow/{flow_id}",
    response_model=FlowRunRead,
    dependencies=[
        Depends(rate_limit("hook_flow", limit=30, period_seconds=60))
    ],
)
async def flow_webhook(
    flow_id: uuid.UUID,
    db: DBSession,
    request: Request,
    token: str | None = Query(None, min_length=8, max_length=128),
    x_senharness_token: str | None = Header(
        None, alias=INGRESS_TOKEN_HEADER, min_length=8, max_length=128
    ),
) -> FlowRunRead:
    """Fire a flow from an external webhook.

    Auth: the ``X-Senharness-Token`` header must match the per-flow shared
    secret (from ``flow.trigger_config.token``). Anyone with the URL can fire
    it, so rotate the token if leaked (PATCH the flow). Query-string token
    is still accepted for legacy callers but logged as deprecated.
    """
    supplied = resolve_ingress_token(x_senharness_token, token)
    flow = await FlowRepository(db).get(flow_id)
    if flow is None or flow.deleted_at is not None or not flow.enabled:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="invalid")
    expected = (flow.trigger_config or {}).get("token")
    if not expected or expected != supplied:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="invalid_token")

    try:
        payload = await request.json()
    except Exception:
        payload = {}

    run_id = await flow_svc.trigger_flow(
        flow.id,
        workspace_id=flow.workspace_id,
        trigger_kind=FlowTriggerKind.WEBHOOK,
        payload=payload if isinstance(payload, dict) else {"payload": payload},
    )
    row = await FlowRunRepository(db).get(run_id)
    return FlowRunRead.model_validate(row)


_ = MessageRole  # silence unused
_ = sess_svc  # silence unused
