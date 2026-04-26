"""Model provider service: CRUD + key ingestion via Vault."""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFound
from app.db.models.model_provider import ModelKey, ModelProvider, ProviderKind
from app.db.models.vault import VaultItemKind
from app.db.repository import AsyncRepository
from app.repositories.provider import ModelKeyRepository, ModelProviderRepository
from app.services import vault as vault_svc


async def list_providers(
    session: AsyncSession, *, workspace_id: uuid.UUID
) -> list[ModelProvider]:
    repo = ModelProviderRepository(session)
    rows = await repo.list(workspace_id=workspace_id, limit=200)
    return list(rows)


async def get_or_404(
    session: AsyncSession, provider_id: uuid.UUID, *, workspace_id: uuid.UUID
) -> ModelProvider:
    repo = ModelProviderRepository(session)
    obj = await repo.get(provider_id)
    if obj is None or obj.workspace_id != workspace_id:
        raise NotFound("provider_not_found", code="provider.not_found")
    return obj


async def create_provider(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    owner_identity_id: uuid.UUID | None,
    kind: ProviderKind,
    name: str,
    base_url: str | None = None,
    default_model: str | None = None,
    enabled: bool = True,
    metadata_json: dict | None = None,
    api_key: str | None = None,
) -> ModelProvider:
    prov_repo = ModelProviderRepository(session)
    provider = await prov_repo.create(
        workspace_id=workspace_id,
        kind=kind,
        name=name,
        base_url=base_url,
        default_model=default_model,
        enabled=enabled,
        metadata_json=metadata_json or {},
    )
    if api_key:
        vault_item = await vault_svc.create_secret(
            session,
            workspace_id=workspace_id,
            owner_identity_id=owner_identity_id,
            name=f"provider/{provider.id}/default",
            plaintext=api_key,
            kind=VaultItemKind.API_KEY,
            metadata={"provider_id": str(provider.id)},
        )
        key_repo: AsyncRepository[ModelKey] = ModelKeyRepository(session)
        await key_repo.create(
            provider_id=provider.id,
            name="default",
            vault_item_id=vault_item.id,
        )
    return provider


async def update_provider(
    session: AsyncSession,
    *,
    provider: ModelProvider,
    name: str | None = None,
    base_url: str | None = None,
    default_model: str | None = None,
    enabled: bool | None = None,
    metadata_json: dict | None = None,
    api_key: str | None = None,
) -> ModelProvider:
    prov_repo = ModelProviderRepository(session)
    updates: dict = {}
    if name is not None:
        updates["name"] = name
    if base_url is not None:
        updates["base_url"] = base_url
    if default_model is not None:
        updates["default_model"] = default_model
    if enabled is not None:
        updates["enabled"] = enabled
    if metadata_json is not None:
        updates["metadata_json"] = metadata_json
    if updates:
        await prov_repo.update(provider, **updates)

    if api_key:
        # Overwrite primary key or create one.
        key_repo = ModelKeyRepository(session)
        key = await key_repo.get_by(provider_id=provider.id, name="default")
        if key and key.vault_item_id:
            from app.db.models.vault import VaultItem

            vault_repo: AsyncRepository[VaultItem] = AsyncRepository(session, VaultItem)
            existing_item = await vault_repo.get(key.vault_item_id)
            if existing_item is not None:
                await vault_svc.replace_secret(session, item=existing_item, plaintext=api_key)
        else:
            vault_item = await vault_svc.create_secret(
                session,
                workspace_id=provider.workspace_id,
                owner_identity_id=None,
                name=f"provider/{provider.id}/default",
                plaintext=api_key,
            )
            await key_repo.create(
                provider_id=provider.id, name="default", vault_item_id=vault_item.id
            )

    return provider


async def delete_provider(
    session: AsyncSession, *, provider: ModelProvider
) -> None:
    await ModelProviderRepository(session).soft_delete(provider)


async def provider_has_key(session: AsyncSession, *, provider_id: uuid.UUID) -> bool:
    repo = ModelKeyRepository(session)
    return await repo.exists(provider_id=provider_id, enabled=True)
