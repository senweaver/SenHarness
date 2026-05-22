"""Tests for the workspace x platform skill-injection config merge (M1.8).

Three flows are pinned:

1. Empty workspace overrides → platform default applies.
2. Per-key workspace override wins (only that key is changed; the
   other fields fall through to the platform default).
3. Updating the platform-level row through ``set_system_setting``
   immediately surfaces on the next read for a workspace that has no
   override (proves the resolver is not caching stale defaults inside
   the function).
"""

from __future__ import annotations

import pytest

from app.services.skill_selection import (
    DEFAULT_MAX_ACTIVE_INJECTED,
    DEFAULT_MAX_INJECTED_CHARS_TOTAL,
    DEFAULT_SELECTION_STRATEGY,
    get_workspace_skill_config,
)
from app.services.system_settings import (
    SystemSettingKey,
    delete_system_setting,
    set_system_setting,
)

pytestmark = pytest.mark.asyncio


async def test_no_workspace_override_uses_platform_default(db_session, workspace):
    cfg = await get_workspace_skill_config(db_session, workspace_id=workspace.id)

    assert cfg.max_active_injected == DEFAULT_MAX_ACTIVE_INJECTED
    assert cfg.max_injected_chars_total == DEFAULT_MAX_INJECTED_CHARS_TOTAL
    assert cfg.selection_strategy == DEFAULT_SELECTION_STRATEGY


async def test_workspace_override_wins_per_key(db_session, workspace):
    # Override only the count cap; chars + strategy must keep the platform default.
    workspace.home_config_json = {"skills": {"max_active_injected": 5}}
    await db_session.flush()

    cfg = await get_workspace_skill_config(db_session, workspace_id=workspace.id)

    assert cfg.max_active_injected == 5
    assert cfg.max_injected_chars_total == DEFAULT_MAX_INJECTED_CHARS_TOTAL
    assert cfg.selection_strategy == DEFAULT_SELECTION_STRATEGY


async def test_platform_default_change_visible_on_next_read(db_session, workspace):
    # Move the platform-level cap, ensure the next workspace read sees it.
    await set_system_setting(
        db_session,
        SystemSettingKey.SKILL_INJECTION_DEFAULTS,
        {
            "max_active_injected": 7,
            "max_injected_chars_total": 4000,
            "selection_strategy": "manual_only",
        },
    )
    await db_session.flush()

    cfg = await get_workspace_skill_config(db_session, workspace_id=workspace.id)

    assert cfg.max_active_injected == 7
    assert cfg.max_injected_chars_total == 4000
    assert cfg.selection_strategy == "manual_only"

    # Cleanup so subsequent tests in the same DB observe the canonical default.
    await delete_system_setting(db_session, SystemSettingKey.SKILL_INJECTION_DEFAULTS)
    await db_session.flush()


async def test_invalid_strategy_falls_back_to_default(db_session, workspace):
    workspace.home_config_json = {"skills": {"selection_strategy": "not-a-real-strategy"}}
    await db_session.flush()

    cfg = await get_workspace_skill_config(db_session, workspace_id=workspace.id)

    assert cfg.selection_strategy == DEFAULT_SELECTION_STRATEGY


async def test_negative_workspace_cap_falls_back_to_default(db_session, workspace):
    workspace.home_config_json = {
        "skills": {"max_active_injected": -10, "max_injected_chars_total": -5}
    }
    await db_session.flush()

    cfg = await get_workspace_skill_config(db_session, workspace_id=workspace.id)

    assert cfg.max_active_injected == DEFAULT_MAX_ACTIVE_INJECTED
    assert cfg.max_injected_chars_total == DEFAULT_MAX_INJECTED_CHARS_TOTAL
