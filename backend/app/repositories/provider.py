"""Model provider + key + per-provider model repositories."""

from __future__ import annotations

from app.db.models.model_provider import (
    ModelKey,
    ModelProvider,
    ModelRoute,
    ProviderModel,
)
from app.db.repository import AsyncRepository


class ModelProviderRepository(AsyncRepository[ModelProvider]):
    model = ModelProvider


class ModelKeyRepository(AsyncRepository[ModelKey]):
    model = ModelKey


class ModelRouteRepository(AsyncRepository[ModelRoute]):
    model = ModelRoute


class ProviderModelRepository(AsyncRepository[ProviderModel]):
    model = ProviderModel
