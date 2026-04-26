"""Business logic for Backend Adapter CRUD.

Combines the three moving pieces — the ``backend_adapters`` row, the Vault
item holding the plaintext API key, and the SHA-256 index used by the gateway
auth hot path — so that route handlers stay readable.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFound
from app.db.models.backend_adapter import (
    BackendAdapter,
    BackendAdapterHealth,
    BackendAdapterKind,
)
from app.db.models.vault import VaultItemKind
from app.repositories.backend_adapter import BackendAdapterRepository
from app.services import vault as vault_svc
from app.services.gateway import generate_api_key, hash_api_key

log = logging.getLogger(__name__)


async def create_adapter(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    created_by: uuid.UUID,
    name: str,
    kind: BackendAdapterKind,
    endpoint: str | None,
    metadata_json: dict[str, Any] | None,
) -> tuple[BackendAdapter, str]:
    """Create + return ``(adapter, plaintext_api_key)``. The caller surfaces
    the key to the user once — we never store or return it again."""

    raw_key = generate_api_key()
    digest = hash_api_key(raw_key)

    vault_item = await vault_svc.create_secret(
        session,
        workspace_id=workspace_id,
        owner_identity_id=created_by,
        name=f"openclaw:{name}",
        plaintext=raw_key,
        kind=VaultItemKind.API_KEY,
        metadata={"purpose": "openclaw-adapter", "adapter_name": name},
    )

    adapter = await BackendAdapterRepository(session).create(
        workspace_id=workspace_id,
        name=name,
        kind=kind,
        endpoint=endpoint,
        api_key_vault_id=vault_item.id,
        api_key_hash=digest,
        capabilities_json={},
        health_status=BackendAdapterHealth.UNKNOWN,
        enabled=True,
        metadata_json=metadata_json or {},
        created_by=created_by,
    )
    return adapter, raw_key


async def rotate_api_key(
    session: AsyncSession, *, adapter: BackendAdapter
) -> tuple[BackendAdapter, str]:
    """Generate a fresh key, re-encrypt the Vault item, flip the hash atomically."""

    raw_key = generate_api_key()
    digest = hash_api_key(raw_key)

    if adapter.api_key_vault_id is not None:
        vault_item = await session.get(
            _VaultItemProxy().model, adapter.api_key_vault_id
        )
        if vault_item is not None:
            await vault_svc.replace_secret(
                session, item=vault_item, plaintext=raw_key
            )

    updated = await BackendAdapterRepository(session).update(
        adapter, api_key_hash=digest
    )
    return updated, raw_key


async def delete_adapter(
    session: AsyncSession, *, adapter: BackendAdapter
) -> None:
    """Soft-delete the adapter. We also break the api_key_hash index so a
    rotated key from a deleted adapter can never authenticate."""

    repo = BackendAdapterRepository(session)
    # Null out hash first so a parallel poll holding the old cache fails safe.
    # Use a unique sentinel per soft-delete to avoid collision with other
    # deleted rows that still carry api_key_hash NOT NULL.
    sentinel = f"deleted:{uuid.uuid4().hex}"
    await repo.update(adapter, api_key_hash=sentinel, enabled=False)
    await repo.soft_delete(adapter)


async def get_or_404(
    session: AsyncSession,
    *,
    adapter_id: uuid.UUID,
    workspace_id: uuid.UUID,
) -> BackendAdapter:
    adapter = await BackendAdapterRepository(session).get(adapter_id)
    if (
        adapter is None
        or adapter.workspace_id != workspace_id
        or adapter.deleted_at is not None
    ):
        raise NotFound("adapter_not_found", code="backend.not_found")
    return adapter


async def ping_endpoint(adapter: BackendAdapter) -> tuple[BackendAdapterHealth, str]:
    """Best-effort live check — GET the adapter ``endpoint`` and infer status.

    Callers use this to stamp ``health_status``. The check deliberately tries
    a lightweight ``GET /`` rather than a POST because most remote workers run
    their own health route at the root.
    """

    if not adapter.endpoint:
        # Without an endpoint, all we can report is "has it polled recently?"
        if adapter.last_seen_at is None:
            return BackendAdapterHealth.UNKNOWN, "no endpoint, never seen"
        return BackendAdapterHealth.HEALTHY, "no endpoint, last_seen_at populated"

    try:
        async with httpx.AsyncClient(timeout=5.0, follow_redirects=True) as c:
            r = await c.get(adapter.endpoint)
        if 200 <= r.status_code < 500:
            return BackendAdapterHealth.HEALTHY, f"HTTP {r.status_code}"
        return BackendAdapterHealth.DEGRADED, f"HTTP {r.status_code}"
    except httpx.RequestError as e:
        return BackendAdapterHealth.DOWN, f"{type(e).__name__}: {e}"


# ─── Private helpers ──────────────────────────────────────
class _VaultItemProxy:
    """Lazy import of the model class to avoid circular imports."""

    @property
    def model(self):
        from app.db.models.vault import VaultItem

        return VaultItem


__all__ = [
    "create_adapter",
    "delete_adapter",
    "get_or_404",
    "ping_endpoint",
    "rotate_api_key",
]
