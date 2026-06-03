"""Channel CRUD endpoints."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Request, status

from app.api.deps import CurrentIdentityId, CurrentWorkspaceId, DBSession
from app.core.errors import Unauthorized
from app.repositories.channel import ChannelRepository
from app.schemas.channel import (
    ChannelBindCodeRead,
    ChannelBindingCreate,
    ChannelBindingRead,
    ChannelCreate,
    ChannelRead,
    ChannelUpdate,
)
from app.services import audit as audit_svc
from app.services import channel as svc
from app.services import channel_binding as binding_svc
from app.services import channel_routing
from app.services import workspace as ws_svc
from app.services.channel import mask_config, seal_config_for_storage
from app.services.channels import describe_providers

router = APIRouter(prefix="/channels", tags=["channels"])


# ─── Provider discovery (public) ─────────────────────────
@router.get("/kinds", summary="List installable channel providers")
async def list_kinds() -> dict:
    """Enumerate every channel provider registered in this deployment.

    Drives the Channel-create form's provider picker and the
    required-config-fields hint list. Public because the set of
    providers isn't sensitive — operators typically surface the same
    info in sales / onboarding docs.

    Shape is stable: ``kind`` + ``display_name`` + ``description`` +
    ``docs_url`` + ``required_config_fields`` +
    ``optional_config_fields`` + ``supports_outbound``. Changing it
    breaks the frontend form in lockstep.
    """
    providers = describe_providers()
    return {"providers": providers, "count": len(providers)}


def _require_workspace(workspace_id: uuid.UUID | None) -> uuid.UUID:
    if workspace_id is None:
        raise Unauthorized("no_active_workspace", code="auth.no_active_workspace")
    return workspace_id


def _present(ch) -> ChannelRead:
    card = ChannelRead.model_validate(ch)
    # Don't leak bot tokens / signing secrets via REST — the inbound_token is
    # meant to be shared with the IM provider so it stays visible.
    card.config_json = mask_config(ch.config_json or {})
    return card


@router.get("", response_model=list[ChannelRead])
async def list_channels(
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
) -> list[ChannelRead]:
    ws_id = _require_workspace(workspace_id)
    await ws_svc.ensure_member_access(db, workspace_id=ws_id, identity_id=identity_id)
    rows = await ChannelRepository(db).list_for_workspace(workspace_id=ws_id)
    return [_present(r) for r in rows]


@router.post("", response_model=ChannelRead, status_code=status.HTTP_201_CREATED)
async def create_channel(
    body: ChannelCreate,
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
    request: Request,
) -> ChannelRead:
    ws_id = _require_workspace(workspace_id)
    await ws_svc.ensure_admin(db, workspace_id=ws_id, identity_id=identity_id)
    ch = await svc.create_channel(
        db,
        workspace_id=ws_id,
        created_by=identity_id,
        name=body.name,
        kind=body.kind,
        config_json=body.config_json,
        default_agent_id=body.default_agent_id,
        default_squad_id=body.default_squad_id,
        enabled=body.enabled,
        metadata_json=body.metadata_json,
        sender_allowlist_json=body.sender_allowlist_json,
        routing_config_json=(
            body.routing_config_json.model_dump(mode="json")
            if body.routing_config_json is not None
            else None
        ),
    )
    await audit_svc.record(
        db,
        action="channel.create",
        actor_identity_id=identity_id,
        workspace_id=ws_id,
        resource_type="channel",
        resource_id=ch.id,
        summary=f"created channel {ch.name!r} ({ch.kind})",
        metadata={"kind": ch.kind},
        request=request,
    )
    await db.commit()
    await svc.notify_runtime_restart(ch)
    return _present(ch)


@router.get("/{channel_id}", response_model=ChannelRead)
async def get_channel(
    channel_id: uuid.UUID,
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
) -> ChannelRead:
    ws_id = _require_workspace(workspace_id)
    await ws_svc.ensure_member_access(db, workspace_id=ws_id, identity_id=identity_id)
    ch = await svc.get_or_404(db, channel_id, workspace_id=ws_id)
    return _present(ch)


@router.patch("/{channel_id}", response_model=ChannelRead)
async def update_channel(
    channel_id: uuid.UUID,
    body: ChannelUpdate,
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
    request: Request,
) -> ChannelRead:
    ws_id = _require_workspace(workspace_id)
    await ws_svc.ensure_admin(db, workspace_id=ws_id, identity_id=identity_id)
    ch = await svc.get_or_404(db, channel_id, workspace_id=ws_id)

    # Merge config_json instead of replacing so editing one key from the UI
    # doesn't wipe out bot_token etc.
    patch = body.model_dump(exclude_none=True, mode="json")
    if "routing_config_json" in patch:
        # Canonicalize the routing blob (stable casing, stringified ids,
        # dropped stray keys) before it lands in the JSONB column.
        patch["routing_config_json"] = channel_routing.normalize_routing_config(
            patch["routing_config_json"]
        )
    if "config_json" in patch:
        merged = dict(ch.config_json or {})
        for k, v in (patch["config_json"] or {}).items():
            # Don't let masked sentinels overwrite the real stored value.
            if isinstance(v, str) and v.startswith("•••"):
                continue
            merged[k] = v
        # Re-seal secret fields after merging — newly supplied plaintext
        # gets envelope-encrypted; existing ``enc:v1:`` values are left
        # alone (encrypt_field is idempotent on already-sealed inputs).
        patch["config_json"] = seal_config_for_storage(merged)

    ch = await svc.update_channel_with_hash(db, channel=ch, patch=patch)
    await audit_svc.record(
        db,
        action="channel.update",
        actor_identity_id=identity_id,
        workspace_id=ws_id,
        resource_type="channel",
        resource_id=ch.id,
        summary=f"updated channel {ch.name!r}",
        metadata={"fields": sorted(patch.keys())},
        request=request,
    )
    await db.commit()
    await svc.notify_runtime_restart(ch)
    return _present(ch)


@router.delete("/{channel_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_channel(
    channel_id: uuid.UUID,
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
    request: Request,
) -> None:
    ws_id = _require_workspace(workspace_id)
    await ws_svc.ensure_admin(db, workspace_id=ws_id, identity_id=identity_id)
    ch = await svc.get_or_404(db, channel_id, workspace_id=ws_id)
    await ChannelRepository(db).soft_delete(ch)
    await audit_svc.record(
        db,
        action="channel.delete",
        actor_identity_id=identity_id,
        workspace_id=ws_id,
        resource_type="channel",
        resource_id=ch.id,
        summary=f"deleted channel {ch.name!r}",
        request=request,
    )
    await db.commit()
    # Use stop_channel rather than restart_channel so a deleted row
    # doesn't get re-spawned by the supervisor's next reconcile pass.
    try:
        from app.core.config import settings as _settings
        from app.services.channel_runtime import get_runtime

        if _settings.CHANNEL_RUNTIME_INPROCESS:
            await get_runtime().stop_channel(channel_id)
    except Exception:  # pragma: no cover
        import logging

        logging.getLogger(__name__).exception("stop_channel for %s after delete failed", channel_id)


@router.get("/{channel_id}/status")
async def channel_status(
    channel_id: uuid.UUID,
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
) -> dict:
    """Stream / connection status for a channel.

    Reads the in-process ChannelRuntime's view of the channel — when the
    runtime isn't running here (multi-worker prod), returns ``mode``
    based on the row only and ``connected=False`` so the UI degrades to
    "we don't know" rather than lying.
    """
    ws_id = _require_workspace(workspace_id)
    await ws_svc.ensure_member_access(db, workspace_id=ws_id, identity_id=identity_id)
    ch = await svc.get_or_404(db, channel_id, workspace_id=ws_id)

    from app.core.config import settings as _settings

    response: dict = {
        "channel_id": str(ch.id),
        "kind": ch.kind,
        "mode": (ch.metadata_json or {}).get("mode"),
        "enabled": ch.enabled,
    }
    if not response["mode"]:
        try:
            from app.services.channels import get_provider as _gp

            response["mode"] = type(_gp(ch.kind)).metadata().default_mode
        except KeyError:
            response["mode"] = "webhook"

    if _settings.CHANNEL_RUNTIME_INPROCESS:
        from app.services.channel_runtime import get_runtime

        st = await get_runtime().status(ch.id)
        response.update(
            {
                "connected": st.connected,
                "last_event_at": (st.last_event_at.isoformat() if st.last_event_at else None),
                "last_error": st.last_error,
                "started_at": (st.started_at.isoformat() if st.started_at else None),
                "reconnect_attempts": st.reconnect_attempts,
            }
        )
    else:
        response.update(
            {
                "connected": False,
                "last_event_at": None,
                "last_error": None,
                "started_at": None,
                "reconnect_attempts": 0,
            }
        )
    return response


@router.post("/{channel_id}/wechat/qr")
async def start_wechat_qr_login(
    channel_id: uuid.UUID,
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
) -> dict:
    """Kick off a WeChat iLink QR-login session.

    Returns ``{qr_id, qrcode_image_data, expires_in}`` so the frontend
    can render the QR (``qrcode_image_data`` is the image URL from
    iLink, pass straight to ``<img src>``) and start polling
    :func:`poll_wechat_qr_login`.
    The actual iLink HTTP calls live in
    :mod:`app.services.channels._wechat_ilink` so this route stays
    purely about auth + DB access.
    """
    ws_id = _require_workspace(workspace_id)
    await ws_svc.ensure_admin(db, workspace_id=ws_id, identity_id=identity_id)
    ch = await svc.get_or_404(db, channel_id, workspace_id=ws_id)
    if ch.kind != "wechat":
        raise svc.Conflict(
            "qr_login_unsupported_kind",
            code="channel.qr_login_unsupported_kind",
        )

    from app.services.channels._wechat_ilink import start_qr_login

    return await start_qr_login(channel=ch)


@router.get("/{channel_id}/wechat/qr/{qr_id}")
async def poll_wechat_qr_login(
    channel_id: uuid.UUID,
    qr_id: str,
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
) -> dict:
    """Polled by the frontend ~every 1.5s while the QR dialog is open."""
    ws_id = _require_workspace(workspace_id)
    await ws_svc.ensure_admin(db, workspace_id=ws_id, identity_id=identity_id)
    ch = await svc.get_or_404(db, channel_id, workspace_id=ws_id)
    if ch.kind != "wechat":
        raise svc.Conflict(
            "qr_login_unsupported_kind",
            code="channel.qr_login_unsupported_kind",
        )

    from app.services.channels._wechat_ilink import poll_qr_login

    result = await poll_qr_login(channel=ch, qr_id=qr_id)
    if result.get("status") == "confirmed":
        bot_token = result.get("bot_token")
        if bot_token:
            merged = dict(ch.config_json or {})
            merged["bot_token"] = bot_token
            # iLink hands back the sharded edge URL for this bot at
            # confirm time; persist it so subsequent long-polls and
            # outbound sends hit the right region instead of the
            # generic ``ilinkai.weixin.qq.com`` entry.
            for key in ("baseurl", "ilink_user_id", "ilink_bot_id"):
                value = result.get(key)
                if value:
                    merged[key] = value
            ch = await svc.update_channel_with_hash(
                db,
                channel=ch,
                patch={"config_json": svc.seal_config_for_storage(merged)},
            )
            # P0 identity binding: the scan binds *this WeChat connector* to
            # the operator who is scanning (the authenticated identity).
            # Routing later resolves inbound senders to this identity so the
            # ``user`` scope can reach their agents across workspaces. We key
            # the link on the bound WeChat account id (``ilink_user_id``).
            external_user_id = str(result.get("ilink_user_id") or "").strip()
            if external_user_id:
                await channel_routing.link_identity(
                    db,
                    channel=ch,
                    external_user_id=external_user_id,
                    identity_id=identity_id,
                    verified_via="qr_scan",
                    created_by=identity_id,
                )
            await db.commit()
            await svc.notify_runtime_restart(ch)
    return result


@router.post(
    "/{channel_id}/bind-codes",
    response_model=ChannelBindCodeRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_bind_code(
    channel_id: uuid.UUID,
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
) -> ChannelBindCodeRead:
    """Mint a one-time ``/bind`` code for the current identity.

    The user types ``/bind <code>`` in the channel chat to link their
    platform account ``(channel, external_user_id)`` to this identity —
    the supplementary path for senders who can't bind via QR scan.
    """
    ws_id = _require_workspace(workspace_id)
    await ws_svc.ensure_member_access(db, workspace_id=ws_id, identity_id=identity_id)
    ch = await svc.get_or_404(db, channel_id, workspace_id=ws_id)
    minted = await channel_routing.mint_bind_code(db, channel=ch, identity_id=identity_id)
    await db.commit()
    return ChannelBindCodeRead(code=minted["code"], ttl_seconds=minted["ttl_seconds"])


@router.get("/{channel_id}/bindings", response_model=list[ChannelBindingRead])
async def list_channel_bindings(
    channel_id: uuid.UUID,
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
) -> list[ChannelBindingRead]:
    """List the layered routing bindings for a channel (P1)."""
    ws_id = _require_workspace(workspace_id)
    await ws_svc.ensure_member_access(db, workspace_id=ws_id, identity_id=identity_id)
    ch = await svc.get_or_404(db, channel_id, workspace_id=ws_id)
    rows = await binding_svc.list_bindings(db, channel_id=ch.id)
    return [ChannelBindingRead.model_validate(r) for r in rows]


@router.post(
    "/{channel_id}/bindings",
    response_model=ChannelBindingRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_channel_binding(
    channel_id: uuid.UUID,
    body: ChannelBindingCreate,
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
    request: Request,
) -> ChannelBindingRead:
    """Add one layered routing binding (most-specific-wins) to a channel."""
    ws_id = _require_workspace(workspace_id)
    await ws_svc.ensure_admin(db, workspace_id=ws_id, identity_id=identity_id)
    ch = await svc.get_or_404(db, channel_id, workspace_id=ws_id)
    binding = await binding_svc.create_binding(
        db,
        workspace_id=ws_id,
        channel_id=ch.id,
        match_scope=body.match_scope,
        match_value=body.match_value,
        bind_scope=body.bind_scope,
        scope_ref_id=body.scope_ref_id,
        target_agent_id=body.target_agent_id,
        allowlist_agent_ids=body.allowlist_agent_ids,
        priority=body.priority,
    )
    await audit_svc.record(
        db,
        action="channel.binding.create",
        actor_identity_id=identity_id,
        workspace_id=ws_id,
        resource_type="channel",
        resource_id=ch.id,
        summary=f"added {body.match_scope} binding to {ch.name!r}",
        metadata={"match_scope": body.match_scope},
        request=request,
    )
    await db.commit()
    return ChannelBindingRead.model_validate(binding)


@router.delete(
    "/{channel_id}/bindings/{binding_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_channel_binding(
    channel_id: uuid.UUID,
    binding_id: uuid.UUID,
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
    request: Request,
) -> None:
    """Remove a layered routing binding from a channel."""
    ws_id = _require_workspace(workspace_id)
    await ws_svc.ensure_admin(db, workspace_id=ws_id, identity_id=identity_id)
    ch = await svc.get_or_404(db, channel_id, workspace_id=ws_id)
    await binding_svc.delete_binding(db, channel_id=ch.id, binding_id=binding_id)
    await audit_svc.record(
        db,
        action="channel.binding.delete",
        actor_identity_id=identity_id,
        workspace_id=ws_id,
        resource_type="channel",
        resource_id=ch.id,
        summary=f"removed binding from {ch.name!r}",
        request=request,
    )
    await db.commit()


@router.delete("/{channel_id}/wechat/session", status_code=status.HTTP_204_NO_CONTENT)
async def logout_wechat_session(
    channel_id: uuid.UUID,
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
) -> None:
    ws_id = _require_workspace(workspace_id)
    await ws_svc.ensure_admin(db, workspace_id=ws_id, identity_id=identity_id)
    ch = await svc.get_or_404(db, channel_id, workspace_id=ws_id)
    merged = dict(ch.config_json or {})
    merged["bot_token"] = ""
    ch = await svc.update_channel_with_hash(
        db,
        channel=ch,
        patch={"config_json": svc.seal_config_for_storage(merged)},
    )
    await db.commit()
    await svc.notify_runtime_restart(ch)


@router.post("/{channel_id}/rotate-token", response_model=ChannelRead)
async def rotate_token(
    channel_id: uuid.UUID,
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
    request: Request,
) -> ChannelRead:
    ws_id = _require_workspace(workspace_id)
    await ws_svc.ensure_admin(db, workspace_id=ws_id, identity_id=identity_id)
    ch = await svc.get_or_404(db, channel_id, workspace_id=ws_id)
    ch = await svc.rotate_token(db, channel=ch)
    await audit_svc.record(
        db,
        action="channel.rotate_token",
        actor_identity_id=identity_id,
        workspace_id=ws_id,
        resource_type="channel",
        resource_id=ch.id,
        summary=f"rotated inbound token for {ch.name!r}",
        request=request,
    )
    await db.commit()
    return _present(ch)
