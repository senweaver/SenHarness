"""Skill verifier endpoints (M2.4).

Two routes — one verb, one read — that surface the
:mod:`app.services.skill_verifier` machinery to the workspace UI:

* ``POST .../verify-now`` lets a workspace admin force-verify a
  PROPOSED candidate version without waiting for the next 30-minute
  cron tick. Tighter rate (5/300s) than the cron itself because each
  call fans out to ``2 * min_replay_artifacts`` aux LLM invocations.
* ``GET .../validation`` reads back the
  :class:`~app.db.models.skill_pack_version.SkillPackVersion`'s
  ``validation_results`` JSONB blob plus the current state — the
  approval card UI (M2.5) consumes this directly.
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, Request

from app.api.deps import CurrentIdentityId, CurrentWorkspaceId, DBSession
from app.core.errors import NotFound, Unauthorized
from app.core.rate_limit import rate_limit
from app.repositories.skill_pack_version import SkillPackVersionRepository
from app.repositories.skills import SkillPackRepository
from app.schemas.skill_verifier import (
    SkillVerifierRunResponse,
    SkillVerifierValidationResponse,
)
from app.services import skill_verifier as verifier_svc
from app.services import workspace as ws_svc

router = APIRouter(prefix="/skills/packs", tags=["skills"])


def _require_workspace(workspace_id: uuid.UUID | None) -> uuid.UUID:
    if workspace_id is None:
        raise Unauthorized("no_active_workspace", code="auth.no_active_workspace")
    return workspace_id


async def _ensure_pack_in_workspace(
    db, *, ws_id: uuid.UUID, pack_id: uuid.UUID
):
    pack = await SkillPackRepository(db).get(pack_id, include_deleted=True)
    if pack is None or pack.workspace_id != ws_id:
        raise NotFound("skill_pack_not_found", code="skill_pack.not_found")
    return pack


async def _load_version_or_404(
    db, *, ws_id: uuid.UUID, pack_id: uuid.UUID, version_id: uuid.UUID
):
    pack = await _ensure_pack_in_workspace(db, ws_id=ws_id, pack_id=pack_id)
    row = await SkillPackVersionRepository(db).get(version_id)
    if row is None or row.workspace_id != ws_id or row.pack_id != pack.id:
        raise NotFound(
            "skill_pack_version_not_found", code="skill_version.not_found"
        )
    return row


@router.post(
    "/{pack_id}/versions/{version_id}/verify-now",
    response_model=SkillVerifierRunResponse,
    dependencies=[
        Depends(rate_limit("skills_verify_now", limit=5, period_seconds=300)),
    ],
)
async def verify_skill_version_now(
    pack_id: uuid.UUID,
    version_id: uuid.UUID,
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
    request: Request,
) -> SkillVerifierRunResponse:
    """Run :func:`verify_skill_version` synchronously for one PROPOSED version.

    Workspace admin only. Returns the
    :class:`~app.services.skill_verifier.VerificationResult` shape so
    the UI can render the new state + the score delta + how many
    artifacts were replayed without a follow-up read.
    """
    ws_id = _require_workspace(workspace_id)
    await ws_svc.ensure_admin(db, workspace_id=ws_id, identity_id=identity_id)
    await _load_version_or_404(
        db, ws_id=ws_id, pack_id=pack_id, version_id=version_id
    )

    result = await verifier_svc.verify_skill_version(
        db,
        workspace_id=ws_id,
        version_id=version_id,
        request=request,
    )
    await db.commit()

    return SkillVerifierRunResponse(
        version_id=result.version_id,
        status=result.status,
        old_score_avg=result.old_score_avg,
        new_score_avg=result.new_score_avg,
        score_delta=result.score_delta,
        replayed_artifacts=result.replayed_artifacts,
        threshold=result.threshold,
        duration_ms=result.duration_ms,
        error=result.error,
    )


@router.get(
    "/{pack_id}/versions/{version_id}/validation",
    response_model=SkillVerifierValidationResponse,
    dependencies=[
        Depends(rate_limit("skills_verify_read", limit=60, period_seconds=60)),
    ],
)
async def get_skill_version_validation(
    pack_id: uuid.UUID,
    version_id: uuid.UUID,
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
) -> SkillVerifierValidationResponse:
    """Return the persisted ``validation_results`` blob + version state.

    Workspace member only. Returns ``validation_results={}`` for
    versions that have never been verified — the UI uses that to show
    a "not yet verified" placeholder rather than a hard 404, which
    would force every version-list cell into a try/catch.
    """
    ws_id = _require_workspace(workspace_id)
    await ws_svc.ensure_member_access(
        db, workspace_id=ws_id, identity_id=identity_id
    )
    version = await _load_version_or_404(
        db, ws_id=ws_id, pack_id=pack_id, version_id=version_id
    )
    payload: dict[str, Any] = dict(version.validation_results or {})
    return SkillVerifierValidationResponse(
        version_id=version.id,
        pack_id=version.pack_id,
        version_no=version.version_no,
        state=version.state,
        judge_score=version.judge_score,
        validation_results=payload,
    )
