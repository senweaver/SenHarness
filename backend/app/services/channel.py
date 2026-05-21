"""Channel CRUD + dispatch service."""

from __future__ import annotations

import secrets
import uuid

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import Conflict, NotFound
from app.db.models.channel import Channel
from app.repositories.channel import ChannelRepository
from app.services.channels._id_hash import compute_external_app_id_hash
from app.services.channels._secret_box import (
    SECRET_FIELDS as _SECRET_FIELDS,
)
from app.services.channels._secret_box import (
    decrypt_config,
    decrypt_field,
    encrypt_config,
)

__all__ = [
    "ChannelConfigConflict",
    "Conflict",
    "NotFound",
    "compute_hash_for_channel",
    "create_channel",
    "get_or_404",
    "mask_config",
    "notify_runtime_restart",
    "rotate_token",
    "seal_config_for_storage",
    "update_channel_with_hash",
]


class ChannelConfigConflict(Conflict):
    """Raised when two channels would share the same external bot/app.

    The ``code`` field is stable so the frontend can render the
    "this bot is already bound somewhere else" copy instead of the
    generic 409 message.
    """

    code = "channel.external_app_already_bound"


def compute_hash_for_channel(*, kind: str, sealed_config: dict) -> str | None:
    """Return the ``external_app_id_hash`` for a channel row.

    Pure helper so the create + update paths share one hash source.
    The input ``sealed_config`` is decrypted in-process (the keyring
    is always reachable when this is called from a request handler);
    we throw away the plaintext immediately after hashing.
    """
    plaintext = decrypt_config(sealed_config or {})
    try:
        return compute_external_app_id_hash(kind, plaintext)
    finally:
        plaintext.clear()


def mask_config(config: dict) -> dict:
    """Mask secret-looking values in the JSON config before sending to UI.

    Sealed (``enc:v1:``-prefixed) values are masked without revealing the
    last 4 characters of the ciphertext — those bytes have no meaning to
    operators. Plaintext legacy values still expose the tail so users can
    visually confirm they pasted the right secret.
    """
    out = {}
    for k, v in (config or {}).items():
        if k in _SECRET_FIELDS and isinstance(v, str) and v:
            if v.startswith("enc:v1:"):
                try:
                    plain = decrypt_field(v)
                except Exception:
                    plain = ""
                tail = plain[-4:] if len(plain) > 8 else ""
                out[k] = f"•••{tail}" if tail else "•••"
            else:
                tail = v[-4:] if len(v) > 8 else ""
                out[k] = f"•••{tail}" if tail else "•••"
        else:
            out[k] = v
    return out


def seal_config_for_storage(config: dict) -> dict:
    """Encrypt secret-looking fields before persisting to ``channels.config_json``.

    Used by both the create and update paths so the DB column never holds
    plaintext for the fields enumerated in :data:`SECRET_FIELDS`.
    """
    return encrypt_config(config)


async def notify_runtime_restart(channel: Channel) -> None:
    """Best-effort poke at the in-process ChannelRuntime after a CRUD op.

    Routes (channels.py) call this once the DB write commits so the
    streaming supervisor sees the new ``enabled`` / mode / config_json
    state on the next tick. We swallow every exception — restart
    failures must not surface as 500s on the user's PATCH.
    """
    try:
        from app.core.config import settings
        from app.services.channel_runtime import get_runtime

        if not settings.CHANNEL_RUNTIME_INPROCESS:
            return
        await get_runtime().restart_channel(channel)
    except Exception:  # pragma: no cover
        import logging

        logging.getLogger(__name__).exception(
            "channel runtime restart for %s failed", channel.id
        )


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
    sender_allowlist_json: dict | None = None,
) -> Channel:
    if default_agent_id is None and default_squad_id is None:
        raise Conflict(
            "no_default_target",
            code="channel.no_default_target",
            extras={"hint": "Set either default_agent_id or default_squad_id."},
        )
    sealed = seal_config_for_storage(config_json)
    external_hash = compute_hash_for_channel(kind=kind, sealed_config=sealed)
    try:
        row = await ChannelRepository(session).create(
            workspace_id=workspace_id,
            created_by=created_by,
            name=name,
            kind=kind,
            inbound_token=_new_inbound_token(),
            config_json=sealed,
            default_agent_id=default_agent_id,
            default_squad_id=default_squad_id,
            enabled=enabled,
            metadata_json=metadata_json,
            sender_allowlist_json=sender_allowlist_json or {},
            external_app_id_hash=external_hash,
        )
        await session.flush()
    except IntegrityError as e:
        await session.rollback()
        if "uq_channel_external_app_per_kind" in str(e.orig):
            raise ChannelConfigConflict(
                "external_app_already_bound",
                code="channel.external_app_already_bound",
                extras={
                    "kind": kind,
                    "hint": (
                        "This bot/app is already bound to another channel. "
                        "Delete the existing channel or choose a different "
                        "bot before re-binding."
                    ),
                },
            ) from e
        raise
    return row


async def update_channel_with_hash(
    session: AsyncSession,
    *,
    channel: Channel,
    patch: dict,
) -> Channel:
    """Repository update wrapper that recomputes ``external_app_id_hash``
    when ``config_json`` (or ``kind``, in the unlikely case it changes)
    is in the patch and translates a partial-unique-index hit into a
    typed :class:`ChannelConfigConflict`.
    """
    if "config_json" in patch or "kind" in patch:
        target_kind = patch.get("kind", channel.kind)
        target_cfg = patch.get("config_json", channel.config_json or {})
        patch["external_app_id_hash"] = compute_hash_for_channel(
            kind=target_kind, sealed_config=target_cfg
        )
    try:
        row = await ChannelRepository(session).update(channel, **patch)
        await session.flush()
    except IntegrityError as e:
        await session.rollback()
        if "uq_channel_external_app_per_kind" in str(e.orig):
            raise ChannelConfigConflict(
                "external_app_already_bound",
                code="channel.external_app_already_bound",
                extras={
                    "kind": channel.kind,
                    "hint": (
                        "This bot/app is already bound to another channel. "
                        "Pick a different bot or remove the conflicting row."
                    ),
                },
            ) from e
        raise
    return row


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
