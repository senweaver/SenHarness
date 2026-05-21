"""Four-layer memory profile endpoints (V2).

Covers:

- `GET  /memory-profiles/workspace`              — workspace MEMORY.md
- `PUT  /memory-profiles/workspace`              — admin edits workspace MEMORY.md
- `GET  /memory-profiles/me`                     — caller's USER.md + SOUL.md
- `PUT  /memory-profiles/me/profile`             — caller edits own USER.md
- `POST /memory-profiles/me/soul/propose`        — queue a SOUL rewrite
- `POST /memory-profiles/me/soul/{pid}/decide`   — approve/reject a SOUL proposal
- `GET  /memory-profiles/me/soul/pending`        — list pending SOUL proposals
- `GET  /memory-profiles/identities/{id}`        — admin reads someone else's profile bundle
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Request, status

from app.api.deps import CurrentIdentityId, CurrentWorkspaceId, DBSession
from app.core.errors import Unauthorized
from app.db.models.memory_profile import MemoryProfileKind
from app.schemas.memory_profile import (
    MemoryProfileRead,
    MemoryProfileUpsert,
    SoulDecisionIn,
    SoulUpdateProposal,
    SoulUpdateRead,
)
from app.services import audit as audit_svc
from app.services import memory_profile as svc
from app.services import workspace as ws_svc

router = APIRouter(prefix="/memory-profiles", tags=["memory-profiles"])


def _require_workspace(workspace_id: uuid.UUID | None) -> uuid.UUID:
    if workspace_id is None:
        raise Unauthorized("no_active_workspace", code="auth.no_active_workspace")
    return workspace_id


def _pending_to_read(entry: dict) -> SoulUpdateRead:
    def _uuid_or_none(v):
        try:
            return uuid.UUID(v) if v else None
        except (TypeError, ValueError):
            return None

    return SoulUpdateRead(
        id=str(entry.get("id", "")),
        proposed_content=str(entry.get("proposed_content", "")),
        proposed_dims=dict(entry.get("proposed_dims", {}) or {}),
        proposed_at=str(entry.get("proposed_at", "")),
        proposed_by_identity_id=_uuid_or_none(entry.get("proposed_by_identity_id")),
        source_session_id=_uuid_or_none(entry.get("source_session_id")),
        rationale=str(entry.get("rationale", "")),
    )


# ─── Workspace MEMORY.md ──────────────────────────────────
@router.get("/workspace", response_model=MemoryProfileRead | None)
async def get_workspace_memory(
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
) -> MemoryProfileRead | None:
    ws_id = _require_workspace(workspace_id)
    await ws_svc.ensure_member_access(db, workspace_id=ws_id, identity_id=identity_id)
    row = await svc.get_profile(
        db,
        workspace_id=ws_id,
        kind=MemoryProfileKind.WORKSPACE_MEMORY,
        subject_id=ws_id,
    )
    return MemoryProfileRead.model_validate(row) if row else None


@router.put("/workspace", response_model=MemoryProfileRead)
async def put_workspace_memory(
    body: MemoryProfileUpsert,
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
    request: Request,
) -> MemoryProfileRead:
    ws_id = _require_workspace(workspace_id)
    await ws_svc.ensure_admin(db, workspace_id=ws_id, identity_id=identity_id)
    row = await svc.upsert_profile(
        db,
        workspace_id=ws_id,
        kind=MemoryProfileKind.WORKSPACE_MEMORY,
        subject_id=ws_id,
        content_md=body.content_md,
        metadata_json=body.metadata_json,
        updated_by=identity_id,
    )
    await audit_svc.record(
        db,
        action="memory_profile.workspace.update",
        actor_identity_id=identity_id,
        workspace_id=ws_id,
        resource_type="memory_profile",
        resource_id=row.id,
        summary=f"updated workspace MEMORY.md ({row.char_count} chars)",
        request=request,
    )
    await db.commit()
    return MemoryProfileRead.model_validate(row)


# ─── Self: USER.md + SOUL.md ─────────────────────────────
@router.get("/me", response_model=dict)
async def get_my_profiles(
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
) -> dict:
    ws_id = _require_workspace(workspace_id)
    await ws_svc.ensure_member_access(db, workspace_id=ws_id, identity_id=identity_id)
    profile = await svc.get_profile(
        db,
        workspace_id=ws_id,
        kind=MemoryProfileKind.USER_PROFILE,
        subject_id=identity_id,
    )
    soul = await svc.get_profile(
        db,
        workspace_id=ws_id,
        kind=MemoryProfileKind.USER_SOUL,
        subject_id=identity_id,
    )
    return {
        "profile": MemoryProfileRead.model_validate(profile) if profile else None,
        "soul": MemoryProfileRead.model_validate(soul) if soul else None,
    }


@router.put("/me/profile", response_model=MemoryProfileRead)
async def put_my_profile(
    body: MemoryProfileUpsert,
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
    request: Request,
) -> MemoryProfileRead:
    ws_id = _require_workspace(workspace_id)
    await ws_svc.ensure_member_access(db, workspace_id=ws_id, identity_id=identity_id)
    row = await svc.upsert_profile(
        db,
        workspace_id=ws_id,
        kind=MemoryProfileKind.USER_PROFILE,
        subject_id=identity_id,
        content_md=body.content_md,
        metadata_json=body.metadata_json,
        updated_by=identity_id,
    )
    await audit_svc.record(
        db,
        action="memory_profile.user_profile.update",
        actor_identity_id=identity_id,
        workspace_id=ws_id,
        resource_type="memory_profile",
        resource_id=row.id,
        summary=f"updated own USER.md ({row.char_count} chars)",
        request=request,
    )
    await db.commit()
    return MemoryProfileRead.model_validate(row)


# ─── SOUL approval queue ─────────────────────────────────
@router.get("/me/soul/pending", response_model=list[SoulUpdateRead])
async def list_my_soul_pending(
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
) -> list[SoulUpdateRead]:
    ws_id = _require_workspace(workspace_id)
    await ws_svc.ensure_member_access(db, workspace_id=ws_id, identity_id=identity_id)
    soul = await svc.get_profile(
        db,
        workspace_id=ws_id,
        kind=MemoryProfileKind.USER_SOUL,
        subject_id=identity_id,
    )
    if soul is None:
        return []
    return [_pending_to_read(p) for p in (soul.pending_updates_json or [])]


@router.post(
    "/me/soul/propose",
    response_model=SoulUpdateRead,
    status_code=status.HTTP_201_CREATED,
)
async def propose_my_soul_update(
    body: SoulUpdateProposal,
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
    request: Request,
) -> SoulUpdateRead:
    ws_id = _require_workspace(workspace_id)
    await ws_svc.ensure_member_access(db, workspace_id=ws_id, identity_id=identity_id)
    proposal = await svc.propose_soul_update(
        db,
        workspace_id=ws_id,
        identity_id=identity_id,
        proposed_content=body.proposed_content,
        proposed_dims=body.proposed_dims,
        source_session_id=body.source_session_id,
        proposed_by_identity_id=identity_id,
        rationale=body.rationale,
    )
    await audit_svc.record(
        db,
        action="memory_profile.user_soul.propose",
        actor_identity_id=identity_id,
        workspace_id=ws_id,
        resource_type="memory_profile_soul_proposal",
        resource_id=None,
        summary=f"queued SOUL update proposal={proposal.id}",
        metadata={"proposal_id": proposal.id, "rationale": body.rationale[:120]},
        request=request,
    )
    await db.commit()
    return SoulUpdateRead(
        id=proposal.id,
        proposed_content=proposal.proposed_content,
        proposed_dims=proposal.proposed_dims,
        proposed_at=proposal.proposed_at,
        proposed_by_identity_id=proposal.proposed_by_identity_id,
        source_session_id=proposal.source_session_id,
        rationale=proposal.rationale,
    )


@router.post("/me/soul/{proposal_id}/decide", response_model=MemoryProfileRead)
async def decide_my_soul_update(
    proposal_id: str,
    body: SoulDecisionIn,
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
    request: Request,
) -> MemoryProfileRead:
    ws_id = _require_workspace(workspace_id)
    await ws_svc.ensure_member_access(db, workspace_id=ws_id, identity_id=identity_id)
    row = await svc.decide_soul_update(
        db,
        workspace_id=ws_id,
        identity_id=identity_id,
        proposal_id=proposal_id,
        approve=(body.decision == "approve"),
        decided_by=identity_id,
        reason=body.reason,
    )
    await audit_svc.record(
        db,
        action=f"memory_profile.user_soul.{body.decision}",
        actor_identity_id=identity_id,
        workspace_id=ws_id,
        resource_type="memory_profile_soul_proposal",
        resource_id=None,
        summary=f"{body.decision}ed SOUL proposal={proposal_id}",
        metadata={"proposal_id": proposal_id, "reason": body.reason[:120]},
        request=request,
    )
    await db.commit()
    return MemoryProfileRead.model_validate(row)


# ─── Admin: read someone else's bundle ───────────────────
@router.get("/identities/{identity_id}", response_model=dict)
async def get_identity_profiles(
    identity_id: uuid.UUID,
    db: DBSession,
    current_identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
) -> dict:
    ws_id = _require_workspace(workspace_id)
    await ws_svc.ensure_admin(db, workspace_id=ws_id, identity_id=current_identity_id)
    profile = await svc.get_profile(
        db,
        workspace_id=ws_id,
        kind=MemoryProfileKind.USER_PROFILE,
        subject_id=identity_id,
    )
    soul = await svc.get_profile(
        db,
        workspace_id=ws_id,
        kind=MemoryProfileKind.USER_SOUL,
        subject_id=identity_id,
    )
    return {
        "identity_id": str(identity_id),
        "profile": MemoryProfileRead.model_validate(profile) if profile else None,
        "soul": MemoryProfileRead.model_validate(soul) if soul else None,
    }
