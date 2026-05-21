"""DTOs for the provider catalog endpoint."""

from __future__ import annotations

from typing import Any

from pydantic import Field

from app.schemas._base import ORMModel


class ProviderCatalogEntryRead(ORMModel):
    """A single catalog row sent to the frontend.

    Mirrors :class:`app.agents.kernels.provider_catalog.CatalogPayload` but
    typed as a Pydantic model so FastAPI/OpenAPI can describe the response.
    """

    kind: str
    display_name: str
    display_name_zh: str
    family: str
    country_code: str | None = None
    credential_type: str
    description: str = ""
    description_zh: str = ""
    default_base_url: str | None = None
    api_key_env: str | None = None
    supports_discover: bool = False
    signup_url: str = ""
    aliases: list[str] = Field(default_factory=list)
    builtin_models: list[dict[str, Any]] = Field(default_factory=list)
