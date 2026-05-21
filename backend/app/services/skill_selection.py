"""Active skill set selection — hard cap on simultaneously injected packs.

Design principle 3 of the M1 roadmap calls for a hard cap on always-on
context: a SkillPack pool that grows unbounded would silently eat the
system prompt budget reserved for persona / memory / tool schemas.
M1.8 plugs the gap by inserting a deterministic selection step between
:meth:`SkillPackRepository.list_active` and the runtime capability
materialisation in :func:`app.agents.harness.skills.build_skills_capability`.

The selection is split into a pure function (:func:`select_active_set`,
trivially testable, no DB) and a thin DB-backed resolver
(:func:`get_workspace_skill_config`, merges workspace overrides with
the platform default). The same pure function is the contract M1.4
curator and M2 evolver will reuse when they need to preview which
packs the runtime would currently inject.

Selection contract:

* **Pinned packs are exempt.** A pack with ``pinned=True`` is always
  selected even when both caps are full — pin is the explicit user
  override that says "I accept the prompt cost". When pinned packs
  alone exceed the count cap a single warn-level log lands so the
  operator can see the breach in audit, but no pack is dropped.
* **Unpinned packs are sorted** by ``effectiveness_avg DESC NULLS
  LAST, last_used_at DESC NULLS LAST, created_at ASC``. Higher
  effectiveness wins; ties break on most-recently-used; final
  tiebreak goes to the older pack (stable across reorders of the
  input list).
* **Greedy fill** — packs are appended one by one until either the
  count cap or the char cap is hit. Char cap uses ``pack.content_md``
  (transient attribute set by the caller; falls back to
  ``pack.description`` for callers that haven't pre-loaded the body).
* **selection_strategy="manual_only"** disables the reorder so the
  caller's input order wins. Used by the curator preview when an
  operator wants to see "what would I get with this exact list".

Drops are returned as a separate list so the caller can record one
``DROPPED_AT_CAP`` row per dropped pack — that telemetry is the
curator's signal to archive long-tail packs that never make the cut.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.skills import SkillPack
from app.db.models.workspace import Workspace
from app.services.system_settings import (
    SystemSettingKey,
    get_system_setting,
)

log = logging.getLogger(__name__)

__all__ = [
    "DEFAULT_MAX_ACTIVE_INJECTED",
    "DEFAULT_MAX_INJECTED_CHARS_TOTAL",
    "DEFAULT_SELECTION_STRATEGY",
    "STRATEGY_EFFECTIVENESS_THEN_RECENCY",
    "STRATEGY_MANUAL_ONLY",
    "SkillSelectionConfig",
    "SkillSelectionResult",
    "estimate_pack_chars",
    "get_workspace_skill_config",
    "select_active_set",
]


# Defaults match :class:`SkillInjectionDefaults`; duplicated here so the
# pure-function path doesn't need to import the schema layer.
DEFAULT_MAX_ACTIVE_INJECTED: int = 30
DEFAULT_MAX_INJECTED_CHARS_TOTAL: int = 12000
STRATEGY_EFFECTIVENESS_THEN_RECENCY: str = "effectiveness_then_recency"
STRATEGY_MANUAL_ONLY: str = "manual_only"
DEFAULT_SELECTION_STRATEGY: str = STRATEGY_EFFECTIVENESS_THEN_RECENCY

_VALID_STRATEGIES: frozenset[str] = frozenset(
    {STRATEGY_EFFECTIVENESS_THEN_RECENCY, STRATEGY_MANUAL_ONLY}
)


@dataclass(frozen=True, slots=True)
class SkillSelectionConfig:
    max_active_injected: int = DEFAULT_MAX_ACTIVE_INJECTED
    max_injected_chars_total: int = DEFAULT_MAX_INJECTED_CHARS_TOTAL
    selection_strategy: str = DEFAULT_SELECTION_STRATEGY

    def normalised_strategy(self) -> str:
        if self.selection_strategy in _VALID_STRATEGIES:
            return self.selection_strategy
        return DEFAULT_SELECTION_STRATEGY


@dataclass(slots=True)
class SkillSelectionResult:
    selected: list[SkillPack] = field(default_factory=list)
    dropped: list[SkillPack] = field(default_factory=list)
    char_count: int = 0
    truncated_by_count: bool = False
    truncated_by_chars: bool = False


def estimate_pack_chars(pack: SkillPack) -> int:
    """Best-effort char count for the pack body.

    Caller may attach a transient ``content_md`` attribute on the pack
    (the harness layer does this after loading the SKILL.md file) for
    a precise count; the fallback uses :attr:`SkillPack.description`
    which is accurate for description-only packs and a tight
    underestimate otherwise. The selector only needs a stable proxy —
    a minor underestimate is harmless because the count cap acts as
    the safety net.
    """
    body = getattr(pack, "content_md", None)
    if isinstance(body, str) and body:
        return len(body)
    desc = getattr(pack, "description", None)
    if isinstance(desc, str) and desc:
        return len(desc)
    return 0


def _sort_key(pack: SkillPack) -> tuple[int, float, int, float, float, str]:
    """Sort tuple ordering packs from best-injection-candidate to worst.

    Layout (smaller tuple = injected first):

    1. ``effectiveness_avg DESC NULLS LAST`` → invert score; ``None``
       packs receive a sentinel that sorts last.
    2. ``last_used_at DESC NULLS LAST`` → invert epoch; ``None`` packs
       receive a sentinel that sorts last.
    3. ``created_at ASC`` for the final stable tiebreak (older first
       so two packs with identical telemetry pick deterministically).
    4. ``slug ASC`` as the absolute last resort so two rows created
       at the exact same instant in tests still order deterministically.
    """
    eff = getattr(pack, "effectiveness_avg", None)
    last_used = getattr(pack, "last_used_at", None)
    created = getattr(pack, "created_at", None)
    slug = getattr(pack, "slug", "") or ""

    eff_null = 1 if eff is None else 0
    eff_score = -float(eff) if eff is not None else 0.0
    last_null = 1 if last_used is None else 0
    last_score = -_to_epoch(last_used)
    created_score = _to_epoch(created)
    return (
        eff_null,
        eff_score,
        last_null,
        last_score,
        created_score,
        slug if isinstance(slug, str) else str(slug),
    )


def _to_epoch(value: datetime | None) -> float:
    if value is None:
        return 0.0
    try:
        return value.timestamp()
    except (ValueError, OSError, AttributeError):
        return 0.0


def select_active_set(
    packs: Sequence[SkillPack],
    *,
    cap: SkillSelectionConfig,
) -> SkillSelectionResult:
    """Pick the subset of ``packs`` that fits inside ``cap``.

    Pinned packs are always included (above the count cap if needed —
    a warn log fires once for visibility but no pinned pack is ever
    dropped). Unpinned packs are reordered by the configured strategy
    and greedily filled until either the count cap or the char cap is
    hit; the rest land in the ``dropped`` list and the caller is
    responsible for emitting the ``DROPPED_AT_CAP`` telemetry.
    """
    result = SkillSelectionResult()
    if not packs:
        return result

    pinned: list[SkillPack] = []
    unpinned: list[SkillPack] = []
    for pack in packs:
        if getattr(pack, "pinned", False):
            pinned.append(pack)
        else:
            unpinned.append(pack)

    strategy = cap.normalised_strategy()
    if strategy == STRATEGY_EFFECTIVENESS_THEN_RECENCY:
        unpinned = sorted(unpinned, key=_sort_key)

    pinned_chars = sum(estimate_pack_chars(p) for p in pinned)
    result.selected.extend(pinned)
    result.char_count = pinned_chars

    pinned_count = len(pinned)
    cap_count = max(int(cap.max_active_injected), 0)
    cap_chars = max(int(cap.max_injected_chars_total), 0)

    if pinned_count > cap_count:
        log.warning(
            "skill.cap_pinned_above_count_cap pinned=%d cap=%d "
            "(pinned packs are exempt; cap not enforced for them)",
            pinned_count,
            cap_count,
        )

    remaining_count_slots = max(cap_count - pinned_count, 0)
    for pack in unpinned:
        pack_chars = estimate_pack_chars(pack)
        if remaining_count_slots <= 0:
            result.truncated_by_count = True
            result.dropped.append(pack)
            continue
        projected = result.char_count + pack_chars
        if cap_chars > 0 and projected > cap_chars:
            result.truncated_by_chars = True
            result.dropped.append(pack)
            continue
        result.selected.append(pack)
        result.char_count = projected
        remaining_count_slots -= 1

    return result


async def get_workspace_skill_config(
    db: AsyncSession,
    *,
    workspace_id: uuid.UUID,
) -> SkillSelectionConfig:
    """Merge the workspace's ``home_config_json["skills"]`` with the
    platform-wide :data:`SystemSettingKey.SKILL_INJECTION_DEFAULTS`
    row.

    Resolution order, per key, is workspace override → platform row →
    hard-coded default. Missing or malformed values fall back; this
    function never raises (the runtime path treats a config-read
    failure as "use defaults" so the agent loop is never blocked on
    settings).
    """
    platform: dict = {}
    try:
        raw_platform = await get_system_setting(
            db, SystemSettingKey.SKILL_INJECTION_DEFAULTS, default={}
        )
        if isinstance(raw_platform, dict):
            platform = raw_platform
    except Exception:  # pragma: no cover - defensive, system_settings is forgiving
        log.warning(
            "skill_selection.platform_default_read_failed workspace=%s",
            workspace_id,
            exc_info=True,
        )

    ws_overrides: dict = {}
    try:
        ws = await db.get(Workspace, workspace_id)
        if ws is not None:
            block = (ws.home_config_json or {}).get("skills")
            if isinstance(block, dict):
                ws_overrides = block
    except Exception:  # pragma: no cover - defensive
        log.warning(
            "skill_selection.workspace_override_read_failed workspace=%s",
            workspace_id,
            exc_info=True,
        )

    def _merged(key: str, default):
        if key in ws_overrides and ws_overrides[key] is not None:
            return ws_overrides[key]
        if key in platform and platform[key] is not None:
            return platform[key]
        return default

    return SkillSelectionConfig(
        max_active_injected=_coerce_int(
            _merged("max_active_injected", DEFAULT_MAX_ACTIVE_INJECTED),
            DEFAULT_MAX_ACTIVE_INJECTED,
        ),
        max_injected_chars_total=_coerce_int(
            _merged("max_injected_chars_total", DEFAULT_MAX_INJECTED_CHARS_TOTAL),
            DEFAULT_MAX_INJECTED_CHARS_TOTAL,
        ),
        selection_strategy=_coerce_strategy(
            _merged("selection_strategy", DEFAULT_SELECTION_STRATEGY)
        ),
    )


def _coerce_int(value, fallback: int) -> int:
    try:
        out = int(value)
    except (TypeError, ValueError):
        return fallback
    if out < 0:
        return fallback
    return out


def _coerce_strategy(value) -> str:
    if isinstance(value, str) and value in _VALID_STRATEGIES:
        return value
    return DEFAULT_SELECTION_STRATEGY
