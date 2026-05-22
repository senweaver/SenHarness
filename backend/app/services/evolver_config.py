"""Evolver settings resolution (M2.6).

Single read-side accessor that merges the platform default
(``system_settings.evolver_defaults``) with the per-workspace override
(``workspace.home_config_json["evolver"]``). The per-workspace override
wins on every field; missing fields back-fill from the platform
default.

The shape itself lives in :class:`app.schemas.platform_settings.EvolverSettings`
— this module is intentionally a thin adapter so the propose verbs
(M2.7), the evolver agent (M2.2), and the workflow runner (M2.3) all
read the same view of the config.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.workspace import Workspace
from app.schemas.platform_settings import EvolverSettings
from app.services.system_settings import SystemSettingKey, get_system_setting

__all__ = [
    "WORKSPACE_EVOLVER_KEY",
    "get_workspace_evolver_config",
]

# Workspace-level override key on ``workspaces.home_config_json``.
WORKSPACE_EVOLVER_KEY = "evolver"


async def _read_platform_defaults(db: AsyncSession) -> dict[str, Any]:
    """Read the platform default row + back-compat with the legacy key.

    Mirrors ``platform_settings._load_section``'s back-compat branch
    so callers reading the merged config see the same EvolverSettings
    shape regardless of whether the deployment has run M2.6 yet.
    """
    raw = await get_system_setting(db, SystemSettingKey.EVOLVER_DEFAULTS, default=None)
    if not isinstance(raw, dict):
        legacy = await get_system_setting(db, SystemSettingKey.EVOLVER, default=None)
        raw = legacy if isinstance(legacy, dict) else None
    if raw is None:
        return EvolverSettings().model_dump(mode="json")
    return raw


def _deep_merge(base: Mapping[str, Any], override: Mapping[str, Any]) -> dict[str, Any]:
    """Override-wins merge that walks one nested level for sub-models.

    ``approval_ttl_days`` and ``auto_verifier`` are dict-shaped sub-
    models; a workspace that wants to bump only one TTL must not have
    its other TTLs reset to schema defaults. The merge therefore
    recurses one level when both sides are dicts.
    """
    out: dict[str, Any] = {**base}
    for key, value in override.items():
        if value is None:
            continue
        existing = out.get(key)
        if isinstance(existing, Mapping) and isinstance(value, Mapping):
            out[key] = _deep_merge(existing, value)
        else:
            out[key] = value
    return out


async def get_workspace_evolver_config(
    db: AsyncSession, *, workspace_id: uuid.UUID
) -> EvolverSettings:
    """Return the merged + validated evolver config for ``workspace_id``."""
    platform_raw = await _read_platform_defaults(db)
    merged: dict[str, Any] = dict(platform_raw)

    ws_row = (
        await db.execute(select(Workspace).where(Workspace.id == workspace_id))
    ).scalar_one_or_none()
    if ws_row is not None and isinstance(ws_row.home_config_json, dict):
        ws_overrides = ws_row.home_config_json.get(WORKSPACE_EVOLVER_KEY)
        if isinstance(ws_overrides, dict):
            merged = _deep_merge(merged, ws_overrides)

    return EvolverSettings.model_validate(merged)
