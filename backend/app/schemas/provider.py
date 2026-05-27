"""Model provider DTOs."""

from __future__ import annotations

import uuid

from pydantic import Field, field_validator

from app.schemas._base import ORMModel, Timestamped


def _validate_kind(value: str) -> str:
    """Whitelist `kind` against the SenHarness catalog.

    Imported lazily so the schema module doesn't pull in pydantic-ai at import
    time during eager Pydantic schema generation.
    """
    from app.agents.kernels.provider_catalog import is_known_kind

    if not value:
        raise ValueError("kind is required")
    cleaned = value.strip().lower()
    if not is_known_kind(cleaned):
        raise ValueError(f"unknown provider kind: {value!r}")
    return cleaned


def _validate_credential_type(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.strip().lower()
    if cleaned not in {"api_key", "oauth_token", "custom_headers"}:
        raise ValueError(f"unknown credential_type: {value!r}")
    return cleaned


class ProviderCreate(ORMModel):
    kind: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=128)
    base_url: str | None = None
    default_model: str | None = None
    api_key: str | None = Field(
        default=None,
        description="Plaintext API key. Stored envelope-encrypted in Vault; never returned.",
    )
    enabled: bool = True
    credential_type: str = Field(default="api_key", max_length=32)
    country_code: str | None = Field(default=None, max_length=8)
    metadata_json: dict = Field(default_factory=dict)

    @field_validator("kind", mode="before")
    @classmethod
    def _check_kind(cls, v: str) -> str:
        return _validate_kind(v)

    @field_validator("credential_type", mode="before")
    @classmethod
    def _check_credential_type(cls, v: str | None) -> str:
        return _validate_credential_type(v) or "api_key"


class ProviderUpdate(ORMModel):
    name: str | None = None
    base_url: str | None = None
    default_model: str | None = None
    api_key: str | None = None
    enabled: bool | None = None
    credential_type: str | None = None
    country_code: str | None = None
    metadata_json: dict | None = None

    @field_validator("credential_type", mode="before")
    @classmethod
    def _check_credential_type(cls, v: str | None) -> str | None:
        return _validate_credential_type(v)


class ProviderRead(Timestamped):
    workspace_id: uuid.UUID
    kind: str
    name: str
    base_url: str | None
    default_model: str | None
    enabled: bool
    credential_type: str = "api_key"
    country_code: str | None = None
    metadata_json: dict
    sort_order: int = 0
    has_key: bool = False
    # Last 4 characters of the stored key (or ``None`` when no key is
    # configured / a legacy row hasn't been re-saved since the hint
    # column was introduced). The plaintext key itself never leaves the
    # backend — this is the **only** key material the frontend gets.
    api_key_hint: str | None = None


class ProviderReorderRequest(ORMModel):
    """Persist drag-to-reorder for the workspace's provider list.

    ``ordered_ids`` is the full set of provider rows the caller wants
    ranked. Each id must belong to the active workspace; missing ids
    keep their current ``sort_order`` so a partial submit doesn't
    accidentally renumber half the list.
    """

    ordered_ids: list[uuid.UUID] = Field(default_factory=list)


# ─── Provider models (the per-provider model catalog) ─────────────


class ProviderModelRead(Timestamped):
    provider_id: uuid.UUID
    model: str
    label: str | None
    family: str | None
    recommended: bool
    enabled: bool
    context_window: int | None
    source: str
    sort_order: int = 0
    metadata_json: dict


class ProviderModelManualCreate(ORMModel):
    """Input for ``POST /providers/{id}/models`` — operator-typed model."""

    model: str = Field(min_length=1, max_length=128)
    label: str | None = Field(default=None, max_length=200)
    family: str | None = Field(default=None, max_length=32)
    context_window: int | None = None
    enabled: bool = True


class ProviderModelUpdate(ORMModel):
    """Input for ``PATCH /providers/{id}/models/{model_id}``."""

    enabled: bool | None = None
    label: str | None = Field(default=None, max_length=200)
    recommended: bool | None = None
    context_window: int | None = None
    capabilities: list[str] | None = None
    sort_order: int | None = None
    # Sparse JSON patch — only the keys present (typically just
    # ``profile``) get merged onto the stored row. Pass
    # ``metadata_json: {"profile": null}`` to clear the per-row
    # reasoning override and fall back to ``BUILTIN_PROFILES``.
    metadata_json: dict | None = None


class ProviderModelReorderRequest(ORMModel):
    """Persist drag-to-reorder for a provider's full model list.

    ``ordered_ids`` accepts each row as either:
      - a ``ProviderModel.id`` UUID (already-persisted row), or
      - the upstream ``model`` identifier (e.g. ``"qwen3.5-plus"``).

    Catalog entries that aren't yet persisted are lazily created with
    ``enabled=False, source="static"`` so the user can sort the entire
    builtin list — including rows they haven't enabled yet — without a
    separate two-step "apply then sort" flow.
    """

    ordered_ids: list[str] = Field(default_factory=list)


class DiscoveredModel(ORMModel):
    """One row in the discover response."""

    model: str
    label: str | None = None
    family: str | None = None
    recommended: bool = False
    in_db: bool = False
    context_window: int | None = None


class DiscoverResponse(ORMModel):
    kind: str
    source: str  # "remote" | "static"
    discovered: list[DiscoveredModel]
    existing_ids: list[str]
    error: str | None = None


class DiscoverApplyRequest(ORMModel):
    model_ids: list[str] = Field(default_factory=list)
    replace: bool = False


class ProviderTestRequest(ORMModel):
    model: str | None = Field(default=None, max_length=128)


class ProviderTestResponse(ORMModel):
    """Outcome of a connectivity probe against the upstream provider."""

    ok: bool
    latency_ms: int | None = None
    detail: str | None = None
    error: str | None = None


class ResolvedReasoningProfile(ORMModel):
    """Effective reasoning profile for one model row.

    Backend merges the static builtin profile with whatever override the
    workspace stored on ``provider_models.metadata_json["profile"]`` —
    the dialog opens with the **resolved** values so operators see what
    actually drives the runner instead of an all-false placeholder.
    """

    supported: bool
    hybrid: bool
    default: str
    tool_call_safe: bool
    # Operator-configured effort knob. ``None`` means "no preference —
    # keep whatever the builtin enable payload already sets". Values
    # follow the OpenAI o-series vocabulary (``minimal`` / ``low`` /
    # ``medium`` / ``high``) which the runner re-uses for every
    # provider that accepts a reasoning_effort string.
    preferred_effort: str | None = None
    flash_alternative: str | None = None
    has_db_override: bool = False
