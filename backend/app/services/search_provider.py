"""Search provider service: CRUD + key ingestion via Vault.

Used by `app.agents.tools.web_search` to look up the workspace's preferred
search backend at runtime — no `.env` reads.
"""

from __future__ import annotations

import logging
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFound
from app.db.models.search_provider import SearchProvider
from app.db.models.vault import VaultItemKind
from app.db.repository import AsyncRepository
from app.repositories.search_provider import SearchProviderRepository
from app.services import vault as vault_svc

log = logging.getLogger(__name__)


# Static catalog (mirrors the LLM provider catalog but kept tiny — search
# APIs are far fewer and don't need pydantic-ai reflection).
SEARCH_CATALOG: list[dict] = [
    {
        "kind": "tavily",
        "display_name": "Tavily",
        "display_name_zh": "Tavily \u641c\u7d22",
        "description": "AI-friendly search with built-in answer extraction.",
        "description_zh": "\u9762\u5411 AI \u4f18\u5316\u7684\u641c\u7d22\uff0c\u5185\u5efa\u7b54\u6848\u63d0\u53d6\u3002",
        "default_base_url": "https://api.tavily.com",
        "needs_key": True,
    },
    {
        "kind": "serpapi",
        "display_name": "SerpAPI",
        "display_name_zh": "SerpAPI",
        "description": "Google SERP via SerpAPI.",
        "description_zh": "\u901a\u8fc7 SerpAPI \u83b7\u53d6 Google \u641c\u7d22\u7ed3\u679c\u3002",
        "default_base_url": "https://serpapi.com",
        "needs_key": True,
    },
    {
        "kind": "brave",
        "display_name": "Brave Search",
        "display_name_zh": "Brave Search",
        "description": "Independent web index by Brave.",
        "description_zh": "Brave \u81ea\u5efa\u7684\u72ec\u7acb\u7d22\u5f15\u3002",
        "default_base_url": "https://api.search.brave.com",
        "needs_key": True,
    },
    {
        "kind": "jina",
        "display_name": "Jina Reader",
        "display_name_zh": "Jina Reader",
        "description": "Jina AI reader / search.",
        "description_zh": "Jina AI \u9605\u8bfb / \u641c\u7d22\u3002",
        "default_base_url": "https://s.jina.ai",
        "needs_key": True,
    },
    {
        "kind": "exa",
        "display_name": "Exa",
        "display_name_zh": "Exa",
        "description": "Neural-search engine for AI agents.",
        "description_zh": "\u9762\u5411 AI Agent \u7684\u795e\u7ecf\u641c\u7d22\u5f15\u64ce\u3002",
        "default_base_url": "https://api.exa.ai",
        "needs_key": True,
    },
    {
        "kind": "duckduckgo",
        "display_name": "DuckDuckGo",
        "display_name_zh": "DuckDuckGo",
        "description": "Free fallback (no key required).",
        "description_zh": "\u514d\u8d39\u5151\u5e95\uff08\u65e0\u9700 Key\uff09\u3002",
        "default_base_url": None,
        "needs_key": False,
    },
]


def get_catalog() -> list[dict]:
    return list(SEARCH_CATALOG)


# ─── CRUD ────────────────────────────────────────────────


async def list_providers(
    session: AsyncSession, *, workspace_id: uuid.UUID
) -> list[SearchProvider]:
    repo = SearchProviderRepository(session)
    return list(
        await repo.list(
            workspace_id=workspace_id,
            order_by=SearchProvider.priority.asc(),
            limit=50,
        )
    )


async def get_or_404(
    session: AsyncSession, provider_id: uuid.UUID, *, workspace_id: uuid.UUID
) -> SearchProvider:
    repo = SearchProviderRepository(session)
    obj = await repo.get(provider_id)
    if obj is None or obj.workspace_id != workspace_id:
        raise NotFound("search_provider_not_found", code="search_provider.not_found")
    return obj


async def create_provider(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    owner_identity_id: uuid.UUID | None,
    kind: str,
    name: str,
    base_url: str | None = None,
    enabled: bool = True,
    priority: int = 100,
    metadata_json: dict | None = None,
    api_key: str | None = None,
) -> SearchProvider:
    repo = SearchProviderRepository(session)
    vault_item_id = None
    if api_key:
        vault_item = await vault_svc.create_secret(
            session,
            workspace_id=workspace_id,
            owner_identity_id=owner_identity_id,
            name=f"search/{kind}/default",
            plaintext=api_key,
            kind=VaultItemKind.API_KEY,
            metadata={"search_kind": kind},
        )
        vault_item_id = vault_item.id
    return await repo.create(
        workspace_id=workspace_id,
        kind=kind.strip().lower(),
        name=name,
        base_url=base_url,
        enabled=enabled,
        priority=priority,
        vault_item_id=vault_item_id,
        metadata_json=metadata_json or {},
    )


async def update_provider(
    session: AsyncSession,
    *,
    provider: SearchProvider,
    name: str | None = None,
    base_url: str | None = None,
    enabled: bool | None = None,
    priority: int | None = None,
    metadata_json: dict | None = None,
    api_key: str | None = None,
) -> SearchProvider:
    repo = SearchProviderRepository(session)
    updates: dict = {}
    if name is not None:
        updates["name"] = name
    if base_url is not None:
        updates["base_url"] = base_url
    if enabled is not None:
        updates["enabled"] = enabled
    if priority is not None:
        updates["priority"] = priority
    if metadata_json is not None:
        updates["metadata_json"] = metadata_json
    if updates:
        await repo.update(provider, **updates)

    if api_key:
        if provider.vault_item_id:
            from app.db.models.vault import VaultItem

            vault_repo: AsyncRepository[VaultItem] = AsyncRepository(session, VaultItem)
            existing_item = await vault_repo.get(provider.vault_item_id)
            if existing_item is not None:
                await vault_svc.replace_secret(
                    session, item=existing_item, plaintext=api_key
                )
        else:
            vault_item = await vault_svc.create_secret(
                session,
                workspace_id=provider.workspace_id,
                owner_identity_id=None,
                name=f"search/{provider.kind}/default",
                plaintext=api_key,
            )
            await repo.update(provider, vault_item_id=vault_item.id)

    return provider


async def delete_provider(
    session: AsyncSession, *, provider: SearchProvider
) -> None:
    await SearchProviderRepository(session).soft_delete(provider)


async def provider_has_key(
    session: AsyncSession, *, provider: SearchProvider
) -> bool:
    return provider.vault_item_id is not None


# ─── Used by web_search tool ─────────────────────────────


async def resolve_search_key(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    kind: str,
) -> tuple[str | None, str | None]:
    """Return ``(api_key, base_url)`` for the workspace's enabled provider.

    Returns ``(None, None)`` when no row exists or the key isn't unwrappable.
    """
    repo = SearchProviderRepository(session)
    provider = await repo.get_by(workspace_id=workspace_id, kind=kind, enabled=True)
    if provider is None:
        return None, None
    api_key: str | None = None
    if provider.vault_item_id is not None:
        from app.db.models.vault import VaultItem

        vault_repo: AsyncRepository[VaultItem] = AsyncRepository(session, VaultItem)
        item = await vault_repo.get(provider.vault_item_id)
        if item is not None:
            try:
                api_key = await vault_svc.reveal_secret(item)
            except Exception as e:  # pragma: no cover
                log.warning(
                    "vault reveal failed for search provider %s: %s",
                    provider.id,
                    e,
                )
                api_key = None
    return api_key, provider.base_url
