"""Model client resolver.

Builds a `pydantic_ai.models.Model` instance given an `Agent` and the workspace
model-pool configuration. In P1 we prefer env-configured providers (simple path
to first-run) and fall back gracefully when no provider is configured.

Resolution order:
  1. Explicit `model_override` on the RunRequest (e.g. `"openai:gpt-4o-mini"`).
  2. Workspace `model_routes` marked `is_default=true`  (P2+, not yet used here).
  3. First enabled `model_providers` row in the workspace with a resolvable key.
  4. Env vars: `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `GOOGLE_API_KEY`,
     `OPENROUTER_API_KEY`, `DEEPSEEK_API_KEY`, `MOONSHOT_API_KEY`, `GROQ_API_KEY`,
     `OLLAMA_HOST` (no key required).

The return value is a pydantic-ai `Model` instance ready for `Agent(model=...)`.
"""

from __future__ import annotations

import logging
import os
import uuid
from dataclasses import dataclass

log = logging.getLogger(__name__)


@dataclass(slots=True)
class ResolvedModel:
    """Lightweight descriptor with everything needed to build a pydantic-ai Model."""

    provider_kind: str         # "openai" | "anthropic" | ... | "mock"
    model_name: str            # e.g. "gpt-4o-mini"
    api_key: str | None        # plaintext (in-memory only)
    base_url: str | None = None
    extra: dict | None = None
    source: str = "env"        # "env" | "db" | "override" | "mock"


# ─── Env fallback providers (deterministic order) ──────────
_ENV_PROVIDERS: tuple[tuple[str, str, str, str], ...] = (
    # (kind, env_key, default_model, default_base_url)
    ("openai",       "OPENAI_API_KEY",       "gpt-4o-mini",                 "https://api.openai.com/v1"),
    ("anthropic",    "ANTHROPIC_API_KEY",    "claude-3-5-sonnet-latest",    "https://api.anthropic.com"),
    ("google",       "GOOGLE_API_KEY",       "gemini-1.5-flash",            ""),
    ("openrouter",   "OPENROUTER_API_KEY",   "openai/gpt-4o-mini",          "https://openrouter.ai/api/v1"),
    ("deepseek",     "DEEPSEEK_API_KEY",     "deepseek-chat",               "https://api.deepseek.com/v1"),
    ("moonshot",     "MOONSHOT_API_KEY",     "moonshot-v1-8k",              "https://api.moonshot.cn/v1"),
    ("groq",         "GROQ_API_KEY",         "llama-3.3-70b-versatile",     "https://api.groq.com/openai/v1"),
)


def resolve_from_env() -> ResolvedModel | None:
    """Scan well-known env vars for the first available provider."""
    for kind, env_key, model, base in _ENV_PROVIDERS:
        key = os.environ.get(env_key)
        if key:
            return ResolvedModel(
                provider_kind=kind,
                model_name=model,
                api_key=key,
                base_url=base or None,
                source="env",
            )
    # Ollama requires no key
    ollama_host = os.environ.get("OLLAMA_HOST")
    if ollama_host:
        return ResolvedModel(
            provider_kind="ollama",
            model_name=os.environ.get("OLLAMA_MODEL", "llama3.1"),
            api_key=None,
            base_url=f"{ollama_host.rstrip('/')}/v1",
            source="env",
        )
    return None


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
        api_key=None,  # fall back to env key for that provider
        base_url=base_url.strip() if base_url else None,
        source="override",
    )


async def resolve_for_agent(
    *, workspace_id: uuid.UUID, agent_id: uuid.UUID, override: str | None = None
) -> ResolvedModel | None:
    """High-level resolver: override → DB-backed provider (with Vault key) → env fallback."""
    if override:
        parsed = parse_override(override)
        if parsed is not None:
            if parsed.api_key is None:
                env_fallback = resolve_from_env()
                if env_fallback and env_fallback.provider_kind == parsed.provider_kind:
                    parsed.api_key = env_fallback.api_key
                    parsed.base_url = parsed.base_url or env_fallback.base_url
            return parsed

    _ = agent_id  # P2: consult agent.model_route_id for provider selection

    db_resolved = await _resolve_from_db(workspace_id=workspace_id)
    if db_resolved is not None:
        return db_resolved

    return resolve_from_env()


async def _resolve_from_db(*, workspace_id: uuid.UUID) -> ResolvedModel | None:
    """Look up the first enabled provider for the workspace and unwrap its key via Vault."""
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
            .order_by(ModelProvider.created_at.asc())
            .limit(1)
        )
        row = (await session.execute(stmt)).first()
        if row is None:
            return None
        provider, _key, vault_item = row
        try:
            api_key = await reveal_secret(vault_item)
        except Exception as e:  # pragma: no cover
            log.warning("vault reveal failed for provider %s: %s", provider.id, e)
            return None

    kind = provider.kind.value if hasattr(provider.kind, "value") else str(provider.kind)
    default_model = provider.default_model or _default_model_for(kind)
    base_url = provider.base_url or _default_base_for(kind)
    return ResolvedModel(
        provider_kind=kind,
        model_name=default_model,
        api_key=api_key,
        base_url=base_url,
        source="db",
    )


def _default_model_for(kind: str) -> str:
    for env_kind, _, model, _ in _ENV_PROVIDERS:
        if env_kind == kind:
            return model
    return kind


def _default_base_for(kind: str) -> str | None:
    for env_kind, _, _, base in _ENV_PROVIDERS:
        if env_kind == kind:
            return base or None
    return None


# ─── pydantic-ai Model factory ────────────────────────────
def build_pydantic_ai_model(resolved: ResolvedModel):
    """Return a pydantic-ai Model instance, or None if unbuildable.

    Uses the new provider import paths in pydantic-ai >= 1.0.
    """
    try:
        from pydantic_ai.models.openai import OpenAIChatModel
    except ImportError:  # pragma: no cover
        log.warning("pydantic-ai not installed or incompatible version")
        return None

    kind = resolved.provider_kind

    # The vast majority of real-world providers speak OpenAI-compatible chat
    # completions: openai / openrouter / deepseek / moonshot / groq / ollama /
    # vllm / sglang / Azure / many local runtimes. We route them through
    # `OpenAIChatModel` with a provider-specific base_url.
    OPENAI_COMPATIBLE = {
        "openai", "openrouter", "deepseek", "moonshot", "groq",
        "ollama", "vllm", "sglang", "custom", "azure_openai",
    }
    if kind in OPENAI_COMPATIBLE:
        try:
            from pydantic_ai.providers.openai import OpenAIProvider

            provider = OpenAIProvider(
                base_url=resolved.base_url,
                api_key=resolved.api_key or "dummy-key",
            )
            return OpenAIChatModel(resolved.model_name, provider=provider)
        except Exception as e:
            log.warning("Failed to build OpenAIChatModel for %s: %s", kind, e)
            return None

    if kind == "anthropic":
        try:
            from pydantic_ai.models.anthropic import AnthropicModel
            from pydantic_ai.providers.anthropic import AnthropicProvider

            return AnthropicModel(
                resolved.model_name,
                provider=AnthropicProvider(api_key=resolved.api_key or ""),
            )
        except Exception as e:
            log.warning("Failed to build AnthropicModel: %s", e)
            return None

    if kind == "google":
        try:
            from pydantic_ai.models.google import GoogleModel
            from pydantic_ai.providers.google import GoogleProvider

            return GoogleModel(
                resolved.model_name,
                provider=GoogleProvider(api_key=resolved.api_key or ""),
            )
        except Exception as e:
            log.warning("Failed to build GoogleModel: %s", e)
            return None

    log.warning("Unknown provider kind for pydantic-ai: %s", kind)
    return None
