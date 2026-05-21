"""Cross-platform logical thread API (M3.6).

All routes are identity-scoped: the caller can only read or mutate
threads keyed on their own ``identity_id``. Cross-tenant isolation is
provided by the workspace header; the service layer additionally
filters on both ``workspace_id`` and ``identity_id`` so a stolen
workspace header cannot read another user's threads.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request

from app.api.deps import CurrentIdentityId, CurrentWorkspaceId, DBSession
from app.core.errors import Unauthorized
from app.core.rate_limit import rate_limit
from app.db.models.channel import Channel
from app.repositories.channel import ChannelRepository
from app.schemas.logical_thread import (
    LogicalThreadDetail,
    LogicalThreadList,
    LogicalThreadRead,
    PairingConsumeRequest,
    PairingConsumeResponse,
    PairingInitiateRequest,
    PairingInitiateResponse,
    ThreadActiveSession,
    ThreadChannelBindingRead,
    ThreadLabelUpdate,
)
from app.services import logical_thread as logical_thread_svc
from app.services import workspace as ws_svc

router = APIRouter(prefix="/threads", tags=["threads"])


_READ = Depends(rate_limit("threads_list", 60, 60))
_RELABEL = Depends(rate_limit("threads_relabel", 10, 60))
_PAIR_INIT = Depends(rate_limit("threads_pair_init", 5, 300))
_PAIR_CONSUME = Depends(rate_limit("threads_pair_consume", 10, 60))
_BINDING_DELETE = Depends(rate_limit("threads_binding_delete", 10, 60))


def _require_workspace(workspace_id: uuid.UUID | None) -> uuid.UUID:
    if workspace_id is None:
        raise Unauthorized("no_active_workspace", code="auth.no_active_workspace")
    return workspace_id


async def _bindings_with_channel(
    db, *, workspace_id: uuid.UUID, thread_id: uuid.UUID
) -> list[ThreadChannelBindingRead]:
    """Hydrate binding rows with the channel display fields the UI needs."""
    bindings = await logical_thread_svc.get_bindings_for_thread(
        db, workspace_id=workspace_id, thread_id=thread_id
    )
    if not bindings:
        return []
    channel_ids = sorted(
        {b.channel_id for b in bindings if b.channel_id is not None},
        key=str,
    )
    channel_map: dict[uuid.UUID, Channel] = {}
    if channel_ids:
        repo = ChannelRepository(db)
        for ch_id in channel_ids:
            ch = await repo.get(ch_id)
            if ch is not None:
                channel_map[ch_id] = ch
    out: list[ThreadChannelBindingRead] = []
    for b in bindings:
        ch = channel_map.get(b.channel_id) if b.channel_id else None
        out.append(
            ThreadChannelBindingRead(
                id=b.id,
                thread_id=b.thread_id,
                channel_id=b.channel_id,
                channel_name=ch.name if ch else None,
                channel_kind=ch.kind if ch else None,
                external_user_id=b.external_user_id,
                last_seen_at=b.last_seen_at,
                is_paired=b.is_paired,
            )
        )
    return out


@router.get("", response_model=LogicalThreadList, dependencies=[_READ])
async def list_threads(
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> LogicalThreadList:
    ws_id = _require_workspace(workspace_id)
    await ws_svc.ensure_member_access(db, workspace_id=ws_id, identity_id=identity_id)
    items, total = await logical_thread_svc.list_threads_for_identity(
        db,
        workspace_id=ws_id,
        identity_id=identity_id,
        limit=limit,
        offset=offset,
    )
    return LogicalThreadList(
        items=[LogicalThreadRead.model_validate(t) for t in items],
        total=total,
    )


@router.get("/{thread_id}", response_model=LogicalThreadDetail, dependencies=[_READ])
async def get_thread(
    thread_id: uuid.UUID,
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
) -> LogicalThreadDetail:
    ws_id = _require_workspace(workspace_id)
    await ws_svc.ensure_member_access(db, workspace_id=ws_id, identity_id=identity_id)
    thread = await logical_thread_svc.get_thread(
        db, workspace_id=ws_id, identity_id=identity_id, thread_id=thread_id
    )
    bindings = await _bindings_with_channel(
        db, workspace_id=ws_id, thread_id=thread.id
    )
    return LogicalThreadDetail(
        **LogicalThreadRead.model_validate(thread).model_dump(),
        bindings=bindings,
    )


@router.get(
    "/{thread_id}/sessions/active",
    response_model=ThreadActiveSession,
    dependencies=[_READ],
)
async def get_thread_active_session(
    thread_id: uuid.UUID,
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
) -> ThreadActiveSession:
    ws_id = _require_workspace(workspace_id)
    await ws_svc.ensure_member_access(db, workspace_id=ws_id, identity_id=identity_id)
    thread = await logical_thread_svc.get_thread(
        db, workspace_id=ws_id, identity_id=identity_id, thread_id=thread_id
    )
    return ThreadActiveSession(
        thread_id=thread.id,
        session_id=thread.primary_session_id,
        last_activity_at=thread.last_activity_at,
    )


@router.post(
    "/{thread_id}/label",
    response_model=LogicalThreadRead,
    dependencies=[_RELABEL],
)
async def relabel_thread(
    thread_id: uuid.UUID,
    payload: ThreadLabelUpdate,
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
) -> LogicalThreadRead:
    ws_id = _require_workspace(workspace_id)
    await ws_svc.ensure_member_access(db, workspace_id=ws_id, identity_id=identity_id)
    thread = await logical_thread_svc.relabel_thread(
        db,
        workspace_id=ws_id,
        identity_id=identity_id,
        thread_id=thread_id,
        label=payload.label,
    )
    await db.commit()
    return LogicalThreadRead.model_validate(thread)


@router.post(
    "/pair/initiate",
    response_model=PairingInitiateResponse,
    dependencies=[_PAIR_INIT],
)
async def pair_initiate(
    payload: PairingInitiateRequest,
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
    request: Request,
) -> PairingInitiateResponse:
    _ = request  # forward-compat for audit IP/UA on the service layer
    ws_id = _require_workspace(workspace_id)
    await ws_svc.ensure_member_access(db, workspace_id=ws_id, identity_id=identity_id)
    out = await logical_thread_svc.initiate_pairing(
        db,
        workspace_id=ws_id,
        identity_id=identity_id,
        source_channel_id=payload.source_channel_id,
        source_external_user_id=payload.source_external_user_id,
        target_channel_id=payload.target_channel_id,
        target_external_user_id=payload.target_external_user_id,
    )
    await db.commit()
    return PairingInitiateResponse(
        code=out["code"],
        expires_at=out["expires_at"],
        ttl_seconds=out["ttl_seconds"],
    )


@router.post(
    "/pair/consume",
    response_model=PairingConsumeResponse,
    dependencies=[_PAIR_CONSUME],
)
async def pair_consume(
    payload: PairingConsumeRequest,
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
) -> PairingConsumeResponse:
    ws_id = _require_workspace(workspace_id)
    await ws_svc.ensure_member_access(db, workspace_id=ws_id, identity_id=identity_id)
    out = await logical_thread_svc.consume_pairing_code(
        db,
        workspace_id=ws_id,
        identity_id=identity_id,
        code=payload.code,
        channel_id=payload.channel_id,
        external_user_id=payload.external_user_id,
    )
    await db.commit()
    return PairingConsumeResponse(
        thread_id=out["thread_id"],
        primary_session_id=out["primary_session_id"],
        bindings_paired=out["bindings_paired"],
        threads_merged=out["threads_merged"],
    )


@router.get(
    "/{thread_id}/bindings",
    response_model=list[ThreadChannelBindingRead],
    dependencies=[_READ],
)
async def list_thread_bindings(
    thread_id: uuid.UUID,
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
) -> list[ThreadChannelBindingRead]:
    ws_id = _require_workspace(workspace_id)
    await ws_svc.ensure_member_access(db, workspace_id=ws_id, identity_id=identity_id)
    await logical_thread_svc.get_thread(
        db, workspace_id=ws_id, identity_id=identity_id, thread_id=thread_id
    )
    return await _bindings_with_channel(
        db, workspace_id=ws_id, thread_id=thread_id
    )


@router.delete(
    "/{thread_id}/bindings/{binding_id}",
    status_code=204,
    dependencies=[_BINDING_DELETE],
)
async def delete_thread_binding(
    thread_id: uuid.UUID,
    binding_id: uuid.UUID,
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
) -> None:
    ws_id = _require_workspace(workspace_id)
    await ws_svc.ensure_member_access(db, workspace_id=ws_id, identity_id=identity_id)
    await logical_thread_svc.unbind_channel(
        db,
        workspace_id=ws_id,
        identity_id=identity_id,
        thread_id=thread_id,
        binding_id=binding_id,
    )
    await db.commit()
