"""Channel layered-binding CRUD service (P1).

Thin business layer over :class:`ChannelBindingRepository`. Routes stay
dumb (parse + authorize + delegate); this owns validation + workspace
scoping. Resolution at dispatch time lives in
:mod:`app.services.channel_routing` (``resolve_binding``).
"""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import Conflict, NotFound
from app.db.models.channel_link import BINDING_MATCH_SCOPES, ChannelBinding
from app.repositories.channel_link import ChannelBindingRepository

__all__ = [
    "create_binding",
    "delete_binding",
    "list_bindings",
]


async def list_bindings(
    session: AsyncSession, *, channel_id: uuid.UUID
) -> list[ChannelBinding]:
    return await ChannelBindingRepository(session).list_for_channel(channel_id=channel_id)


async def create_binding(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    channel_id: uuid.UUID,
    match_scope: str,
    match_value: str | None,
    bind_scope: str | None,
    scope_ref_id: uuid.UUID | None,
    target_agent_id: uuid.UUID | None,
    allowlist_agent_ids: list[uuid.UUID] | None,
    priority: int,
) -> ChannelBinding:
    if match_scope not in BINDING_MATCH_SCOPES:
        raise Conflict(
            "invalid_match_scope",
            code="channel.binding.invalid_match_scope",
            extras={"allowed": list(BINDING_MATCH_SCOPES)},
        )
    # ``channel_default`` is the fallback rung — it never carries a value;
    # every other scope must name what it matches.
    value = None if match_scope == "channel_default" else (match_value or "").strip()
    if match_scope != "channel_default" and not value:
        raise Conflict(
            "match_value_required",
            code="channel.binding.match_value_required",
            extras={"match_scope": match_scope},
        )
    repo = ChannelBindingRepository(session)
    return await repo.create(
        workspace_id=workspace_id,
        channel_id=channel_id,
        match_scope=match_scope,
        match_value=value,
        bind_scope=bind_scope,
        scope_ref_id=scope_ref_id,
        target_agent_id=target_agent_id,
        allowlist_agent_ids_json=([str(a) for a in allowlist_agent_ids] if allowlist_agent_ids else None),
        priority=priority,
    )


async def delete_binding(
    session: AsyncSession, *, channel_id: uuid.UUID, binding_id: uuid.UUID
) -> None:
    repo = ChannelBindingRepository(session)
    binding = await repo.get_for_channel(channel_id=channel_id, binding_id=binding_id)
    if binding is None:
        raise NotFound("binding_not_found", code="channel.binding.not_found")
    await repo.soft_delete(binding)
