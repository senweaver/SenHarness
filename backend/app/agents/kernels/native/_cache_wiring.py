"""Runner wiring for the M2.5.9 cache marker.

Glues :mod:`app.services.cache_control` and
:mod:`app.services.cache_adaptive` to the native runner without
embedding 200 lines of cache-aware logic into ``runner.py``. The
runner imports two functions from this module: :func:`prepare` runs
before ``agent.iter`` to resolve config + apply cache settings to
the model, and :func:`finalize` runs after the stream closes to
record the hit/miss outcome.

Splitting it out also keeps the **fall-through semantics explicit**:
on any DB hiccup, missing config, or unsupported provider, prepare()
returns a no-op preparation result that finalize() handles cleanly.
The runner never has to branch on "is cache wired up" — every code
path drops through to the fail-open default.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from typing import Any

from app.services import audit as audit_svc
from app.services import cache_adaptive
from app.services import cache_control as cache_ctl

log = logging.getLogger(__name__)

__all__ = [
    "CacheWiringResult",
    "finalize",
    "prepare",
]


@dataclass(slots=True)
class CacheWiringResult:
    """Carry the cache-aware decision from prepare → finalize.

    The runner stashes one instance per turn between the
    ``annotate_cache_breakpoints`` decision and the post-stream usage
    drain. ``annotated`` is True only when we actually mutated the
    model settings (or message content) — finalize() consults it to
    audit ``cache.annotated`` exactly once per turn.
    """

    annotated: bool = False
    enabled: bool = False
    disabled_by_adaptive: bool = False
    provider_kind: str = ""
    workspace_id: uuid.UUID | None = None
    ttl: cache_ctl.CacheTtl = cache_ctl.CacheTtl.DEFAULT
    breakpoint_count: int = 0
    extras: dict[str, Any] = field(default_factory=dict)


# Workspace JSONB keys — same shape as the M2.5.3 provider block so
# the admin UI can render both M2.5.3 and M2.5.9 knobs from one
# nested namespace.
WORKSPACE_PROVIDERS_KEY = "providers"
WORKSPACE_CACHE_KEY = "cache_control"
_DEFAULT_TTL = cache_ctl.CacheTtl.DEFAULT


# ─── Public entry points ────────────────────────────────────
async def prepare(
    *,
    agent: Any,
    workspace_id: uuid.UUID,
    provider_kind: str,
    redis: Any | None,
) -> CacheWiringResult:
    """Resolve the cache config and apply it to ``agent`` in-place.

    The function is idempotent and never raises — a resolution
    failure logs at DEBUG and returns a result with ``enabled=False``
    so the caller takes the no-cache path. It writes one of two
    audit rows:

    * ``cache.annotated`` — when annotation actually landed
      (provider supported + workspace enabled + not in adaptive
      disable).
    * ``cache.adaptive_skipped`` — when annotation was skipped
      because the workspace is currently inside the adaptive disable
      window. Useful for operators debugging "why is my Anthropic
      bill so high" — the audit row carries the disable timestamp.

    No audit is written for the unsupported-provider path; that's
    the steady state for OpenAI/DeepSeek/Google and would flood the
    log.
    """
    pk = cache_ctl.normalize_provider_kind(provider_kind)
    result = CacheWiringResult(
        provider_kind=pk,
        workspace_id=workspace_id,
    )
    if not pk or not cache_ctl.is_provider_supported(pk):
        return result

    config = await _resolve_config(workspace_id=workspace_id)
    if not config.enabled:
        return result

    cache_adaptive.configure_thresholds(
        threshold=config.adaptive_disable_threshold,
        duration_seconds=config.adaptive_disable_duration_seconds,
    )

    disabled = await cache_adaptive.is_cache_disabled(
        redis,
        workspace_id=workspace_id,
        provider_kind=pk,
    )
    if disabled:
        result.disabled_by_adaptive = True
        await _audit(
            action="cache.adaptive_skipped",
            workspace_id=workspace_id,
            metadata={
                "provider_kind": pk,
                "reason": "inside_adaptive_window",
            },
        )
        return result

    annotated = _apply_to_agent(
        agent=agent, provider_kind=pk, ttl=config.ttl
    )
    result.enabled = True
    result.annotated = annotated
    result.ttl = config.ttl
    if annotated:
        # Record the breakpoint count we actually requested so the
        # admin dashboard can correlate "how many markers are we
        # spending" against "how many cache hits did we get".
        result.breakpoint_count = config.max_breakpoints
        await _audit(
            action="cache.annotated",
            workspace_id=workspace_id,
            metadata={
                "provider_kind": pk,
                "ttl": config.ttl.value,
                "breakpoint_count": config.max_breakpoints,
            },
        )
    return result


async def finalize(
    *,
    result: CacheWiringResult,
    usage: Any,
    redis: Any | None,
    actor_identity_id: uuid.UUID | None,
) -> int:
    """Record the cache hit/miss outcome of the just-finished turn.

    Returns the cache-hit token count for the runner to surface on
    the USAGE event metadata. When ``result.enabled`` is False the
    function is a no-op (returns 0) — we never want to write hit/miss
    stats for turns where annotation didn't even attempt to land.
    """
    if not result.enabled or result.workspace_id is None:
        return 0

    hit_tokens = cache_ctl.extract_cache_hit_tokens(
        usage, provider_kind=result.provider_kind
    )
    hit = hit_tokens > 0
    snapshot = await cache_adaptive.record_cache_result(
        redis,
        workspace_id=result.workspace_id,
        provider_kind=result.provider_kind,
        hit=hit,
        hit_tokens=hit_tokens,
    )

    action = "cache.hit" if hit else "cache.miss_recorded"
    await _audit(
        action=action,
        workspace_id=result.workspace_id,
        metadata={
            "provider_kind": result.provider_kind,
            "hit_tokens": int(hit_tokens),
            "consecutive_misses": int(snapshot.consecutive_misses),
            "total_hits": int(snapshot.total_hits),
            "total_misses": int(snapshot.total_misses),
        },
    )

    if snapshot.extras.get("just_disabled"):
        await _audit(
            action="cache.adaptive_disabled",
            workspace_id=result.workspace_id,
            metadata={
                "provider_kind": result.provider_kind,
                "consecutive_misses": int(snapshot.consecutive_misses),
                "disabled_until": (
                    snapshot.disabled_until.isoformat()
                    if snapshot.disabled_until is not None
                    else None
                ),
                "duration_seconds": int(
                    cache_adaptive.ADAPTIVE_DISABLE_DURATION_SECONDS
                ),
            },
        )
        await _emit_disabled_notification(
            workspace_id=result.workspace_id,
            actor_identity_id=actor_identity_id,
            provider_kind=result.provider_kind,
            disabled_until=snapshot.disabled_until,
        )
    elif snapshot.extras.get("just_recovered"):
        await _audit(
            action="cache.adaptive_recovered",
            workspace_id=result.workspace_id,
            metadata={
                "provider_kind": result.provider_kind,
                "total_hits": int(snapshot.total_hits),
                "total_misses": int(snapshot.total_misses),
            },
        )

    return int(hit_tokens)


# ─── Workspace + platform config resolution ─────────────────
@dataclass(slots=True)
class _ResolvedCacheConfig:
    enabled: bool
    min_prompt_tokens: int
    max_breakpoints: int
    ttl: cache_ctl.CacheTtl
    adaptive_disable_threshold: int
    adaptive_disable_duration_seconds: int


_DEFAULT_CONFIG = _ResolvedCacheConfig(
    enabled=False,
    min_prompt_tokens=1024,
    max_breakpoints=4,
    ttl=cache_ctl.CacheTtl.DEFAULT,
    adaptive_disable_threshold=cache_adaptive.ADAPTIVE_DISABLE_THRESHOLD,
    adaptive_disable_duration_seconds=(
        cache_adaptive.ADAPTIVE_DISABLE_DURATION_SECONDS
    ),
)


async def _resolve_config(
    *, workspace_id: uuid.UUID
) -> _ResolvedCacheConfig:
    """Read platform defaults + per-workspace overrides.

    Falls back to a safe disabled default on any DB / settings
    failure — caller treats False ``enabled`` as "skip cache wiring".
    """
    try:
        from app.db.models.workspace import Workspace  # noqa: PLC0415
        from app.db.session import get_session_factory  # noqa: PLC0415
        from app.services.platform_settings import (  # noqa: PLC0415
            PlatformSettingsSection,
            get_section,
        )
    except Exception:  # pragma: no cover — degraded import path
        return _DEFAULT_CONFIG

    factory = get_session_factory()
    try:
        async with factory() as fresh:
            section = await get_section(
                fresh, section=PlatformSettingsSection.CACHE_CONTROL
            )
            workspace = await fresh.get(Workspace, workspace_id)
            home = (workspace.home_config_json or {}) if workspace else {}
    except Exception:  # pragma: no cover — degraded DB path
        return _DEFAULT_CONFIG

    section_dict = section.model_dump(mode="json") if section else {}
    enabled_default = bool(section_dict.get("enabled_default", True))
    min_tokens_default = int(section_dict.get("min_prompt_tokens_default", 1024))
    max_breakpoints_default = int(section_dict.get("max_breakpoints_default", 4))
    ttl_default_raw = str(section_dict.get("ttl_default", "5m"))
    threshold_default = int(section_dict.get("adaptive_disable_threshold", 5))
    duration_default = int(
        section_dict.get("adaptive_disable_duration_seconds", 60)
    )

    providers_block = home.get(WORKSPACE_PROVIDERS_KEY)
    cache_block: dict[str, Any] = {}
    if isinstance(providers_block, dict):
        raw_cache = providers_block.get(WORKSPACE_CACHE_KEY)
        if isinstance(raw_cache, dict):
            cache_block = raw_cache

    enabled = bool(cache_block.get("enabled", enabled_default))
    min_tokens = int(cache_block.get("min_prompt_tokens", min_tokens_default))
    max_breakpoints = int(
        cache_block.get("max_breakpoints", max_breakpoints_default)
    )
    ttl_raw = str(cache_block.get("ttl", ttl_default_raw)).strip().lower()
    if ttl_raw in {"1h", "extended_1h", "extended-1h"}:
        ttl = cache_ctl.CacheTtl.EXTENDED_1H
    else:
        ttl = cache_ctl.CacheTtl.DEFAULT

    threshold = int(
        cache_block.get("adaptive_disable_threshold", threshold_default)
    )
    duration = int(
        cache_block.get(
            "adaptive_disable_duration_seconds", duration_default
        )
    )

    return _ResolvedCacheConfig(
        enabled=enabled,
        min_prompt_tokens=max(0, min_tokens),
        max_breakpoints=max(1, min(8, max_breakpoints)),
        ttl=ttl,
        adaptive_disable_threshold=max(1, threshold),
        adaptive_disable_duration_seconds=max(1, duration),
    )


# ─── Native model annotation ────────────────────────────────
def _apply_to_agent(
    *, agent: Any, provider_kind: str, ttl: cache_ctl.CacheTtl
) -> bool:
    """Apply provider-native cache settings to ``agent``.

    Anthropic exposes first-class cache knobs on the model settings
    bag (``anthropic_cache_*`` plus the ``extended-cache-ttl``
    beta when needed); OpenRouter's openai-compatible ChatModel does
    not currently expose a clean injection point for the per-message
    ``cache_control`` blocks, so the runner records intent + stats
    via the cache_adaptive tracker but does not mutate the request.
    Returns True when annotation actually landed.
    """
    if provider_kind == "anthropic":
        return _apply_anthropic(agent=agent, ttl=ttl)
    if provider_kind == "openrouter":
        return _apply_openrouter(agent=agent, ttl=ttl)
    return False


def _apply_anthropic(*, agent: Any, ttl: cache_ctl.CacheTtl) -> bool:
    """Pour the AnthropicCacheSettings into ``agent.model_settings``."""
    try:
        settings = cache_ctl.build_anthropic_cache_settings(ttl=ttl)
        ms = getattr(agent, "model_settings", None)
        if ms is None:
            agent.model_settings = {}  # type: ignore[attr-defined]
            ms = agent.model_settings  # type: ignore[attr-defined]
        if not isinstance(ms, dict):
            # Some pydantic-ai versions accept a TypedDict or dataclass
            # — give up cleanly so the caller falls through to NoOp
            # rather than corrupting an opaque object.
            return False

        if settings.cache_tool_definitions:
            ms["anthropic_cache_tool_definitions"] = settings.cache_tool_definitions
        if settings.cache_instructions:
            ms["anthropic_cache_instructions"] = settings.cache_instructions
        if settings.cache_messages:
            ms["anthropic_cache_messages"] = settings.cache_messages
        if settings.cache:
            ms["anthropic_cache"] = settings.cache

        if settings.betas:
            existing = ms.get("anthropic_betas")
            betas = list(existing) if isinstance(existing, list) else []
            for beta in settings.betas:
                if beta not in betas:
                    betas.append(beta)
            ms["anthropic_betas"] = betas
        return True
    except Exception:  # pragma: no cover — defensive
        log.debug("anthropic cache_control apply failed", exc_info=True)
        return False


def _apply_openrouter(*, agent: Any, ttl: cache_ctl.CacheTtl) -> bool:
    """Stash an intent flag on ``agent.model_settings`` for OpenRouter.

    OpenRouter passes Anthropic-shaped ``cache_control`` markers
    through to the upstream Anthropic API verbatim, but pydantic-ai's
    OpenAIChatModel does not expose a per-message hook for inserting
    those markers. We record the intent so a future pydantic-ai
    upgrade can pick it up; the cache_adaptive tracker still records
    stats because the upstream hit/miss reporting is provider-agnostic.

    Returns False so the caller doesn't audit ``cache.annotated`` —
    no actual annotation landed yet.
    """
    try:
        ms = getattr(agent, "model_settings", None)
        if ms is None:
            agent.model_settings = {}  # type: ignore[attr-defined]
            ms = agent.model_settings  # type: ignore[attr-defined]
        if isinstance(ms, dict):
            ms.setdefault("openrouter_cache_intent", ttl.value)
    except Exception:  # pragma: no cover — defensive
        log.debug("openrouter cache_control intent stash failed", exc_info=True)
    return False


# ─── Audit helpers ──────────────────────────────────────────
async def _audit(
    *,
    action: str,
    workspace_id: uuid.UUID,
    metadata: dict[str, Any],
) -> None:
    """Open a fresh session for the audit row.

    Mirrors the pattern already used by ``_audit_upstream_called`` in
    ``runner.py``: the runner's main session is short-lived and
    closed before the stream finalises, so we cannot rely on it.
    Errors here log + swallow because cache audit is observability
    only — a degraded audit pipeline must never break the chat turn.
    """
    try:
        from app.db.session import get_session_factory  # noqa: PLC0415

        factory = get_session_factory()
        async with factory() as fresh:
            await audit_svc.record(
                fresh,
                action=action,
                actor_identity_id=None,
                workspace_id=workspace_id,
                resource_type="cache_control",
                resource_id=None,
                summary=f"{action} ({metadata.get('provider_kind', '')})",
                metadata=metadata,
            )
            await fresh.commit()
    except Exception:  # pragma: no cover — degraded audit path
        log.debug("cache audit %s failed", action, exc_info=True)


async def _emit_disabled_notification(
    *,
    workspace_id: uuid.UUID,
    actor_identity_id: uuid.UUID | None,
    provider_kind: str,
    disabled_until: Any,
) -> None:
    """Fan ``cache.adaptive_disabled`` out via the M0.10 pipeline.

    Workspace admins are the audience; the runner is fail-safe so a
    notification crash never trips the turn. The cooldown_resource_id
    is set to ``provider_kind`` so two genuinely distinct providers
    can both fire without dedup collapsing them.
    """
    try:
        from app.db.session import get_session_factory  # noqa: PLC0415
        from app.services.notification_events import emit_event  # noqa: PLC0415

        factory = get_session_factory()
        async with factory() as fresh:
            await emit_event(
                fresh,
                event_key="cache.adaptive_disabled",
                workspace_id=workspace_id,
                actor_identity_id=actor_identity_id,
                payload={
                    "provider_kind": provider_kind,
                    "disabled_until": (
                        disabled_until.isoformat()
                        if hasattr(disabled_until, "isoformat")
                        else str(disabled_until or "")
                    ),
                    "resource_type": "cache_control",
                },
                cooldown_resource_id=provider_kind,
            )
            await fresh.commit()
    except Exception:  # pragma: no cover — degraded notification path
        log.debug(
            "cache.adaptive_disabled notification emit failed", exc_info=True
        )


_ = _DEFAULT_TTL  # silence unused (kept for downstream consumer extension)
