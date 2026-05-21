"""Tests for ``EvolverSettings`` schema + workspace resolution (M2.6).

Pure tests cover the pydantic shape (defaults, range clamping, the
backward-compat ``model_validator``). DB-backed tests assert that
:func:`app.services.evolver_config.get_workspace_evolver_config`
correctly merges the platform default with the workspace override.
"""

from __future__ import annotations

import pytest

from app.schemas.platform_settings import EvolverSettings


def test_evolver_settings_default_is_disabled():
    cfg = EvolverSettings()
    assert cfg.enabled is False
    assert cfg.engine == "workflow"
    assert cfg.publish_mode == "approval_required"
    assert cfg.evolver_breaker_strikes == 5
    assert cfg.evolver_breaker_window_seconds == 300
    assert cfg.evolver_rate_per_minute == 10
    assert cfg.aux_model_evolver is None
    assert cfg.approval_ttl_days.skill_pack_create == 14
    assert cfg.approval_ttl_days.skill_pack_patch == 14
    assert cfg.approval_ttl_days.skill_pack_edit == 14
    assert cfg.approval_ttl_days.skill_pack_delete == 7
    assert cfg.approval_ttl_days.skill_pack_write_file == 14
    assert cfg.approval_ttl_days.skill_pack_remove_file == 7
    assert cfg.auto_verifier.enabled is True
    assert cfg.auto_verifier.min_score_delta == 0.05
    assert cfg.auto_verifier.min_replay_artifacts == 3


def test_evolver_settings_legacy_keys_absorbed():
    """The M0.13 placeholder fields map cleanly into the M2.6 shape."""
    cfg = EvolverSettings.model_validate(
        {
            "workspace_can_enable": True,  # discarded
            "platform_aux_model_recommendation": "openai:gpt-4o-mini",
        }
    )
    assert cfg.aux_model_evolver == "openai:gpt-4o-mini"
    # The legacy ``workspace_can_enable`` was admin-only metadata and
    # never gated anything — it intentionally does NOT flip ``enabled``.
    assert cfg.enabled is False


def test_evolver_settings_partial_payload_back_fills():
    cfg = EvolverSettings.model_validate(
        {"enabled": True, "approval_ttl_days": {"skill_pack_delete": 30}}
    )
    assert cfg.enabled is True
    assert cfg.approval_ttl_days.skill_pack_delete == 30
    # untouched TTLs keep schema defaults
    assert cfg.approval_ttl_days.skill_pack_create == 14


def test_evolver_settings_clamps_out_of_range():
    with pytest.raises(ValueError):
        EvolverSettings.model_validate({"evolver_breaker_strikes": 0})
    with pytest.raises(ValueError):
        EvolverSettings.model_validate({"approval_ttl_days": {"skill_pack_create": 0}})
    with pytest.raises(ValueError):
        EvolverSettings.model_validate({"engine": "bogus"})


# ─── DB-backed merge ─────────────────────────────────────────
# pyproject ``asyncio_mode = "auto"`` discovers + decorates async
# tests automatically; no pytestmark needed.


async def test_workspace_override_wins_over_platform_default(
    db_session, workspace
):
    from app.services.evolver_config import get_workspace_evolver_config

    workspace.home_config_json = {
        "evolver": {
            "enabled": True,
            "evolver_rate_per_minute": 30,
            "approval_ttl_days": {"skill_pack_delete": 14},
        }
    }
    await db_session.flush()

    cfg = await get_workspace_evolver_config(
        db_session, workspace_id=workspace.id
    )
    assert cfg.enabled is True
    assert cfg.evolver_rate_per_minute == 30
    assert cfg.approval_ttl_days.skill_pack_delete == 14
    # other TTL fields fall back to platform default (= schema default
    # when no platform row exists yet)
    assert cfg.approval_ttl_days.skill_pack_create == 14


async def test_workspace_default_falls_back_to_platform(
    db_session, workspace
):
    from app.services import system_settings as svc
    from app.services.evolver_config import get_workspace_evolver_config

    await svc.set_system_setting(
        db_session,
        svc.SystemSettingKey.EVOLVER_DEFAULTS,
        {
            "enabled": True,
            "aux_model_evolver": "openai:gpt-4o",
            "evolver_rate_per_minute": 25,
        },
    )
    await db_session.flush()

    cfg = await get_workspace_evolver_config(
        db_session, workspace_id=workspace.id
    )
    assert cfg.enabled is True
    assert cfg.aux_model_evolver == "openai:gpt-4o"
    assert cfg.evolver_rate_per_minute == 25


async def test_legacy_evolver_row_used_when_new_key_absent(
    db_session, workspace
):
    """A deployment that ran M0.13 → M1.x sees the legacy row migrate
    transparently on the first read.
    """
    from app.services import system_settings as svc
    from app.services.evolver_config import get_workspace_evolver_config

    # Wipe the auto-seeded EVOLVER_DEFAULTS row that ``_DEFAULTS`` may
    # have backfilled, simulating a deployment whose only evolver row
    # is the legacy placeholder.
    await svc.delete_system_setting(db_session, svc.SystemSettingKey.EVOLVER_DEFAULTS)
    await svc.set_system_setting(
        db_session,
        svc.SystemSettingKey.EVOLVER,
        {
            "workspace_can_enable": True,
            "platform_aux_model_recommendation": "openai:gpt-4o-mini",
        },
    )
    await db_session.flush()

    cfg = await get_workspace_evolver_config(
        db_session, workspace_id=workspace.id
    )
    # legacy ``workspace_can_enable=True`` does NOT flip enabled; the
    # admin must explicitly opt the workspace in via the new shape.
    assert cfg.enabled is False
    assert cfg.aux_model_evolver == "openai:gpt-4o-mini"
