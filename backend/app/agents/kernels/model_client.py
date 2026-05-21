"""Model client resolver — DB + Vault only, no `.env` fallback.

Resolution order:
  1. Explicit `model_override` on the RunRequest (e.g. `"openai:gpt-4o-mini"`).
     The Sessions WS path already folds the per-call ``data.model`` and the
     caller's ``Identity.profile_json.chat_model_prefs`` into this single
     override string before reaching the kernel.
  2. ``agents.default_model`` — per-agent default (``"provider:model"``).
     The kind must be enabled in the workspace; if not, we fall through
     rather than failing.
  3. First enabled `model_providers` row in the workspace with a resolvable
     vault-backed key, ordered by ``sort_order`` then ``created_at``.

If none of the above produces a working `ResolvedModel`, the agent stream
falls back to the placeholder reason ``no_model_configured`` so the UI can
prompt the user to head to Settings → Providers.

All provider metadata (base_url, model_profile, env var names) lives in
`pydantic_ai.providers` — we just reflect on it via `infer_provider_class()`
and add the SenHarness display layer in `provider_catalog`.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass

from app.agents.kernels.provider_catalog import (
    default_base_url_for,
    family_of,
    get_entry,
)

log = logging.getLogger(__name__)


@dataclass(slots=True)
class ResolvedModel:
    """Lightweight descriptor with everything needed to build a pydantic-ai Model."""

    provider_kind: str         # "openai" | "anthropic" | ... | "mock"
    model_name: str            # e.g. "gpt-4o-mini"
    api_key: str | None        # plaintext (in-memory only)
    base_url: str | None = None
    extra: dict | None = None
    source: str = "db"         # "db" | "override" | "mock"


def parse_override(override: str) -> ResolvedModel | None:
    """Parse a `"provider:model"` or `"provider:model@base_url"` override string."""
    if not override or ":" not in override:
        return None
    left, sep, right = override.partition(":")
    if not sep:
        return None
    model_name = right
    base_url = None
    if "@" in right:
        model_name, _, base_url = right.partition("@")
    return ResolvedModel(
        provider_kind=left.strip(),
        model_name=model_name.strip(),
        api_key=None,
        base_url=base_url.strip() if base_url else None,
        source="override",
    )


async def resolve_for_agent(
    *, workspace_id: uuid.UUID, agent_id: uuid.UUID, override: str | None = None
) -> ResolvedModel | None:
    """High-level resolver.

    Order of precedence:
      1. Explicit ``override`` (per-turn, or the resolved user pref).
      2. ``agents.default_model`` — the agent's own pick (M2.5.8).
         When the kind isn't enabled in the workspace we fall through
         to the workspace default rather than failing the turn.
      3. First enabled workspace provider (``_resolve_from_db``)
         ordered by ``sort_order``, then ``created_at``.

    ``agent_id`` is consulted for step 2 only; the legacy P2
    ``model_route_id`` field remains a no-op placeholder.
    """
    if override:
        parsed = parse_override(override)
        if parsed is not None:
            if parsed.api_key is None:
                db_for_kind = await _resolve_from_db(
                    workspace_id=workspace_id, prefer_kind=parsed.provider_kind
                )
                if db_for_kind is not None:
                    parsed.api_key = db_for_kind.api_key
                    parsed.base_url = parsed.base_url or db_for_kind.base_url
            return parsed

    agent_default = await _read_agent_default_model(agent_id)
    if agent_default:
        parsed = parse_override(agent_default)
        if parsed is not None:
            db_for_kind = await _resolve_from_db(
                workspace_id=workspace_id, prefer_kind=parsed.provider_kind
            )
            if db_for_kind is not None:
                parsed.api_key = db_for_kind.api_key
                parsed.base_url = parsed.base_url or db_for_kind.base_url
                parsed.source = "agent_default"
                return parsed
            # Agent has a default but its provider_kind isn't enabled in
            # the workspace — fall through to whatever IS enabled rather
            # than failing the turn.

    return await _resolve_from_db(workspace_id=workspace_id)


async def _read_agent_default_model(agent_id: uuid.UUID) -> str | None:
    """Fetch ``agents.default_model`` for the agent, or ``None``.

    Kept narrow on purpose: the resolver only needs one column, so a
    single ``SELECT`` keeps the hot path one round trip.
    """
    try:
        from sqlalchemy import select

        from app.db.models.agent import Agent
        from app.db.session import get_session_factory
    except ImportError:  # pragma: no cover
        return None

    factory = get_session_factory()
    async with factory() as session:
        result = await session.execute(
            select(Agent.default_model).where(Agent.id == agent_id)
        )
        row = result.first()
    if row is None:
        return None
    value = row[0]
    if not isinstance(value, str) or not value.strip():
        return None
    return value.strip()


async def resolve_for_workspace(
    *, workspace_id: uuid.UUID, kind: str | None = None
) -> ResolvedModel | None:
    """Public helper for non-agent paths (embedder, multimedia tools).

    Returns the first enabled provider for ``workspace_id``. If ``kind`` is
    given, prefers that kind first; otherwise falls back to whatever is
    available.
    """
    return await _resolve_from_db(workspace_id=workspace_id, prefer_kind=kind)


@dataclass(slots=True)
class ResolvedEmbedder:
    """Pick of an embedding-capable workspace provider + chosen model."""

    provider_id: uuid.UUID
    provider_kind: str
    embedding_model: str
    api_key: str | None
    base_url: str | None


async def resolve_embedder_for_workspace(
    *, workspace_id: uuid.UUID
) -> ResolvedEmbedder | None:
    """Pick the first enabled workspace provider whose catalog declares
    embedding support.

    Selection rules:
      1. Only providers whose ``kind`` returns at least one
         ``CatalogModel(category="embedding")`` from
         :mod:`app.agents.kernels.model_catalog`.
      2. Ordered by ``sort_order asc, created_at asc`` (same as chat).
      3. Embedding model name is taken from
         ``ModelProvider.metadata_json["embedding_model"]`` when the
         workspace admin pinned one, otherwise from the catalog's
         ``default_embedding_model_for_provider``.

    Returns ``None`` when the workspace has no embedding-capable
    provider configured. Callers MUST treat that as "skip embedding" —
    no hash-fallback, no silent best-effort. Keeping the contract this
    strict is what stops the legacy 404 storm against DeepSeek /
    Moonshot / xAI etc. (chat-only providers).
    """
    try:
        from sqlalchemy import select

        from app.agents.kernels.model_catalog import (
            default_embedding_model_for_provider,
            provider_supports_embeddings,
        )
        from app.db.models.model_provider import ModelKey, ModelProvider
        from app.db.models.vault import VaultItem
        from app.db.session import get_session_factory
        from app.services.vault import reveal_secret
    except ImportError:  # pragma: no cover
        return None

    factory = get_session_factory()
    async with factory() as session:
        stmt = (
            select(ModelProvider, ModelKey, VaultItem)
            .join(ModelKey, ModelKey.provider_id == ModelProvider.id)
            .join(VaultItem, VaultItem.id == ModelKey.vault_item_id)
            .where(
                ModelProvider.workspace_id == workspace_id,
                ModelProvider.enabled.is_(True),
                ModelProvider.deleted_at.is_(None),
                ModelKey.enabled.is_(True),
            )
            .order_by(
                ModelProvider.sort_order.asc(),
                ModelProvider.created_at.asc(),
            )
            .limit(50)
        )
        rows = (await session.execute(stmt)).all()
        if not rows:
            return None

        chosen = None
        for row in rows:
            provider = row[0]
            kind = (
                provider.kind.value
                if hasattr(provider.kind, "value")
                else str(provider.kind)
            )
            if provider_supports_embeddings(kind):
                chosen = row
                break
            # Power-user escape hatch: a workspace admin can pin an
            # ``embedding_model`` on any openai-compatible provider
            # (incl. ``kind="custom"``) and we trust the override even
            # when the catalog declares no embedding SKU for that kind.
            # Non-openai families are excluded so we don't POST openai-
            # shaped requests at anthropic / google base URLs.
            md = provider.metadata_json or {}
            if isinstance(md, dict):
                override = md.get("embedding_model")
                if (
                    isinstance(override, str)
                    and override.strip()
                    and family_of(kind) == "openai-compatible"
                ):
                    chosen = row
                    break
        if chosen is None:
            return None

        provider, _key, vault_item = chosen
        try:
            api_key = await reveal_secret(vault_item)
        except Exception as e:  # pragma: no cover
            log.warning(
                "vault reveal failed for embedding provider %s: %s",
                provider.id,
                e,
            )
            return None

    kind = (
        provider.kind.value
        if hasattr(provider.kind, "value")
        else str(provider.kind)
    )
    override = ""
    md = provider.metadata_json or {}
    if isinstance(md, dict):
        candidate = md.get("embedding_model")
        if isinstance(candidate, str) and candidate.strip():
            override = candidate.strip()
    embedding_model = override or default_embedding_model_for_provider(kind) or ""
    if not embedding_model:
        return None
    base_url = provider.base_url or default_base_url_for(kind)
    return ResolvedEmbedder(
        provider_id=provider.id,
        provider_kind=kind,
        embedding_model=embedding_model,
        api_key=api_key,
        base_url=base_url,
    )


async def _resolve_from_db(
    *, workspace_id: uuid.UUID, prefer_kind: str | None = None
) -> ResolvedModel | None:
    """Look up an enabled provider for the workspace and unwrap its key via Vault.

    If ``prefer_kind`` is set, returns the matching provider when present; else
    returns the oldest enabled provider as the workspace default.
    """
    try:
        from sqlalchemy import select

        from app.db.models.model_provider import ModelKey, ModelProvider
        from app.db.models.vault import VaultItem
        from app.db.session import get_session_factory
        from app.services.vault import reveal_secret
    except ImportError:  # pragma: no cover
        return None

    factory = get_session_factory()
    async with factory() as session:
        stmt = (
            select(ModelProvider, ModelKey, VaultItem)
            .join(ModelKey, ModelKey.provider_id == ModelProvider.id)
            .join(VaultItem, VaultItem.id == ModelKey.vault_item_id)
            .where(
                ModelProvider.workspace_id == workspace_id,
                ModelProvider.enabled.is_(True),
                ModelProvider.deleted_at.is_(None),
                ModelKey.enabled.is_(True),
            )
            .order_by(
                ModelProvider.sort_order.asc(),
                ModelProvider.created_at.asc(),
            )
        )
        if prefer_kind:
            # Fetch all candidates so we can prefer the matching kind in Python
            # without having to special-case dialect-specific case expressions.
            stmt = stmt.limit(50)
        else:
            stmt = stmt.limit(1)

        rows = (await session.execute(stmt)).all()
        if not rows:
            return None

        chosen = rows[0]
        if prefer_kind:
            for row in rows:
                if str(row[0].kind) == prefer_kind:
                    chosen = row
                    break

        provider, _key, vault_item = chosen
        try:
            api_key = await reveal_secret(vault_item)
        except Exception as e:  # pragma: no cover
            log.warning("vault reveal failed for provider %s: %s", provider.id, e)
            return None

    kind = (
        provider.kind.value if hasattr(provider.kind, "value") else str(provider.kind)
    )
    default_model = provider.default_model or _first_builtin_model(kind) or kind
    base_url = provider.base_url or default_base_url_for(kind)
    return ResolvedModel(
        provider_kind=kind,
        model_name=default_model,
        api_key=api_key,
        base_url=base_url,
        source="db",
    )


def _first_builtin_model(kind: str) -> str | None:
    """Pick a sensible default chat model when the provider row stored none.

    Filters out ``category="embedding"`` rows so a provider whose only
    ``recommended=True`` SKU is an embedder (or whose catalog now ships
    both chat + embedding rows) still returns a chat default.
    """
    from app.agents.kernels.model_catalog import CATALOG

    rows = CATALOG.get(kind, [])
    if not rows:
        entry = get_entry(kind)
        if entry and entry.pydantic_ai_kind:
            rows = CATALOG.get(entry.pydantic_ai_kind, [])
    chat_rows = [row for row in rows if row.category != "embedding"]
    for row in chat_rows:
        if row.recommended:
            return row.model
    if chat_rows:
        return chat_rows[0].model
    return None


# ─── pydantic-ai Model factory ────────────────────────────
# Process-wide model cache. Building a pydantic-ai provider/model is
# surprisingly expensive on the cold path (the openai provider's
# constructor sets up an httpx connection pool that performs DNS +
# TLS handshake to the base_url on first use, which can cost
# 10-15s for cross-continental hosts on flaky networks). Building
# one per chat turn dominates time-to-first-token. The cache key
# includes everything that can change the resulting wire identity.
_MODEL_BUILD_CACHE: dict[tuple, object] = {}


def _model_cache_key(resolved: ResolvedModel) -> tuple:
    import hashlib
    key_hash = (
        hashlib.sha1((resolved.api_key or "").encode("utf-8")).hexdigest()[:16]
        if resolved.api_key
        else ""
    )
    return (
        resolved.provider_kind,
        resolved.model_name,
        resolved.base_url or "",
        key_hash,
    )


def build_pydantic_ai_model(resolved: ResolvedModel):
    """Return a pydantic-ai Model instance, or None if unbuildable.

    Strategy:
      1. Resolve the protocol family (openai-compatible / anthropic / google /
         bedrock / cohere / mistral / huggingface / outlines).
      2. Construct the appropriate `<X>Provider` either via reflection
         (`infer_provider_class`) or directly when the catalog kind doesn't
         map 1:1 to a pydantic-ai provider name.
      3. Wrap in the family's Model class.

    Results are memoised in :data:`_MODEL_BUILD_CACHE` keyed on the full
    provider envelope so the cold-path DNS + TLS handshake fires once
    per (workspace, model, api_key) combination.
    """
    cache_key = _model_cache_key(resolved)
    cached = _MODEL_BUILD_CACHE.get(cache_key)
    if cached is not None:
        return cached

    kind = resolved.provider_kind
    family = family_of(kind)

    try:
        if family == "openai-compatible":
            built = _build_openai_compatible(resolved)
        elif family == "anthropic":
            built = _build_anthropic(resolved)
        elif family == "google":
            built = _build_google(resolved)
        elif family == "huggingface":
            built = _build_huggingface(resolved)
        else:
            log.warning(
                "Unsupported provider family for pydantic-ai: %s (kind=%s)",
                family,
                kind,
            )
            return None
    except Exception as e:  # pragma: no cover - defensive: any provider import / build error
        log.warning(
            "Failed to build pydantic-ai model kind=%s family=%s: %s", kind, family, e
        )
        return None

    if built is not None:
        _MODEL_BUILD_CACHE[cache_key] = built
    return built


def _resolve_pydantic_ai_kind(kind: str) -> str:
    """Map a SenHarness catalog kind onto its pydantic-ai provider class name."""
    entry = get_entry(kind)
    if entry and entry.pydantic_ai_kind:
        return entry.pydantic_ai_kind
    return kind


def _build_openai_compatible(resolved: ResolvedModel):
    """All OpenAI-protocol providers go through OpenAIChatModel.

    For provider classes pydantic-ai already knows (via `infer_provider_class`)
    we use the dedicated provider so its bundled model_profile + base_url +
    headers apply correctly. For unknown / `custom` / self-hosted (vllm, sglang)
    we fall back to a plain `OpenAIProvider` with the user-supplied base_url.
    """
    from pydantic_ai.models.openai import OpenAIChatModel
    from pydantic_ai.providers import infer_provider_class
    from pydantic_ai.providers.openai import OpenAIProvider

    kind = resolved.provider_kind
    pkind = _resolve_pydantic_ai_kind(kind)
    api_key = resolved.api_key or "api-key-not-set"

    # `custom` and locally-hosted backends (vllm/sglang) don't have a dedicated
    # provider class — go straight to OpenAIProvider with explicit base_url.
    plain_kinds = {"custom", "vllm", "sglang"}
    if kind in plain_kinds:
        provider = OpenAIProvider(
            base_url=resolved.base_url,
            api_key=api_key,
        )
        return OpenAIChatModel(resolved.model_name, provider=provider)

    try:
        provider_cls = infer_provider_class(pkind)
    except ValueError:
        provider = OpenAIProvider(base_url=resolved.base_url, api_key=api_key)
        return OpenAIChatModel(resolved.model_name, provider=provider)

    # Most provider classes accept `api_key=...`. A handful (Azure, GitHub,
    # Bedrock, Vertex, Outlines, Vercel) need extra arguments — try the
    # common signature first, and fall back to plain OpenAIProvider with
    # base_url if the construction fails.
    try:
        provider = provider_cls(api_key=api_key)
    except TypeError:
        provider = OpenAIProvider(base_url=resolved.base_url, api_key=api_key)
    except Exception:  # pragma: no cover
        provider = OpenAIProvider(base_url=resolved.base_url, api_key=api_key)

    if resolved.base_url:
        provider = OpenAIProvider(base_url=resolved.base_url, api_key=api_key)

    return OpenAIChatModel(resolved.model_name, provider=provider)


def _build_anthropic(resolved: ResolvedModel):
    from pydantic_ai.models.anthropic import AnthropicModel
    from pydantic_ai.providers.anthropic import AnthropicProvider

    return AnthropicModel(
        resolved.model_name,
        provider=AnthropicProvider(api_key=resolved.api_key or ""),
    )


def _build_google(resolved: ResolvedModel):
    from pydantic_ai.models.google import GoogleModel
    from pydantic_ai.providers.google import GoogleProvider

    return GoogleModel(
        resolved.model_name,
        provider=GoogleProvider(api_key=resolved.api_key or ""),
    )


def _build_huggingface(resolved: ResolvedModel):
    from pydantic_ai.models.huggingface import HuggingFaceModel
    from pydantic_ai.providers.huggingface import HuggingFaceProvider

    return HuggingFaceModel(
        resolved.model_name,
        provider=HuggingFaceProvider(api_key=resolved.api_key or ""),
    )
