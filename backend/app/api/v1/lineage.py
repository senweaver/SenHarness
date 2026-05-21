"""M4.3 — REST surface for lineage replay.

Two read-only routes back the chat trace tab "Expand from summary"
affordance:

* ``GET /sessions/{session_id}/messages/{message_id}/lineage`` →
  :class:`~app.schemas.lineage.LineageReplay`. Resolves a compressed
  summary back to the original turns it folded. Returns 404
  ``lineage.not_compressed`` when the message exists but was never
  marked as a summary (the most common case).
* ``GET /sessions/{session_id}/lineage-summaries`` →
  ``list[LineageSummary]``. Lists every compressed summary in a
  session for the trace tab badge ("compressed N original turns").

RBAC: workspace-scoped. The caller must hold an ACTIVE membership
on the workspace that owns the session, same shape as the M0.2
session artifacts surface. Cross-workspace probes route through
:func:`app.services.session.get_session_or_404` and surface as
404 ``session.not_found``.

Audit: every successful replay query writes
``lineage.replay_queried`` so a forensics auditor can spot a user
who repeatedly expanded the same summary (potential attempt to
exfiltrate truncated content). The summaries-list call is hot on
the trace tab and not audited to avoid log spam.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query, Request, status

from app.api.deps import CurrentIdentityId, CurrentWorkspaceId, DBSession
from app.core.errors import NotFound, Unauthorized
from app.core.rate_limit import rate_limit
from app.schemas.lineage import LineageNode, LineageReplay, LineageSummary
from app.services import audit as audit_svc
from app.services import lineage_replay as lineage_svc
from app.services import workspace as ws_svc

router = APIRouter()


def _require_workspace(workspace_id: uuid.UUID | None) -> uuid.UUID:
    if workspace_id is None:
        raise Unauthorized("no_active_workspace", code="auth.no_active_workspace")
    return workspace_id


@router.get(
    "/sessions/{session_id}/messages/{message_id}/lineage",
    response_model=LineageReplay,
    status_code=status.HTTP_200_OK,
    dependencies=[
        Depends(rate_limit("lineage_read", limit=60, period_seconds=60))
    ],
    tags=["sessions"],
)
async def get_message_lineage(
    session_id: uuid.UUID,
    message_id: uuid.UUID,
    db: DBSession,
    request: Request,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
) -> LineageReplay:
    ws_id = _require_workspace(workspace_id)
    await ws_svc.ensure_member_access(
        db, workspace_id=ws_id, identity_id=identity_id
    )
    replay = await lineage_svc.get_lineage_replay(
        db,
        workspace_id=ws_id,
        session_id=session_id,
        message_id=message_id,
    )
    if replay is None:
        raise NotFound(
            "message has no lineage",
            code="lineage.not_compressed",
        )

    await audit_svc.record(
        db,
        action="lineage.replay_queried",
        actor_identity_id=identity_id,
        workspace_id=ws_id,
        resource_type="message",
        resource_id=message_id,
        summary="lineage replay viewed",
        metadata={
            "session_id": str(session_id),
            "summary_message_id": str(replay.summary_message_id),
            "original_turn_count": replay.original_turn_count,
            "compaction_strategy": replay.compaction_strategy,
        },
        request=request,
    )
    await db.commit()

    return LineageReplay(
        summary_message_id=replay.summary_message_id,
        session_id=replay.session_id,
        workspace_id=replay.workspace_id,
        original_turn_count=replay.original_turn_count,
        original_turns=[
            LineageNode(
                message_id=node.message_id,
                role=node.role,
                text_excerpt=node.text_excerpt,
                created_at=node.created_at,
                is_compressed_summary=node.is_compressed_summary,
                is_original_turn=node.is_original_turn,
            )
            for node in replay.original_turns
        ],
        compaction_strategy=replay.compaction_strategy,
        compressed_at=replay.compressed_at,
    )


@router.get(
    "/sessions/{session_id}/lineage-summaries",
    response_model=list[LineageSummary],
    status_code=status.HTTP_200_OK,
    dependencies=[
        Depends(rate_limit("lineage_summaries_read", limit=30, period_seconds=60))
    ],
    tags=["sessions"],
)
async def list_session_lineage_summaries(
    session_id: uuid.UUID,
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
    limit: int = Query(50, ge=1, le=200),
) -> list[LineageSummary]:
    ws_id = _require_workspace(workspace_id)
    await ws_svc.ensure_member_access(
        db, workspace_id=ws_id, identity_id=identity_id
    )
    rows = await lineage_svc.list_compressed_summaries_in_session(
        db,
        workspace_id=ws_id,
        session_id=session_id,
        limit=limit,
    )
    return [LineageSummary(**row) for row in rows]
