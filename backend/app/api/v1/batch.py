"""Batch replay API — ``/api/v1/batch/runs`` + session checkpoint routes.

Two endpoints families in one router:

* ``/api/v1/sessions/{session_id}/checkpoints`` — list / create + fork
* ``/api/v1/batch/runs`` — CRUD + start the replay + fetch per-case results

``execute_batch`` is kicked off as a background asyncio task so the HTTP
response returns immediately with ``status=running``; callers poll
``GET /batch/runs/{id}`` for progress.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any

from fastapi import APIRouter, Request, status

from app.api.deps import CurrentIdentityId, CurrentWorkspaceId, DBSession
from app.core.errors import Unauthorized
from app.repositories.batch import (
    BatchRunCaseRepository,
    BatchRunRepository,
    SessionCheckpointRepository,
)
from app.schemas.batch import (
    BatchRunCaseRead,
    BatchRunCreate,
    BatchRunDetail,
    BatchRunRead,
    SessionCheckpointCreate,
    SessionCheckpointRead,
    SessionForkIn,
    SessionForkOut,
)
from app.services import audit as audit_svc
from app.services import batch as batch_svc
from app.services import session as sess_svc
from app.services import workspace as ws_svc

log = logging.getLogger(__name__)

router = APIRouter(tags=["batch"])

_BG_TASKS: set[Any] = set()


def _require_workspace(workspace_id: uuid.UUID | None) -> uuid.UUID:
    if workspace_id is None:
        raise Unauthorized("no_active_workspace", code="auth.no_active_workspace")
    return workspace_id


# ─── Session checkpoints ─────────────────────────────────
@router.get(
    "/sessions/{session_id}/checkpoints",
    response_model=list[SessionCheckpointRead],
)
async def list_session_checkpoints(
    session_id: uuid.UUID,
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
) -> list[SessionCheckpointRead]:
    ws_id = _require_workspace(workspace_id)
    await ws_svc.ensure_member_access(
        db, workspace_id=ws_id, identity_id=identity_id
    )
    await sess_svc.get_session_or_404(db, session_id, workspace_id=ws_id)
    rows = await SessionCheckpointRepository(db).list_for_session(
        session_id=session_id
    )
    return [SessionCheckpointRead.model_validate(r) for r in rows]


@router.post(
    "/sessions/{session_id}/checkpoints",
    response_model=SessionCheckpointRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_session_checkpoint(
    session_id: uuid.UUID,
    body: SessionCheckpointCreate,
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
    request: Request,
) -> SessionCheckpointRead:
    ws_id = _require_workspace(workspace_id)
    await ws_svc.ensure_member_access(
        db, workspace_id=ws_id, identity_id=identity_id
    )
    session_obj = await sess_svc.get_session_or_404(
        db, session_id, workspace_id=ws_id
    )
    row = await batch_svc.capture_checkpoint(
        db,
        workspace_id=ws_id,
        session_obj=session_obj,
        label=body.label,
        description=body.description,
        created_by=identity_id,
    )
    await audit_svc.record(
        db,
        action="session.checkpoint",
        actor_identity_id=identity_id,
        workspace_id=ws_id,
        resource_type="session",
        resource_id=session_id,
        summary=f"captured checkpoint {row.label!r} at msg {row.message_count}",
        request=request,
    )
    await db.commit()
    return SessionCheckpointRead.model_validate(row)


@router.delete(
    "/sessions/{session_id}/checkpoints/{checkpoint_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_session_checkpoint(
    session_id: uuid.UUID,
    checkpoint_id: uuid.UUID,
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
) -> None:
    ws_id = _require_workspace(workspace_id)
    await ws_svc.ensure_member_access(
        db, workspace_id=ws_id, identity_id=identity_id
    )
    await sess_svc.get_session_or_404(db, session_id, workspace_id=ws_id)
    repo = SessionCheckpointRepository(db)
    row = await repo.get(checkpoint_id)
    if row is None or row.workspace_id != ws_id or row.session_id != session_id:
        return
    await repo.hard_delete(row)
    await db.commit()


@router.post(
    "/sessions/{session_id}/fork",
    response_model=SessionForkOut,
)
async def fork_session(
    session_id: uuid.UUID,
    body: SessionForkIn,
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
    request: Request,
) -> SessionForkOut:
    ws_id = _require_workspace(workspace_id)
    await ws_svc.ensure_member_access(
        db, workspace_id=ws_id, identity_id=identity_id
    )
    await sess_svc.get_session_or_404(db, session_id, workspace_id=ws_id)
    original, fork, copied = await batch_svc.fork_at_checkpoint(
        db,
        workspace_id=ws_id,
        checkpoint_id=body.checkpoint_id,
        created_by=identity_id,
        title_override=body.title,
    )
    if original.id != session_id:
        # The checkpoint's session_id must match the URL's session_id so
        # forking is "about" the session the caller is looking at.
        raise Unauthorized(
            "checkpoint_session_mismatch",
            code="checkpoint.session_mismatch",
        )
    await audit_svc.record(
        db,
        action="session.fork",
        actor_identity_id=identity_id,
        workspace_id=ws_id,
        resource_type="session",
        resource_id=fork.id,
        summary=f"forked {original.id} → {fork.id} ({copied} msgs)",
        metadata={
            "original_session_id": str(original.id),
            "checkpoint_id": str(body.checkpoint_id),
            "copied_message_count": copied,
        },
        request=request,
    )
    await db.commit()
    return SessionForkOut(
        original_session_id=original.id,
        fork_session_id=fork.id,
        copied_message_count=copied,
    )


# ─── Batch runs ──────────────────────────────────────────
@router.get("/batch/runs", response_model=list[BatchRunRead])
async def list_batch_runs(
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
) -> list[BatchRunRead]:
    ws_id = _require_workspace(workspace_id)
    await ws_svc.ensure_member_access(
        db, workspace_id=ws_id, identity_id=identity_id
    )
    rows = await BatchRunRepository(db).list_for_workspace(workspace_id=ws_id)
    return [BatchRunRead.model_validate(r) for r in rows]


@router.post(
    "/batch/runs",
    response_model=BatchRunRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_batch_run(
    body: BatchRunCreate,
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
    request: Request,
) -> BatchRunRead:
    ws_id = _require_workspace(workspace_id)
    await ws_svc.ensure_member_access(
        db, workspace_id=ws_id, identity_id=identity_id
    )
    batch = await batch_svc.create_batch_run(
        db,
        workspace_id=ws_id,
        created_by=identity_id,
        name=body.name,
        description=body.description,
        agent_id=body.agent_id,
        cases_raw=[c.model_dump() for c in body.cases],
        config_json=body.config_json,
    )
    await audit_svc.record(
        db,
        action="batch.create",
        actor_identity_id=identity_id,
        workspace_id=ws_id,
        resource_type="batch_run",
        resource_id=batch.id,
        summary=f"created batch run {batch.name!r} with {len(body.cases)} cases",
        metadata={"agent_id": str(body.agent_id), "case_count": len(body.cases)},
        request=request,
    )
    await db.commit()

    # Kick off execution in the background. We stash a strong ref in _BG_TASKS
    # so the GC doesn't eat it mid-flight; the set is cleaned up when the
    # task resolves.
    task = asyncio.create_task(batch_svc.execute_batch(batch.id))
    _BG_TASKS.add(task)
    task.add_done_callback(_BG_TASKS.discard)

    return BatchRunRead.model_validate(batch)


@router.get("/batch/runs/{batch_run_id}", response_model=BatchRunDetail)
async def get_batch_run(
    batch_run_id: uuid.UUID,
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
) -> BatchRunDetail:
    ws_id = _require_workspace(workspace_id)
    await ws_svc.ensure_member_access(
        db, workspace_id=ws_id, identity_id=identity_id
    )
    batch = await BatchRunRepository(db).get(batch_run_id)
    if batch is None or batch.workspace_id != ws_id:
        raise Unauthorized(
            "batch_run_not_found", code="batch.not_found"
        )
    cases = await BatchRunCaseRepository(db).list_for_run(
        batch_run_id=batch_run_id
    )
    detail = BatchRunDetail.model_validate(batch)
    detail.cases = [BatchRunCaseRead.model_validate(c) for c in cases]
    return detail


@router.get(
    "/batch/runs/{batch_run_id}/cases",
    response_model=list[BatchRunCaseRead],
)
async def list_batch_cases(
    batch_run_id: uuid.UUID,
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
) -> list[BatchRunCaseRead]:
    ws_id = _require_workspace(workspace_id)
    await ws_svc.ensure_member_access(
        db, workspace_id=ws_id, identity_id=identity_id
    )
    batch = await BatchRunRepository(db).get(batch_run_id)
    if batch is None or batch.workspace_id != ws_id:
        raise Unauthorized(
            "batch_run_not_found", code="batch.not_found"
        )
    cases = await BatchRunCaseRepository(db).list_for_run(
        batch_run_id=batch_run_id
    )
    return [BatchRunCaseRead.model_validate(c) for c in cases]
