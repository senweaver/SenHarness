"""Cross-session insights REST surface (M4.5).

Two routes round out the slash-command-only entry point:

* ``POST /insights/generate`` — same path as the slash command but
  reachable from REST so the UI can offer a "Generate insights"
  button outside the chat composer (used by the workspace home tile
  in M4.5+).
* ``GET /insights/recent`` — list the caller's last few
  ``insights.cross_session_summarized`` audit rows so the UI can
  show "your last 5 insight runs" without re-querying the artifact
  pipeline.

Both routes are identity-scoped: the caller can only generate /
inspect summaries of their own session_artifacts. Workspace admins
get the same surface but the privacy filter still scopes to *their*
identity (cross-identity insights are deliberately out of scope per
the M4.5 design — file an admin-tooling ticket if needed).
"""

from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.api.deps import CurrentIdentityId, CurrentWorkspaceId, DBSession
from app.core.errors import NotFound, Unauthorized
from app.core.rate_limit import rate_limit
from app.core.security import utcnow_naive
from app.repositories.audit import AuditRepository
from app.schemas.insights import (
    InsightsGenerateRequest,
    InsightsGenerateResponse,
    InsightsRecentResponse,
    InsightsRunSummary,
)
from app.services import cross_session_insights as insights_svc
from app.services import session as session_svc
from app.services import workspace as ws_svc

router = APIRouter(prefix="/insights", tags=["insights"])


_GENERATE_DEPS = [
    # Cap insights generation per identity. The job is aux-LLM heavy +
    # writes a chat message every time; 3/300s lines up with the
    # ``insights.queued`` audit cooldown design point.
    Depends(rate_limit("insights_generate", limit=3, period_seconds=300)),
]
_RECENT_DEPS = [
    Depends(rate_limit("insights_recent", limit=30, period_seconds=60)),
]


def _require_workspace(workspace_id: uuid.UUID | None) -> uuid.UUID:
    if workspace_id is None:
        raise Unauthorized("no_active_workspace", code="auth.no_active_workspace")
    return workspace_id


@router.post(
    "/generate",
    response_model=InsightsGenerateResponse,
    dependencies=_GENERATE_DEPS,
)
async def generate_insights(
    body: InsightsGenerateRequest,
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
) -> InsightsGenerateResponse:
    """Queue a cross-session insights run for the calling identity.

    The chat slash command (``/insights [--days N]``) and this
    endpoint share the underlying ``queue_insights_generation``
    service, so the gating story (``InsightsSettings.enabled``,
    breaker, day-window validation) is identical.
    """
    ws_id = _require_workspace(workspace_id)
    await ws_svc.ensure_member_access(
        db, workspace_id=ws_id, identity_id=identity_id
    )
    # Validate the target session belongs to this workspace + caller
    # before we queue a job that would write a chat message into it.
    session = await session_svc.get_session_or_404(
        db, body.return_session_id, workspace_id=ws_id
    )
    if (
        session.owner_identity_id is not None
        and session.owner_identity_id != identity_id
    ):
        raise NotFound("session_not_found", code="session.not_found")

    result = await insights_svc.queue_insights_generation(
        db,
        workspace_id=ws_id,
        identity_id=identity_id,
        return_session_id=body.return_session_id,
        days=body.days,
        actor_identity_id=identity_id,
        invocation_kind="rest",
    )
    await db.commit()
    return InsightsGenerateResponse(
        queued=bool(result.get("queued")),
        days=int(result.get("days", body.days or 30)),
        expected_completion_seconds=int(
            result.get("expected_completion_seconds", 30)
        ),
        job_id=result.get("job_id"),
    )


@router.get(
    "/recent",
    response_model=InsightsRecentResponse,
    dependencies=_RECENT_DEPS,
)
async def list_recent_insights(
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
    limit: Annotated[int, Query(ge=1, le=50)] = 10,
    days: Annotated[int, Query(ge=1, le=180)] = 30,
) -> InsightsRecentResponse:
    """Return the caller's recent insights runs (newest first).

    Reads straight from ``audit_events`` where
    ``action='insights.cross_session_summarized'`` and
    ``actor_identity_id == caller`` so the privacy boundary cannot be
    relaxed without touching the audit row's actor field. Empty list
    is the no-runs-yet signal — the UI renders the
    ``noInsightsYet`` empty state.
    """
    ws_id = _require_workspace(workspace_id)
    await ws_svc.ensure_member_access(
        db, workspace_id=ws_id, identity_id=identity_id
    )
    since = utcnow_naive() - timedelta(days=int(days))
    rows = await AuditRepository(db).search(
        workspace_id=ws_id,
        since=since,
        action=insights_svc.AUDIT_SUMMARIZED,
        actor_identity_id=identity_id,
        limit=int(limit),
    )

    items: list[InsightsRunSummary] = []
    for event, _identity in rows:
        meta = event.metadata_json or {}
        items.append(
            InsightsRunSummary(
                audit_event_id=event.id,
                session_id=event.resource_id,
                created_at=event.created_at,
                days=int(meta.get("days") or 0),
                artifact_count=int(meta.get("artifact_count") or 0),
                item_count=int(meta.get("item_count") or 0),
                aux_model=meta.get("aux_model"),
                degraded=bool(meta.get("degraded")),
            )
        )
    return InsightsRecentResponse(items=items)
