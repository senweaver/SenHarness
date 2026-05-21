"""Sub-agent batch settings resolution (M2.5.6).

Single read-side accessor that merges the platform default
(``system_settings.subagent_batch_defaults``) with the per-workspace
override (``workspace.home_config_json["subagent"]``). The per-workspace
override wins on every field; missing fields back-fill from the platform
default.

The shape itself lives in :class:`app.schemas.platform_settings.SubagentBatchDefaults`.
This module is intentionally a thin adapter so the batch service
(``app.agents.harness.subagents.delegate_batch``) and the platform admin
form read the same view of the config.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.workspace import Workspace
from app.schemas.platform_settings import SubagentBatchDefaults
from app.services.system_settings import SystemSettingKey, get_system_setting

__all__ = [
    "WORKSPACE_SUBAGENT_KEY",
    "ResolvedSubagentBatchConfig",
    "get_workspace_subagent_batch_config",
]

# Workspace-level override key on ``workspaces.home_config_json``. The
# block uses the unprefixed ``subagent`` slot — the platform-default
# row keys still carry the ``_default`` suffix so the admin form is
# obvious; here we expose the resolved values as ``batch_enabled`` /
# ``max_batch_size`` / ``max_concurrent`` / ``max_nesting_depth`` to
# match how callers reason about them.
WORKSPACE_SUBAGENT_KEY = "subagent"


@dataclass(slots=True, frozen=True)
class ResolvedSubagentBatchConfig:
    """Per-workspace effective batch configuration.

    The fields drop the ``_default`` suffix because at this layer the
    knob is no longer a default — it's the binding value the runtime
    enforces for every spawn.
    """

    batch_enabled: bool
    max_batch_size: int
    max_concurrent: int
    max_nesting_depth: int

    @classmethod
    def from_defaults(cls, defaults: SubagentBatchDefaults) -> "ResolvedSubagentBatchConfig":
        return cls(
            batch_enabled=bool(defaults.batch_enabled_default),
            max_batch_size=int(defaults.max_batch_size_default),
            max_concurrent=int(defaults.max_concurrent_default),
            max_nesting_depth=int(defaults.max_nesting_depth_default),
        )


async def _read_platform_defaults(db: AsyncSession) -> SubagentBatchDefaults:
    raw = await get_system_setting(
        db, SystemSettingKey.SUBAGENT_BATCH_DEFAULTS, default=None
    )
    if not isinstance(raw, dict):
        return SubagentBatchDefaults()
    try:
        return SubagentBatchDefaults.model_validate(raw)
    except Exception:
        return SubagentBatchDefaults()


def _merge_overrides(
    base: SubagentBatchDefaults,
    overrides: Mapping[str, Any] | None,
) -> ResolvedSubagentBatchConfig:
    """Apply workspace overrides on top of platform defaults.

    Workspace blocks may use either the canonical short keys
    (``batch_enabled`` / ``max_batch_size`` / ``max_concurrent`` /
    ``max_nesting_depth``) or the platform-style ``_default`` keys; we
    accept both so an admin who copies the platform JSON into the
    workspace block doesn't get a silent no-op.
    """
    resolved = ResolvedSubagentBatchConfig.from_defaults(base)
    if not isinstance(overrides, Mapping):
        return resolved

    def _lookup(*keys: str) -> Any:
        for key in keys:
            if key in overrides and overrides[key] is not None:
                return overrides[key]
        return None

    enabled_raw = _lookup("batch_enabled", "batch_enabled_default")
    if isinstance(enabled_raw, bool):
        batch_enabled = enabled_raw
    else:
        batch_enabled = resolved.batch_enabled

    def _coerce_int(raw: Any, *, lo: int, hi: int, default: int) -> int:
        if raw is None:
            return default
        try:
            value = int(raw)
        except (TypeError, ValueError):
            return default
        return max(lo, min(hi, value))

    max_batch_size = _coerce_int(
        _lookup("max_batch_size", "max_batch_size_default"),
        lo=1,
        hi=100,
        default=resolved.max_batch_size,
    )
    max_concurrent = _coerce_int(
        _lookup("max_concurrent", "max_concurrent_per_parent", "max_concurrent_default"),
        lo=1,
        hi=20,
        default=resolved.max_concurrent,
    )
    max_nesting_depth = _coerce_int(
        _lookup("max_nesting_depth", "max_nesting_depth_default"),
        lo=1,
        hi=10,
        default=resolved.max_nesting_depth,
    )

    return ResolvedSubagentBatchConfig(
        batch_enabled=batch_enabled,
        max_batch_size=max_batch_size,
        max_concurrent=max_concurrent,
        max_nesting_depth=max_nesting_depth,
    )


async def get_workspace_subagent_batch_config(
    db: AsyncSession, *, workspace_id: uuid.UUID
) -> ResolvedSubagentBatchConfig:
    """Return the merged batch-spawn config for ``workspace_id``."""
    defaults = await _read_platform_defaults(db)

    ws_row = (
        await db.execute(select(Workspace).where(Workspace.id == workspace_id))
    ).scalar_one_or_none()
    overrides: Mapping[str, Any] | None = None
    if ws_row is not None and isinstance(ws_row.home_config_json, dict):
        candidate = ws_row.home_config_json.get(WORKSPACE_SUBAGENT_KEY)
        if isinstance(candidate, Mapping):
            overrides = candidate

    return _merge_overrides(defaults, overrides)
