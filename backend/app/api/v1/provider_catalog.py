"""GET /api/v1/provider-catalog — static catalog of supported providers.

Public read-only endpoint authenticated users can list. The response is
generated from `app.agents.kernels.provider_catalog` which reflects on the
installed `pydantic_ai.providers` package.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.agents.kernels.provider_catalog import iter_catalog
from app.api.deps import CurrentIdentityId
from app.schemas.provider_catalog import ProviderCatalogEntryRead

router = APIRouter()


@router.get("", response_model=list[ProviderCatalogEntryRead])
async def get_catalog(_: CurrentIdentityId) -> list[ProviderCatalogEntryRead]:
    """Return the full provider catalog (~30 entries)."""
    return [
        ProviderCatalogEntryRead(
            kind=p.kind,
            display_name=p.display_name,
            display_name_zh=p.display_name_zh,
            family=p.family,
            country_code=p.country_code,
            credential_type=p.credential_type,
            description=p.description,
            description_zh=p.description_zh,
            default_base_url=p.default_base_url,
            api_key_env=p.api_key_env,
            supports_discover=p.supports_discover,
            signup_url=p.signup_url,
            builtin_models=[m for m in p.builtin_models],
        )
        for p in iter_catalog()
    ]
