"""Audit log query + CSV export.

Scope:
* ``scope=workspace`` (default): rows where ``workspace_id`` matches the
  caller's active workspace. Requires workspace **admin** or **owner**.
* ``scope=platform``: platform-wide feed (including rows with no workspace).
  Requires platform-admin role on the identity.
"""

from __future__ import annotations

import csv
import io
import json
import uuid
from datetime import UTC, date, datetime, time, timedelta

from fastapi import APIRouter, Query, Response

from app.api.deps import CurrentIdentityId, CurrentWorkspaceId, DBSession
from app.core.errors import PermissionDenied, Unauthorized
from app.db.models.identity import PlatformRole
from app.db.models.role import BuiltinRole
from app.repositories.audit import AuditRepository
from app.repositories.identity import IdentityRepository
from app.schemas.audit import AuditEventEnriched
from app.services import workspace as ws_svc

router = APIRouter(prefix="/audit", tags=["audit"])


def _require_workspace(workspace_id: uuid.UUID | None) -> uuid.UUID:
    if workspace_id is None:
        raise Unauthorized("no_active_workspace", code="auth.no_active_workspace")
    return workspace_id


async def _is_platform_admin(db, identity_id: uuid.UUID) -> bool:
    ident = await IdentityRepository(db).get(identity_id)
    return (
        ident is not None
        and ident.platform_role == PlatformRole.PLATFORM_ADMIN
    )


def _parse_window(
    since: date | None, until: date | None
) -> tuple[datetime | None, datetime | None]:
    today = datetime.now(UTC).date()
    until_d = until or (today + timedelta(days=1))
    since_d = since or (until_d - timedelta(days=30))
    return datetime.combine(since_d, time.min), datetime.combine(until_d, time.min)


@router.get("/events", response_model=list[AuditEventEnriched])
async def list_audit_events(
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
    scope: str = Query("workspace", pattern="^(workspace|platform)$"),
    since: date | None = Query(None),
    until: date | None = Query(None),
    action: str | None = Query(None, description="Exact or prefix match (use '*')."),
    actor: uuid.UUID | None = Query(None, description="Filter by actor identity id."),
    resource_type: str | None = Query(None),
    resource_id: uuid.UUID | None = Query(None),
    q: str | None = Query(None, description="Search in summary (ILIKE)."),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> list[AuditEventEnriched]:
    ws_filter = await _resolve_scope(
        db,
        scope=scope,
        identity_id=identity_id,
        workspace_id=workspace_id,
    )
    start, end = _parse_window(since, until)
    rows = await AuditRepository(db).search(
        workspace_id=ws_filter,
        since=start,
        until=end,
        action=action,
        actor_identity_id=actor,
        resource_type=resource_type,
        resource_id=resource_id,
        q=q,
        limit=limit,
        offset=offset,
    )
    return [_enrich(ev, actor_row) for ev, actor_row in rows]


@router.get("/events.csv")
async def export_audit_events_csv(
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
    scope: str = Query("workspace", pattern="^(workspace|platform)$"),
    since: date | None = Query(None),
    until: date | None = Query(None),
    action: str | None = Query(None),
    actor: uuid.UUID | None = Query(None),
    resource_type: str | None = Query(None),
    resource_id: uuid.UUID | None = Query(None),
    q: str | None = Query(None),
    limit: int = Query(5000, ge=1, le=50000),
) -> Response:
    ws_filter = await _resolve_scope(
        db,
        scope=scope,
        identity_id=identity_id,
        workspace_id=workspace_id,
    )
    start, end = _parse_window(since, until)
    rows = await AuditRepository(db).search(
        workspace_id=ws_filter,
        since=start,
        until=end,
        action=action,
        actor_identity_id=actor,
        resource_type=resource_type,
        resource_id=resource_id,
        q=q,
        limit=limit,
        offset=0,
    )

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(
        [
            "created_at",
            "action",
            "actor_name",
            "actor_email",
            "workspace_id",
            "resource_type",
            "resource_id",
            "summary",
            "ip_address",
            "metadata_json",
        ]
    )
    for ev, actor_row in rows:
        writer.writerow(
            [
                ev.created_at.isoformat(),
                ev.action,
                actor_row.name if actor_row else "",
                actor_row.email if actor_row else "",
                str(ev.workspace_id) if ev.workspace_id else "",
                ev.resource_type or "",
                str(ev.resource_id) if ev.resource_id else "",
                ev.summary or "",
                ev.ip_address or "",
                json.dumps(ev.metadata_json, ensure_ascii=False),
            ]
        )

    csv_text = buf.getvalue()
    filename = f"audit-{datetime.now(UTC).strftime('%Y%m%d_%H%M%S')}.csv"
    return Response(
        # UTF-8 BOM helps Excel open the file with correct encoding.
        content="\ufeff" + csv_text,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


async def _resolve_scope(
    db,
    *,
    scope: str,
    identity_id: uuid.UUID,
    workspace_id: uuid.UUID | None,
) -> uuid.UUID | None:
    """Return the workspace filter: UUID to restrict, or None for platform-wide.

    Raises ``PermissionDenied`` if the caller isn't allowed to see the
    requested scope.
    """
    if scope == "platform":
        if not await _is_platform_admin(db, identity_id):
            raise PermissionDenied(
                "platform_admin_required", code="audit.platform_admin_required"
            )
        return None

    ws_id = _require_workspace(workspace_id)
    mem = await ws_svc.ensure_member_access(
        db, workspace_id=ws_id, identity_id=identity_id
    )
    if mem.role not in {
        BuiltinRole.OWNER.value,
        BuiltinRole.ADMIN.value,
        BuiltinRole.AUDITOR.value,
    }:
        raise PermissionDenied(
            "admin_or_auditor_required", code="audit.admin_or_auditor_required"
        )
    return ws_id


def _enrich(ev, actor_row) -> AuditEventEnriched:
    card = AuditEventEnriched.model_validate(ev)
    if actor_row is not None:
        card.actor_name = actor_row.name
        card.actor_email = actor_row.email
    return card
