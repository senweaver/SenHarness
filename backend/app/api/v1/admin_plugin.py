"""Platform-admin surface for the plugin registry (M3.5 + M3.9).

Six endpoints, every one gated by ``platform_admin`` role + a
per-bucket Redis rate limit:

* ``GET    /admin/plugins``                     — list every registry row
* ``GET    /admin/plugins/{registry_id}``       — full row detail
* ``POST   /admin/plugins/{registry_id}/approve`` — promote DISCOVERED →
  APPROVED, optionally trigger a hot reload
* ``POST   /admin/plugins/{registry_id}/reject``  — terminal REJECTED
* ``POST   /admin/plugins/scan``                — populate the registry
  from the on-disk plugins directory without registering anything
* ``POST   /admin/plugins/reload``              — re-run the loader
  honouring the current platform settings + approval flags

Approval / reject decisions write ``plugin.approved_by_admin`` /
``plugin.rejected_by_admin`` audit rows so the trust trail survives
admin churn.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import desc, select

from app.api.deps import DBSession
from app.api.v1.admin import AdminGate
from app.core.config import settings
from app.core.rate_limit import rate_limit
from app.db.models.identity import Identity
from app.db.models.plugin_registry import PluginRegistry, PluginRegistryStatus
from app.services import audit as audit_svc
from app.services import platform_settings as ps_svc
from app.services.plugin_loader import (
    discover_plugins,
    load_and_register_plugins,
)

router = APIRouter(
    prefix="/admin/plugins",
    tags=["admin", "plugins"],
)


# ── DTOs ──────────────────────────────────────────────────────
class PluginRegistryRead(BaseModel):
    """JSON shape of one ``plugin_registry`` row.

    ``signature`` is surfaced unredacted because base64 ed25519 sigs
    aren't sensitive — verification needs the public key only and
    publishing the signature is harmless. The trust root pubkey is
    the platform-wide ``signing_root_pubkey`` and lives behind the
    settings endpoint.
    """

    id: uuid.UUID
    name: str
    version: str
    sha256: str
    signature: str | None
    trust_root: str | None
    approved_by_platform_admin: bool
    approved_at: datetime | None
    approved_by_identity_id: uuid.UUID | None
    status: PluginRegistryStatus
    capability_scopes: list[str]
    last_load_attempt_at: datetime | None
    last_load_error: str | None
    folder_name: str | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class ApprovePayload(BaseModel):
    reload: bool = Field(
        default=True,
        description=(
            "When True, immediately re-run the loader so the freshly "
            "approved plugin starts firing without a backend restart. "
            "Honours platform_settings.plugins.auto_reload_on_admin_approve "
            "as the upper bound."
        ),
    )


class RejectPayload(BaseModel):
    reason: str | None = Field(default=None, max_length=500)


class ScanResult(BaseModel):
    discovered: int
    rows_total: int
    new_rows: int


class ReloadResult(BaseModel):
    loaded: int
    plugin_dir: str
    allow_user_plugins: bool


# ── Helpers ───────────────────────────────────────────────────
def _serialize(row: PluginRegistry) -> PluginRegistryRead:
    return PluginRegistryRead(
        id=row.id,
        name=row.name,
        version=row.version,
        sha256=row.sha256,
        signature=row.signature,
        trust_root=row.trust_root,
        approved_by_platform_admin=row.approved_by_platform_admin,
        approved_at=row.approved_at,
        approved_by_identity_id=row.approved_by_identity_id,
        status=row.status,
        capability_scopes=list(row.capability_scopes or []),
        last_load_attempt_at=row.last_load_attempt_at,
        last_load_error=row.last_load_error,
        folder_name=row.folder_name,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _plugin_dir() -> Path:
    return Path(settings.STORAGE_LOCAL_PATH) / "plugins"


# ── Routes ────────────────────────────────────────────────────
@router.get(
    "",
    response_model=list[PluginRegistryRead],
    dependencies=[
        Depends(rate_limit("plugin_admin_read", limit=30, period_seconds=60))
    ],
)
async def list_plugins(
    db: DBSession,
    _admin: Identity = AdminGate,
    status_filter: str | None = None,
) -> list[PluginRegistryRead]:
    """List every plugin_registry row, newest write first."""
    stmt = select(PluginRegistry).order_by(desc(PluginRegistry.updated_at))
    if status_filter:
        try:
            sf = PluginRegistryStatus(status_filter)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        stmt = stmt.where(PluginRegistry.status == sf)
    rows = (await db.execute(stmt)).scalars().all()
    return [_serialize(r) for r in rows]


@router.get(
    "/{registry_id}",
    response_model=PluginRegistryRead,
    dependencies=[
        Depends(rate_limit("plugin_admin_read", limit=30, period_seconds=60))
    ],
)
async def get_plugin(
    registry_id: uuid.UUID,
    db: DBSession,
    _admin: Identity = AdminGate,
) -> PluginRegistryRead:
    row = (
        await db.execute(
            select(PluginRegistry).where(PluginRegistry.id == registry_id)
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="plugin_not_found")
    return _serialize(row)


@router.post(
    "/{registry_id}/approve",
    response_model=PluginRegistryRead,
    dependencies=[
        Depends(rate_limit("plugin_admin_action", limit=10, period_seconds=60))
    ],
)
async def approve_plugin(
    registry_id: uuid.UUID,
    request: Request,
    db: DBSession,
    payload: ApprovePayload | None = None,
    admin: Identity = AdminGate,
) -> PluginRegistryRead:
    """Mark a plugin row APPROVED so the next load picks it up.

    Approval does not bypass the signature check — the loader still
    runs ``evaluate_plugin_for_load``. A row whose signature later
    fails verification is skipped at load time and the failure
    audits separately so the operator sees the breakdown without
    the approval row itself flipping status.
    """
    row = (
        await db.execute(
            select(PluginRegistry).where(PluginRegistry.id == registry_id)
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="plugin_not_found")
    if row.status == PluginRegistryStatus.REJECTED:
        raise HTTPException(
            status_code=400, detail="plugin_rejected_cannot_approve"
        )
    row.approved_by_platform_admin = True
    row.approved_at = datetime.now(UTC).replace(tzinfo=None)
    row.approved_by_identity_id = admin.id
    row.status = PluginRegistryStatus.APPROVED
    await audit_svc.record(
        db,
        action="plugin.approved_by_admin",
        actor_identity_id=admin.id,
        workspace_id=None,
        resource_type="plugin",
        resource_id=row.id,
        summary=f"plugin {row.name!r} v{row.version} approved",
        metadata={
            "name": row.name,
            "version": row.version,
            "sha256": row.sha256,
            "registry_id": str(row.id),
        },
        request=request,
    )
    await db.commit()

    payload = payload or ApprovePayload()
    if payload.reload:
        plugins_settings = await ps_svc.get_section(
            db, section=ps_svc.PlatformSettingsSection.PLUGINS
        )
        if bool(getattr(plugins_settings, "auto_reload_on_admin_approve", True)):
            try:
                await load_and_register_plugins(db, plugin_dir=_plugin_dir())
            except Exception:  # pragma: no cover - never break the approve call
                # Reload is a best-effort post-approval action; the
                # admin can always click /reload manually.
                pass

    refreshed = (
        await db.execute(
            select(PluginRegistry).where(PluginRegistry.id == registry_id)
        )
    ).scalar_one_or_none()
    if refreshed is None:  # pragma: no cover - row was just upserted
        raise HTTPException(status_code=500, detail="plugin_disappeared")
    return _serialize(refreshed)


@router.post(
    "/{registry_id}/reject",
    response_model=PluginRegistryRead,
    dependencies=[
        Depends(rate_limit("plugin_admin_action", limit=10, period_seconds=60))
    ],
)
async def reject_plugin(
    registry_id: uuid.UUID,
    request: Request,
    db: DBSession,
    payload: RejectPayload | None = None,
    admin: Identity = AdminGate,
) -> PluginRegistryRead:
    row = (
        await db.execute(
            select(PluginRegistry).where(PluginRegistry.id == registry_id)
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="plugin_not_found")
    row.status = PluginRegistryStatus.REJECTED
    row.approved_by_platform_admin = False
    row.approved_at = None
    row.approved_by_identity_id = None
    payload = payload or RejectPayload()
    metadata: dict[str, Any] = {
        "name": row.name,
        "version": row.version,
        "sha256": row.sha256,
        "registry_id": str(row.id),
    }
    if payload.reason:
        metadata["reason"] = payload.reason
    await audit_svc.record(
        db,
        action="plugin.rejected_by_admin",
        actor_identity_id=admin.id,
        workspace_id=None,
        resource_type="plugin",
        resource_id=row.id,
        summary=f"plugin {row.name!r} v{row.version} rejected",
        metadata=metadata,
        request=request,
    )
    await db.commit()
    return _serialize(row)


@router.post(
    "/scan",
    response_model=ScanResult,
    dependencies=[
        Depends(rate_limit("plugin_admin_scan", limit=5, period_seconds=300))
    ],
)
async def scan_plugins(
    request: Request,
    db: DBSession,
    admin: Identity = AdminGate,
) -> ScanResult:
    """Walk the on-disk plugin directory and ensure every folder has
    a registry row in DISCOVERED state.

    Does NOT register hooks or consult ``allow_user_plugins`` — the
    admin reviews discovered rows and explicitly approves before
    any plugin code runs. Returns counts so the admin UI can show
    a "X new plugins to review" banner.
    """
    from app.repositories.plugin_registry import PluginRegistryRepository

    plugin_dir = _plugin_dir()
    discovered = await discover_plugins(plugin_dir)
    repo = PluginRegistryRepository(db)
    new_rows = 0
    for plugin in discovered:
        if plugin.error or plugin.register_func is None:
            continue
        existing = await repo.get_by_sha(
            name=plugin.manifest.name,
            version=plugin.manifest.version,
            sha256=plugin.sha256,
        )
        if existing is None:
            new_rows += 1
        await repo.upsert_discovered(
            name=plugin.manifest.name,
            version=plugin.manifest.version,
            sha256=plugin.sha256,
            signature=plugin.signature,
            capability_scopes=list(plugin.manifest.capability_scopes),
            folder_name=plugin.folder.name,
        )

    rows_total = (
        await db.execute(select(PluginRegistry))
    ).scalars().all()
    await audit_svc.record(
        db,
        action="plugin.scanned",
        actor_identity_id=admin.id,
        workspace_id=None,
        resource_type="plugin",
        resource_id=None,
        summary=(
            f"plugin scan: {len(discovered)} folder(s), {new_rows} new row(s)"
        ),
        metadata={
            "plugin_dir": str(plugin_dir),
            "discovered": len(discovered),
            "new_rows": new_rows,
        },
        request=request,
    )
    await db.commit()
    return ScanResult(
        discovered=len(discovered),
        rows_total=len(rows_total),
        new_rows=new_rows,
    )


@router.post(
    "/reload",
    response_model=ReloadResult,
    dependencies=[
        Depends(rate_limit("plugin_admin_reload", limit=3, period_seconds=300))
    ],
)
async def reload_plugins(
    request: Request,
    db: DBSession,
    admin: Identity = AdminGate,
) -> ReloadResult:
    """Re-run the loader, honouring the current platform settings.

    Equivalent to a soft restart of the plugin host: every previously
    loaded callback stays where it is (the runner host is process-
    local; M3.5 doesn't ship a hot-unload path), but newly approved
    plugins start firing on the next runner event.
    """
    plugin_dir = _plugin_dir()
    plugins_settings = await ps_svc.get_section(
        db, section=ps_svc.PlatformSettingsSection.PLUGINS
    )
    allow = bool(getattr(plugins_settings, "allow_user_plugins", False))
    if not allow:
        await load_and_register_plugins(
            db, plugin_dir=plugin_dir, allow_user_plugins=False
        )
        loaded_count = 0
    else:
        loaded = await load_and_register_plugins(db, plugin_dir=plugin_dir)
        loaded_count = len(loaded)

    await audit_svc.record(
        db,
        action="plugin.reloaded",
        actor_identity_id=admin.id,
        workspace_id=None,
        resource_type="plugin",
        resource_id=None,
        summary=f"plugin reload: {loaded_count} loaded",
        metadata={
            "plugin_dir": str(plugin_dir),
            "loaded": loaded_count,
            "allow_user_plugins": allow,
        },
        request=request,
    )
    await db.commit()
    return ReloadResult(
        loaded=loaded_count,
        plugin_dir=str(plugin_dir),
        allow_user_plugins=allow,
    )
