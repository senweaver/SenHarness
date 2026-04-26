"""GET /metrics/usage — workspace cost & token dashboard data.

One endpoint returns the whole dashboard payload (summary + daily series +
top agents + top models). Clients can narrow scope with ``?scope=me`` to see
only their own usage (default is workspace-wide for admins, ``me`` otherwise).

Date window (``since`` / ``until``) is inclusive-start / exclusive-end on
``created_at``. If either is omitted we default to the last 30 days.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, time, timedelta

from fastapi import APIRouter, Query, Response, status
from pydantic import BaseModel, Field

from app.api.deps import CurrentIdentityId, CurrentWorkspaceId, DBSession
from app.core.errors import Unauthorized
from app.core.prometheus import record_web_vital, render_exposition
from app.db.models.membership import Membership
from app.db.models.role import BuiltinRole
from app.repositories.metrics import MetricsRepository
from app.schemas.metrics import (
    UsageByAgent,
    UsageByModel,
    UsageDailyBucket,
    UsageReport,
    UsageSummary,
)
from app.services import workspace as ws_svc

router = APIRouter(prefix="/metrics", tags=["metrics"])


class WebVitalIn(BaseModel):
    """Browser-reported Web Vital payload.

    Sent from ``frontend/src/lib/web-vitals.ts`` via ``navigator.sendBeacon``
    — keep it tiny. The ``id`` field lets us correlate retries but we
    don't persist it; we only feed ``name + value + path`` into the
    Prometheus histogram.
    """

    name: str = Field(min_length=1, max_length=32)
    value: float = Field(ge=0.0)
    id: str | None = Field(default=None, max_length=64)
    path: str | None = Field(default=None, max_length=256)


@router.get(
    "/prometheus",
    response_class=Response,
    summary="Prometheus scrape endpoint",
    description=(
        "Expose process-level counters / histograms in the Prometheus "
        "exposition format. Intended for `prometheus-operator` scrapes; "
        "no auth is enforced so operators should either put this path "
        "behind a private subnet or terminate it at an ingress gate."
    ),
)
def prometheus_scrape() -> Response:
    return Response(
        content=render_exposition(),
        media_type="text/plain; version=0.0.4; charset=utf-8",
    )


@router.post(
    "/web-vitals",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Ingest a Web Vitals sample from the browser",
    description=(
        "Fire-and-forget endpoint targeted by ``navigator.sendBeacon`` "
        "from the frontend. Accepts the standard Web Vitals names "
        "(CLS / FCP / INP / LCP / TTFB); anything else is silently "
        "dropped server-side to prevent label explosion."
    ),
)
def ingest_web_vital(body: WebVitalIn) -> Response:
    record_web_vital(body.name, body.value, body.path or "/")
    return Response(status_code=status.HTTP_202_ACCEPTED)


def _require_workspace(workspace_id: uuid.UUID | None) -> uuid.UUID:
    if workspace_id is None:
        raise Unauthorized("no_active_workspace", code="auth.no_active_workspace")
    return workspace_id


def _parse_window(
    since: date | None, until: date | None
) -> tuple[datetime, datetime, date, date]:
    """Resolve the date window, defaulting to last 30 days.

    Returned datetimes are UTC-naive (Postgres stores naive timestamps).
    ``until`` is advanced by one day to make the upper bound exclusive.
    """
    today = datetime.now(UTC).date()
    until_d = until or (today + timedelta(days=1))
    since_d = since or (until_d - timedelta(days=30))
    start = datetime.combine(since_d, time.min)
    end = datetime.combine(until_d, time.min)
    return start, end, since_d, until_d


async def _can_see_workspace(
    db, workspace_id: uuid.UUID, identity_id: uuid.UUID
) -> bool:
    """Admin/owner may view everyone's usage; members see only their own."""
    mem: Membership = await ws_svc.ensure_member_access(
        db, workspace_id=workspace_id, identity_id=identity_id
    )
    return mem.role in {BuiltinRole.OWNER.value, BuiltinRole.ADMIN.value}


@router.get("/usage", response_model=UsageReport)
async def get_usage_report(
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
    since: date | None = Query(None, description="Inclusive start (YYYY-MM-DD)."),
    until: date | None = Query(None, description="Exclusive end (YYYY-MM-DD)."),
    scope: str = Query(
        "auto",
        description="'me' (only your usage), 'workspace' (admin/owner only), 'auto'.",
    ),
    top: int = Query(10, ge=1, le=50),
) -> UsageReport:
    ws_id = _require_workspace(workspace_id)
    can_see_all = await _can_see_workspace(db, ws_id, identity_id)

    resolved_scope = scope
    if resolved_scope == "auto":
        resolved_scope = "workspace" if can_see_all else "me"
    if resolved_scope == "workspace" and not can_see_all:
        resolved_scope = "me"
    scope_identity = None if resolved_scope == "workspace" else identity_id

    start, end, since_d, until_d = _parse_window(since, until)

    repo = MetricsRepository(db)
    summary = await repo.summary(
        workspace_id=ws_id,
        since=start,
        until=end,
        identity_id=scope_identity,
    )
    daily = await repo.daily(
        workspace_id=ws_id,
        since=start,
        until=end,
        identity_id=scope_identity,
    )
    top_agents = await repo.top_agents(
        workspace_id=ws_id,
        since=start,
        until=end,
        identity_id=scope_identity,
        limit=top,
    )
    top_models = await repo.top_models(
        workspace_id=ws_id,
        since=start,
        until=end,
        identity_id=scope_identity,
        limit=top,
    )

    return UsageReport(
        since=since_d,
        until=until_d,
        scope=resolved_scope,
        summary=UsageSummary(**summary),
        daily=[UsageDailyBucket(**row) for row in daily],
        top_agents=[UsageByAgent(**row) for row in top_agents],
        top_models=[UsageByModel(**row) for row in top_models],
    )
