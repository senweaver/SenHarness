"""Channel CRUD + dispatch service."""

from __future__ import annotations

import secrets
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import Conflict, NotFound
from app.db.models.channel import Channel
from app.repositories.channel import ChannelRepository

_SECRET_FIELDS = {
    "bot_token",
    "signing_secret",
    "sign_secret",
    "app_secret",
    "client_secret",
    "public_key",
    "verification_token",
    "secret_token",
    "secret",
    "token",
    "encoding_aes_key",
    "webhook_url",
    "incoming_webhook_url",
}


def mask_config(config: dict) -> dict:
    """Mask secret-looking values in the JSON config before sending to UI."""
    out = {}
    for k, v in (config or {}).items():
        if k in _SECRET_FIELDS and isinstance(v, str) and v:
            tail = v[-4:] if len(v) > 8 else ""
            out[k] = f"•••{tail}" if tail else "•••"
        else:
            out[k] = v
    return out


async def create_channel(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    created_by: uuid.UUID | None,
    name: str,
    kind: str,
    config_json: dict,
    default_agent_id: uuid.UUID | None,
    default_squad_id: uuid.UUID | None,
    enabled: bool,
    metadata_json: dict,
) -> Channel:
    if default_agent_id is None and default_squad_id is None:
        raise Conflict(
            "no_default_target",
            code="channel.no_default_target",
            extras={"hint": "Set either default_agent_id or default_squad_id."},
        )
    return await ChannelRepository(session).create(
        workspace_id=workspace_id,
        created_by=created_by,
        name=name,
        kind=kind,
        inbound_token=_new_inbound_token(),
        config_json=config_json,
        default_agent_id=default_agent_id,
        default_squad_id=default_squad_id,
        enabled=enabled,
        metadata_json=metadata_json,
    )


async def get_or_404(
    session: AsyncSession, channel_id: uuid.UUID, *, workspace_id: uuid.UUID
) -> Channel:
    row = await ChannelRepository(session).get(channel_id)
    if row is None or row.workspace_id != workspace_id:
        raise NotFound("channel_not_found", code="channel.not_found")
    return row


async def rotate_token(
    session: AsyncSession, *, channel: Channel
) -> Channel:
    return await ChannelRepository(session).update(
        channel, inbound_token=_new_inbound_token()
    )


def _new_inbound_token() -> str:
    return secrets.token_urlsafe(32)
