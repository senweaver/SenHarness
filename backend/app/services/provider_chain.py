"""Per-workspace provider failover chain resolver (M2.5.3).

Reads the operator-curated chain from
``workspace.home_config_json["providers"]["failover_chain"]`` (a list of
``"provider_kind:model_id"`` strings) and turns it into an ordered list
of :class:`ProviderChainEntry` rows the runner will try in sequence.

Resolution order:

1. The workspace-level ``failover_chain`` list, parsed in declared order.
2. The platform-level fallback chain from
   :class:`~app.schemas.platform_settings.provider_failover.ProviderFailoverDefaults.chain_global_default`.
3. A single-element fallback that mirrors the served alias's upstream
   (so ``failover_enabled=True`` with an empty chain still routes the
   primary provider through the failover wrapper).

After parsing, entries currently in cooldown are removed (with a debug
log so operators can spot a degraded provider). When every candidate is
in cooldown the resolver returns the **original** parsed chain — falling
back to "try anyway" is preferable to silently dropping the turn while
the cooldown is set on stale data; the runner will record fresh
failures on each attempt and trip the cooldown again on real outages.

Failover scope
--------------

The chain only switches **across** providers. Pydantic-AI's own
per-provider retry (httpx + provider SDK back-off) lives inside one
attempt; the chain wrapper kicks in after a provider has exhausted its
own retry budget. This matches roadmap principle 5: don't reformat
``message_history`` between providers — every chain attempt sends the
same payload so the upstream's prompt cache prefix stays stable.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.workspace import Workspace
from app.services import provider_health as health_svc

log = logging.getLogger(__name__)

__all__ = [
    "FAILOVER_CHAIN_KEY",
    "FAILOVER_ENABLED_KEY",
    "ProviderChainEntry",
    "ProviderFailoverConfig",
    "get_provider_chain",
    "get_workspace_failover_config",
    "parse_chain_entry",
]


# ── Workspace JSONB keys ────────────────────────────────────
PROVIDERS_KEY = "providers"
FAILOVER_ENABLED_KEY = "failover_enabled"
FAILOVER_CHAIN_KEY = "failover_chain"
FAILOVER_MAX_ATTEMPTS_KEY = "failover_max_attempts"
COOLDOWN_THRESHOLD_KEY = "cooldown_threshold"
COOLDOWN_SECONDS_KEY = "cooldown_seconds"


@dataclass(slots=True, frozen=True)
class ProviderChainEntry:
    """One candidate provider/model the runner can try this turn."""

    provider_kind: str
    model_id: str
    upstream_label: str  # e.g. "openai:gpt-5" — fed straight into model_override


@dataclass(slots=True)
class ProviderFailoverConfig:
    """Resolved per-workspace failover knobs (defaults overlaid)."""

    enabled: bool
    chain_raw: list[str]
    failover_max_attempts: int
    cooldown_threshold: int
    cooldown_seconds: int


# ── Parsing ─────────────────────────────────────────────────
def parse_chain_entry(raw: str) -> ProviderChainEntry | None:
    """Parse one ``"provider_kind:model_id"`` string.

    Returns ``None`` when the string is malformed (empty, missing the
    separator, blank halves) so the caller can skip degenerate entries
    without aborting the whole chain. Mirrors the format the runner's
    ``parse_override`` expects so we can hand the label straight to
    ``model_client``.
    """
    if not isinstance(raw, str):
        return None
    cleaned = raw.strip()
    if not cleaned or ":" not in cleaned:
        return None
    left, _, right = cleaned.partition(":")
    provider_kind = left.strip().lower()
    model_id = right.strip()
    if not provider_kind or not model_id:
        return None
    return ProviderChainEntry(
        provider_kind=provider_kind,
        model_id=model_id,
        upstream_label=cleaned,
    )


def _read_provider_block(workspace: Workspace | None) -> dict[str, Any]:
    if workspace is None:
        return {}
    home = workspace.home_config_json or {}
    raw = home.get(PROVIDERS_KEY)
    if not isinstance(raw, dict):
        return {}
    return raw


# ── Config resolution ───────────────────────────────────────
async def _load_platform_defaults(
    db: AsyncSession,
) -> dict[str, Any]:
    """Resolve the platform-level ``provider_failover`` section payload.

    Falls back to the schema defaults when the row is missing — the
    section is opt-in, so an unconfigured deployment still observes
    the safe ``enabled_default=False`` posture.
    """
    try:
        from app.services.platform_settings import (
            PlatformSettingsSection,
            get_section,
        )
    except Exception:  # pragma: no cover — defensive
        return {}
    try:
        value = await get_section(
            db, section=PlatformSettingsSection.PROVIDER_FAILOVER
        )
    except Exception:  # pragma: no cover — defensive
        return {}
    return value.model_dump(mode="json")


async def get_workspace_failover_config(
    db: AsyncSession,
    *,
    workspace_id: uuid.UUID,
) -> ProviderFailoverConfig:
    """Merge platform defaults + per-workspace overrides into one config.

    Workspace overrides (when present) win on a field-by-field basis;
    missing fields fall back to the platform defaults. The chain itself
    is **not** merged — the workspace either supplies its own ordered
    list or inherits the platform-wide fallback chain wholesale.
    """
    defaults = await _load_platform_defaults(db)
    workspace = await db.get(Workspace, workspace_id)
    block = _read_provider_block(workspace)

    enabled_default = bool(defaults.get("enabled_default", False))
    enabled = bool(block.get(FAILOVER_ENABLED_KEY, enabled_default))

    raw_chain = block.get(FAILOVER_CHAIN_KEY)
    if isinstance(raw_chain, list) and raw_chain:
        chain_raw = [str(x) for x in raw_chain if isinstance(x, str)]
    else:
        global_chain = defaults.get("chain_global_default") or []
        chain_raw = [str(x) for x in global_chain if isinstance(x, str)]

    return ProviderFailoverConfig(
        enabled=enabled,
        chain_raw=chain_raw,
        failover_max_attempts=int(
            block.get(
                FAILOVER_MAX_ATTEMPTS_KEY,
                defaults.get("failover_max_attempts_default", 3),
            )
        ),
        cooldown_threshold=int(
            block.get(
                COOLDOWN_THRESHOLD_KEY,
                defaults.get("cooldown_threshold_default", 3),
            )
        ),
        cooldown_seconds=int(
            block.get(
                COOLDOWN_SECONDS_KEY,
                defaults.get("cooldown_seconds_default", 300),
            )
        ),
    )


# ── Chain resolution ────────────────────────────────────────
def _dedup_keep_first(entries: list[ProviderChainEntry]) -> list[ProviderChainEntry]:
    seen: set[tuple[str, str]] = set()
    out: list[ProviderChainEntry] = []
    for entry in entries:
        key = (entry.provider_kind, entry.model_id)
        if key in seen:
            continue
        seen.add(key)
        out.append(entry)
    return out


async def get_provider_chain(
    db: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    served_name: str | None = None,
    primary_upstream: str | None = None,
    config: ProviderFailoverConfig | None = None,
    redis: Any | None = None,
) -> list[ProviderChainEntry]:
    """Compute the ordered list of provider/model candidates to try.

    ``primary_upstream`` is the resolver's chosen upstream for this turn
    (from the served alias map, the agent default, or the per-turn
    override). When the parsed chain is empty we synthesise a single
    entry from it so ``failover_enabled=True`` with no chain still
    routes the primary call through the failover wrapper.

    Entries currently in cooldown are removed. When every entry is in
    cooldown we return the **original** parsed chain — failing closed
    here would silently drop the turn even though the cooldown could
    be stale; the runner will record fresh failures and re-trip the
    cooldown on the next attempt if the provider is still down.
    """
    cfg = config or await get_workspace_failover_config(
        db, workspace_id=workspace_id
    )

    parsed: list[ProviderChainEntry] = []
    for raw in cfg.chain_raw:
        entry = parse_chain_entry(raw)
        if entry is not None:
            parsed.append(entry)

    if not parsed and primary_upstream:
        primary_entry = parse_chain_entry(primary_upstream)
        if primary_entry is not None:
            parsed.append(primary_entry)

    parsed = _dedup_keep_first(parsed)
    if not parsed:
        return []

    # Cap at ``failover_max_attempts`` so a misconfigured chain with 20
    # entries can't burn through the operator's budget on a single turn.
    cap = max(1, int(cfg.failover_max_attempts))
    parsed = parsed[:cap]

    healthy: list[ProviderChainEntry] = []
    for entry in parsed:
        in_cooldown = await health_svc.is_in_cooldown(
            redis,
            provider_kind=entry.provider_kind,
            model_id=entry.model_id,
        )
        if in_cooldown:
            log.debug(
                "provider_chain skip in_cooldown provider=%s model=%s",
                entry.provider_kind,
                entry.model_id,
            )
            continue
        healthy.append(entry)

    if healthy:
        return healthy

    log.warning(
        "provider_chain all entries in cooldown — falling back to full chain "
        "workspace=%s served=%s",
        workspace_id,
        served_name or "",
    )
    return list(parsed)
