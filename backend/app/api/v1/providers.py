"""Model provider routes — CRUD + vault-backed key storage + model discovery."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, status

from app.agents.kernels.model_profile import _match_builtin, resolve_profile
from app.api.deps import CurrentIdentityId, CurrentWorkspaceId, DBSession
from app.core.errors import Unauthorized
from app.schemas.provider import (
    DiscoverApplyRequest,
    DiscoveredModel,
    DiscoverResponse,
    ProviderCreate,
    ProviderModelManualCreate,
    ProviderModelRead,
    ProviderModelReorderRequest,
    ProviderModelUpdate,
    ProviderRead,
    ProviderReorderRequest,
    ProviderTestRequest,
    ProviderTestResponse,
    ProviderUpdate,
    ResolvedReasoningProfile,
)
from app.services import provider as svc
from app.services import workspace as ws_svc

router = APIRouter()


def _require_workspace(workspace_id: uuid.UUID | None) -> uuid.UUID:
    if workspace_id is None:
        raise Unauthorized("no_active_workspace", code="auth.no_active_workspace")
    return workspace_id


async def _attach_has_key(db, provider) -> ProviderRead:
    has_key = await svc.provider_has_key(db, provider_id=provider.id)
    hint = await svc.provider_key_hint(db, provider_id=provider.id) if has_key else None
    out = ProviderRead.model_validate(provider)
    out.has_key = has_key
    out.api_key_hint = hint
    return out


@router.get("", response_model=list[ProviderRead])
async def list_providers(
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
) -> list[ProviderRead]:
    ws_id = _require_workspace(workspace_id)
    await ws_svc.ensure_member_access(db, workspace_id=ws_id, identity_id=identity_id)
    rows = await svc.list_providers(db, workspace_id=ws_id)
    return [await _attach_has_key(db, p) for p in rows]


@router.post("", response_model=ProviderRead, status_code=status.HTTP_201_CREATED)
async def create_provider(
    body: ProviderCreate,
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
) -> ProviderRead:
    ws_id = _require_workspace(workspace_id)
    await ws_svc.ensure_admin(db, workspace_id=ws_id, identity_id=identity_id)
    provider = await svc.create_provider(
        db,
        workspace_id=ws_id,
        owner_identity_id=identity_id,
        kind=body.kind,
        name=body.name,
        base_url=body.base_url,
        default_model=body.default_model,
        enabled=body.enabled,
        credential_type=body.credential_type,
        country_code=body.country_code,
        metadata_json=body.metadata_json,
        api_key=body.api_key,
    )
    await db.commit()
    return await _attach_has_key(db, provider)


@router.post("/reorder", response_model=list[ProviderRead])
async def reorder_providers(
    body: ProviderReorderRequest,
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
) -> list[ProviderRead]:
    """Persist drag-to-reorder for the workspace's provider list.

    Returns the workspace's providers in their **new** order so the
    caller can replace its local cache atomically. Ids belonging to
    another workspace (or already deleted) are silently dropped — the
    service layer treats them as no-ops.
    """
    ws_id = _require_workspace(workspace_id)
    await ws_svc.ensure_admin(db, workspace_id=ws_id, identity_id=identity_id)
    rows = await svc.reorder_providers(db, workspace_id=ws_id, ordered_ids=body.ordered_ids)
    await db.commit()
    return [await _attach_has_key(db, p) for p in rows]


@router.patch("/{provider_id}", response_model=ProviderRead)
async def update_provider(
    provider_id: uuid.UUID,
    body: ProviderUpdate,
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
) -> ProviderRead:
    ws_id = _require_workspace(workspace_id)
    await ws_svc.ensure_admin(db, workspace_id=ws_id, identity_id=identity_id)
    provider = await svc.get_or_404(db, provider_id, workspace_id=ws_id)
    await svc.update_provider(
        db,
        provider=provider,
        **body.model_dump(exclude_none=True, exclude={"api_key"}),
        api_key=body.api_key,
    )
    await db.commit()
    return await _attach_has_key(db, provider)


@router.delete("/{provider_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_provider(
    provider_id: uuid.UUID,
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
) -> None:
    ws_id = _require_workspace(workspace_id)
    await ws_svc.ensure_admin(db, workspace_id=ws_id, identity_id=identity_id)
    provider = await svc.get_or_404(db, provider_id, workspace_id=ws_id)
    await svc.delete_provider(db, provider=provider)
    await db.commit()


# ─── Models per provider ─────────────────────────────────────────


@router.get("/{provider_id}/models", response_model=list[ProviderModelRead])
async def list_provider_models(
    provider_id: uuid.UUID,
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
) -> list[ProviderModelRead]:
    ws_id = _require_workspace(workspace_id)
    await ws_svc.ensure_member_access(db, workspace_id=ws_id, identity_id=identity_id)
    provider = await svc.get_or_404(db, provider_id, workspace_id=ws_id)
    rows = await svc.list_provider_models(db, provider_id=provider.id)
    return [ProviderModelRead.model_validate(r) for r in rows]


@router.post(
    "/{provider_id}/models",
    response_model=ProviderModelRead,
    status_code=status.HTTP_201_CREATED,
)
async def add_provider_model(
    provider_id: uuid.UUID,
    body: ProviderModelManualCreate,
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
) -> ProviderModelRead:
    ws_id = _require_workspace(workspace_id)
    await ws_svc.ensure_admin(db, workspace_id=ws_id, identity_id=identity_id)
    provider = await svc.get_or_404(db, provider_id, workspace_id=ws_id)
    pm = await svc.add_manual_model(
        db,
        provider=provider,
        model=body.model,
        label=body.label,
        family=body.family,
        context_window=body.context_window,
        enabled=body.enabled,
    )
    await db.commit()
    return ProviderModelRead.model_validate(pm)


@router.patch("/{provider_id}/models/{model_id}", response_model=ProviderModelRead)
async def update_provider_model(
    provider_id: uuid.UUID,
    model_id: uuid.UUID,
    body: ProviderModelUpdate,
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
) -> ProviderModelRead:
    ws_id = _require_workspace(workspace_id)
    await ws_svc.ensure_admin(db, workspace_id=ws_id, identity_id=identity_id)
    provider = await svc.get_or_404(db, provider_id, workspace_id=ws_id)
    pm = await svc.get_provider_model(db, model_id, provider_id=provider.id)
    await svc.update_provider_model(
        db,
        pm=pm,
        enabled=body.enabled,
        label=body.label,
        recommended=body.recommended,
        context_window=body.context_window,
        capabilities=body.capabilities,
        sort_order=body.sort_order,
        metadata_json=body.metadata_json,
    )
    await db.commit()
    return ProviderModelRead.model_validate(pm)


@router.get(
    "/{provider_id}/models/{model_id}/profile",
    response_model=ResolvedReasoningProfile,
)
async def get_resolved_model_profile(
    provider_id: uuid.UUID,
    model_id: uuid.UUID,
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
) -> ResolvedReasoningProfile:
    """Return the model's **effective** reasoning profile.

    Merges ``BUILTIN_PROFILES`` with ``provider_models.metadata_json``
    so the operator dialog opens pre-filled with the values that
    actually drive the runner — instead of showing all-false defaults
    whenever the row inherits a builtin (e.g. ``qwen3*``,
    ``deepseek-reasoner``) without an explicit DB override.
    """
    ws_id = _require_workspace(workspace_id)
    await ws_svc.ensure_member_access(db, workspace_id=ws_id, identity_id=identity_id)
    provider = await svc.get_or_404(db, provider_id, workspace_id=ws_id)
    pm = await svc.get_provider_model(db, model_id, provider_id=provider.id)
    profile = resolve_profile(
        provider_kind=str(provider.kind),
        model_name=pm.model,
        db_metadata=pm.metadata_json,
    )
    has_db_override = bool((pm.metadata_json or {}).get("profile"))
    if has_db_override:
        source = "override"
    elif _match_builtin(str(provider.kind), pm.model) is not None:
        source = "builtin"
    else:
        source = "default"
    return ResolvedReasoningProfile(
        supported=profile.reasoning.supported,
        hybrid=profile.reasoning.hybrid,
        default=profile.reasoning.default,
        tool_call_safe=profile.reasoning.tool_call_safe,
        supports_effort=profile.reasoning.supports_effort,
        source=source,
        preferred_effort=profile.reasoning.preferred_effort,
        flash_alternative=profile.flash_alternative,
        has_db_override=has_db_override,
    )


@router.delete(
    "/{provider_id}/models/{model_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_provider_model(
    provider_id: uuid.UUID,
    model_id: uuid.UUID,
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
) -> None:
    ws_id = _require_workspace(workspace_id)
    await ws_svc.ensure_admin(db, workspace_id=ws_id, identity_id=identity_id)
    provider = await svc.get_or_404(db, provider_id, workspace_id=ws_id)
    pm = await svc.get_provider_model(db, model_id, provider_id=provider.id)
    await svc.delete_provider_model(db, pm=pm)
    await db.commit()


@router.post(
    "/{provider_id}/models:reorder",
    response_model=list[ProviderModelRead],
)
async def reorder_provider_models(
    provider_id: uuid.UUID,
    body: ProviderModelReorderRequest,
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
) -> list[ProviderModelRead]:
    ws_id = _require_workspace(workspace_id)
    await ws_svc.ensure_admin(db, workspace_id=ws_id, identity_id=identity_id)
    provider = await svc.get_or_404(db, provider_id, workspace_id=ws_id)
    rows = await svc.reorder_provider_models(db, provider=provider, ordered_ids=body.ordered_ids)
    await db.commit()
    return [ProviderModelRead.model_validate(r) for r in rows]


# ─── Discover ────────────────────────────────────────────────────


@router.post("/{provider_id}/discover", response_model=DiscoverResponse)
async def discover(
    provider_id: uuid.UUID,
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
) -> DiscoverResponse:
    ws_id = _require_workspace(workspace_id)
    await ws_svc.ensure_admin(db, workspace_id=ws_id, identity_id=identity_id)
    provider = await svc.get_or_404(db, provider_id, workspace_id=ws_id)
    payload = await svc.discover_models(db, provider=provider)
    return DiscoverResponse(
        kind=payload["kind"],
        source=payload["source"],
        discovered=[DiscoveredModel(**row) for row in payload["discovered"]],
        existing_ids=payload["existing_ids"],
        error=payload["error"],
    )


@router.post("/{provider_id}/test", response_model=ProviderTestResponse)
async def test_connectivity(
    provider_id: uuid.UUID,
    body: ProviderTestRequest,
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
) -> ProviderTestResponse:
    """Probe upstream credentials + base_url without spending tokens."""
    ws_id = _require_workspace(workspace_id)
    await ws_svc.ensure_admin(db, workspace_id=ws_id, identity_id=identity_id)
    provider = await svc.get_or_404(db, provider_id, workspace_id=ws_id)
    payload = await svc.test_connectivity(db, provider=provider, model=body.model)
    return ProviderTestResponse(**payload)


@router.post("/{provider_id}/discover/apply", response_model=list[ProviderModelRead])
async def apply_discover(
    provider_id: uuid.UUID,
    body: DiscoverApplyRequest,
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
) -> list[ProviderModelRead]:
    ws_id = _require_workspace(workspace_id)
    await ws_svc.ensure_admin(db, workspace_id=ws_id, identity_id=identity_id)
    provider = await svc.get_or_404(db, provider_id, workspace_id=ws_id)
    rows = await svc.apply_discovered_models(
        db, provider=provider, model_ids=body.model_ids, replace=body.replace
    )
    await db.commit()
    return [ProviderModelRead.model_validate(r) for r in rows]
