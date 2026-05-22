"""Unit tests for the M3.6 routing-config resolver.

These tests exercise the merge precedence between the platform-level
``SessionRoutingDefaults`` and the workspace-level
``home_config_json["session_routing"]`` block without spinning up a
real DB. The resolver is the single gate the dispatcher reads to
decide whether to take the cross-platform path or fall back to
per-channel — every routing decision flows through this function so
the contract has to be airtight.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

import pytest

from app.schemas.platform_settings.session_routing import SessionRoutingDefaults
from app.services.logical_thread import RoutingConfig, get_routing_config
from app.services.system_settings import SystemSettingKey


@dataclass
class _FakeWorkspace:
    home_config_json: dict[str, Any]


class _FakeWorkspaceRepo:
    def __init__(self, ws: _FakeWorkspace | None) -> None:
        self._ws = ws

    async def get(self, _id: uuid.UUID, **_kw: Any) -> _FakeWorkspace | None:
        return self._ws


async def _run(monkeypatch, *, platform: dict[str, Any], home: dict[str, Any]):
    async def fake_get_setting(_db, key, default=None):  # type: ignore[no-untyped-def]
        if key == SystemSettingKey.SESSION_ROUTING_DEFAULTS:
            return platform
        return default

    fake_ws = _FakeWorkspace(home_config_json=home)

    def fake_repo_factory(_db):  # type: ignore[no-untyped-def]
        return _FakeWorkspaceRepo(fake_ws)

    monkeypatch.setattr("app.services.logical_thread.get_system_setting", fake_get_setting)
    monkeypatch.setattr("app.services.logical_thread.WorkspaceRepository", fake_repo_factory)

    return await get_routing_config(
        db=None,  # type: ignore[arg-type]
        workspace_id=uuid.uuid4(),
    )


# ─── Default contract ────────────────────────────────────────
async def test_default_disabled_when_no_overrides_present(monkeypatch) -> None:
    cfg = await _run(monkeypatch, platform={}, home={})
    assert isinstance(cfg, RoutingConfig)
    assert cfg.cross_platform_enabled is False
    assert cfg.pairing_required is True
    assert cfg.pairing_code_ttl_seconds == 600
    assert cfg.default_strategy == "per_channel"


async def test_platform_default_schema_matches_resolver_default() -> None:
    schema_default = SessionRoutingDefaults().model_dump()
    assert schema_default["cross_platform_enabled"] is False
    assert schema_default["pairing_required_for_cross_platform"] is True
    assert schema_default["pairing_code_ttl_seconds"] == 600
    assert schema_default["default_strategy"] == "per_channel"


# ─── Platform-level overrides ────────────────────────────────
async def test_platform_enables_cross_platform_when_workspace_silent(
    monkeypatch,
) -> None:
    cfg = await _run(
        monkeypatch,
        platform={"cross_platform_enabled": True},
        home={},
    )
    assert cfg.cross_platform_enabled is True
    assert cfg.pairing_required is True


# ─── Workspace overrides win over platform ───────────────────
async def test_workspace_override_wins_over_platform(monkeypatch) -> None:
    cfg = await _run(
        monkeypatch,
        platform={"cross_platform_enabled": True, "pairing_code_ttl_seconds": 300},
        home={
            "session_routing": {
                "cross_platform_enabled": False,
                "pairing_code_ttl_seconds": 900,
            }
        },
    )
    assert cfg.cross_platform_enabled is False
    assert cfg.pairing_code_ttl_seconds == 900


async def test_workspace_partial_override_backfills_from_platform(monkeypatch) -> None:
    cfg = await _run(
        monkeypatch,
        platform={"cross_platform_enabled": True, "pairing_code_ttl_seconds": 1200},
        home={"session_routing": {"pairing_required_for_cross_platform": False}},
    )
    assert cfg.cross_platform_enabled is True
    assert cfg.pairing_required is False
    assert cfg.pairing_code_ttl_seconds == 1200


# ─── Defensive parsing ───────────────────────────────────────
async def test_non_dict_workspace_block_is_ignored(monkeypatch) -> None:
    cfg = await _run(
        monkeypatch,
        platform={"cross_platform_enabled": True},
        home={"session_routing": "garbage"},  # type: ignore[dict-item]
    )
    assert cfg.cross_platform_enabled is True


async def test_non_dict_platform_payload_is_ignored(monkeypatch) -> None:
    async def fake_get_setting(_db, _key, default=None):  # type: ignore[no-untyped-def]
        return ["not", "a", "dict"]

    monkeypatch.setattr("app.services.logical_thread.get_system_setting", fake_get_setting)

    def fake_repo_factory(_db):  # type: ignore[no-untyped-def]
        return _FakeWorkspaceRepo(_FakeWorkspace(home_config_json={}))

    monkeypatch.setattr("app.services.logical_thread.WorkspaceRepository", fake_repo_factory)

    cfg = await get_routing_config(
        db=None,  # type: ignore[arg-type]
        workspace_id=uuid.uuid4(),
    )
    assert cfg.cross_platform_enabled is False  # default takes over


# ─── Sanity: every field has a sensible bound ────────────────
def test_pairing_code_ttl_lower_bound_in_schema() -> None:
    with pytest.raises(Exception):
        SessionRoutingDefaults(pairing_code_ttl_seconds=10)


def test_pairing_code_ttl_upper_bound_in_schema() -> None:
    with pytest.raises(Exception):
        SessionRoutingDefaults(pairing_code_ttl_seconds=10**6)
