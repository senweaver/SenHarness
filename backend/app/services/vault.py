"""Vault service — store / read / rotate secrets with envelope encryption."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.vault import VaultItem, VaultItemKind
from app.db.repository import AsyncRepository
from app.security.crypto import Sealed, open_sealed, seal_str
from app.security.keyring import get_keyring


async def create_secret(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    owner_identity_id: uuid.UUID | None,
    name: str,
    plaintext: str,
    kind: VaultItemKind = VaultItemKind.API_KEY,
    metadata: dict[str, Any] | None = None,
    required_approval: bool = False,
) -> VaultItem:
    kr = get_keyring()
    sealed = seal_str(plaintext, keyring=kr)
    repo: AsyncRepository[VaultItem] = AsyncRepository(session, VaultItem)
    item = await repo.create(
        workspace_id=workspace_id,
        owner_identity_id=owner_identity_id,
        name=name,
        kind=kind,
        ciphertext=sealed.ciphertext,
        wrapped_dek=sealed.wrapped_dek,
        kek_version=sealed.kek_version,
        metadata_json=metadata or {},
        required_approval=required_approval,
    )
    return item


async def reveal_secret(item: VaultItem) -> str:
    kr = get_keyring()
    sealed = Sealed(
        ciphertext=item.ciphertext,
        wrapped_dek=item.wrapped_dek,
        kek_version=item.kek_version,
    )
    data = open_sealed(sealed, keyring=kr)
    return data.decode()


async def replace_secret(
    session: AsyncSession, *, item: VaultItem, plaintext: str
) -> VaultItem:
    kr = get_keyring()
    sealed = seal_str(plaintext, keyring=kr)
    repo: AsyncRepository[VaultItem] = AsyncRepository(session, VaultItem)
    return await repo.update(
        item,
        ciphertext=sealed.ciphertext,
        wrapped_dek=sealed.wrapped_dek,
        kek_version=sealed.kek_version,
    )
