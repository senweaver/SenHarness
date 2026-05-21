"""Platform-admin surface for workspace creation quota (M0.12).

Three endpoints, all gated by ``platform_admin`` role + per-bucket
rate limit:

* ``GET    /admin/workspace-quotas``                          — list rows
* ``GET    /admin/workspace-quotas/{identity_id}``            — single row
* ``PATCH  /admin/identities/{id}/workspace-quota``           — set / clear override

Every mutation writes ``workspace.quota_override_set`` audit so an
external reviewer can correlate manual overrides with the affected
identity.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field

from app.api.deps import DBSession
from app.api.v1.admin import AdminGate
from app.core.rate_limit import rate_limit
from app.db.models.identity import Identity, IdentityStatus, PlatformRole
from app.db.models.workspace_creation_log import CreationKind
from app.services import workspace_quota as quota_svc

router = APIRouter(tags=["admin", "quota"])


class AdminQuotaRowOut(BaseModel):
    """Row shape for ``GET /admin/workspace-quotas``."""

    identity_id: uuid.UUID
    email: str
    name: str
    status: IdentityStatus
    platform_role: PlatformRole
    source_kind: CreationKind
    used: int
    limit: int
    override: int | None


class AdminQuotaListOut(BaseModel):
    rows: list[AdminQuotaRowOut]
    total: int


class IdentityQuotaUpdateIn(BaseModel):
    """``null`` clears the override (revert to platform default)."""

    quota: int | None = Field(default=None, ge=0, le=10000)


class IdentityQuotaUpdateOut(BaseModel):
    identity_id: uuid.UUID
    workspace_quota_override: int | None


def _serialize_row(row: quota_svc.AdminQuotaRow) -> AdminQuotaRowOut:
    return AdminQuotaRowOut(
        identity_id=row.identity_id,
        email=row.email,
        name=row.name,
        status=row.status,
        platform_role=row.platform_role,
        source_kind=row.source_kind,
        used=row.used,
        limit=row.limit,
        override=row.override,
    )


@router.get(
    "/admin/workspace-quotas",
    response_model=AdminQuotaListOut,
    dependencies=[
        Depends(rate_limit("admin_quota_read", limit=60, period_seconds=60)),
    ],
)
async def list_workspace_quotas(
    db: DBSession,
    _admin: Identity = AdminGate,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    sort_by_usage: bool = Query(default=True),
) -> AdminQuotaListOut:
    rows = await quota_svc.list_admin_quotas(
        db, limit=limit, offset=offset, sort_by_usage=sort_by_usage
    )
    return AdminQuotaListOut(
        rows=[_serialize_row(r) for r in rows],
        total=len(rows),
    )


@router.get(
    "/admin/workspace-quotas/{identity_id}",
    response_model=AdminQuotaRowOut,
    dependencies=[
        Depends(rate_limit("admin_quota_read", limit=60, period_seconds=60)),
    ],
)
async def get_workspace_quota(
    identity_id: uuid.UUID,
    db: DBSession,
    _admin: Identity = AdminGate,
) -> AdminQuotaRowOut:
    row = await quota_svc.admin_quota_for_identity(db, identity_id=identity_id)
    return _serialize_row(row)


@router.patch(
    "/admin/identities/{identity_id}/workspace-quota",
    response_model=IdentityQuotaUpdateOut,
    dependencies=[
        Depends(rate_limit("admin_quota_write", limit=20, period_seconds=60)),
    ],
)
async def update_workspace_quota_override(
    identity_id: uuid.UUID,
    body: IdentityQuotaUpdateIn,
    db: DBSession,
    request: Request,
    admin: Identity = AdminGate,
) -> IdentityQuotaUpdateOut:
    """Set / clear ``identities.workspace_quota_override``.

    ``body.quota = null`` clears the override; the identity reverts to
    the platform default for its inferred source kind on the next
    quota read.
    """
    target = await quota_svc.set_quota_override(
        db,
        target_identity_id=identity_id,
        quota=body.quota,
        actor_identity_id=admin.id,
        request=request,
    )
    await db.commit()
    return IdentityQuotaUpdateOut(
        identity_id=target.id,
        workspace_quota_override=target.workspace_quota_override,
    )
