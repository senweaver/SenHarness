"""Search provider repository."""

from __future__ import annotations

from app.db.models.search_provider import SearchProvider
from app.db.repository import AsyncRepository


class SearchProviderRepository(AsyncRepository[SearchProvider]):
    model = SearchProvider
