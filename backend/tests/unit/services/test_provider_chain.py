"""Unit tests for :mod:`app.services.provider_chain` (M2.5.3).

Verifies the four resolution branches independently of pydantic-ai:

* Empty workspace chain + no platform default → falls back to the
  single-element chain synthesised from the primary upstream.
* Workspace-supplied chain wins over the platform default.
* Cooldown filtering removes degraded entries.
* Every entry in cooldown → returns the original parsed chain (warn).
"""

from __future__ import annotations

from typing import Any

import pytest

from app.services import provider_health as health_svc
from app.services.provider_chain import (
    ProviderFailoverConfig,
    get_provider_chain,
    parse_chain_entry,
)


# ─── Fake redis (mirrors test_provider_health) ─────────────
class FakeRedis:
    def __init__(self) -> None:
        self.store: dict[str, dict[str, str]] = {}

    async def hgetall(self, key: str) -> dict[str, str]:
        return dict(self.store.get(key, {}))

    async def hset(self, key: str, *, mapping: dict[str, Any]) -> int:
        bucket = self.store.setdefault(key, {})
        for k, v in mapping.items():
            bucket[k] = str(v)
        return len(mapping)

    async def expire(self, key: str, ttl_seconds: int) -> bool:  # noqa: ARG002
        return True

    async def delete(self, key: str) -> int:
        return 1 if self.store.pop(key, None) is not None else 0


@pytest.fixture(autouse=True)
def _reset_local_cache():
    health_svc.reset_cache()
    yield
    health_svc.reset_cache()


# ─── parse_chain_entry pure tests ──────────────────────────
def test_parse_simple():
    entry = parse_chain_entry("openai:gpt-5")
    assert entry is not None
    assert entry.provider_kind == "openai"
    assert entry.model_id == "gpt-5"
    assert entry.upstream_label == "openai:gpt-5"


def test_parse_normalises_whitespace_and_case():
    entry = parse_chain_entry("  Openai : gpt-4o-mini  ")
    assert entry is not None
    assert entry.provider_kind == "openai"
    assert entry.model_id == "gpt-4o-mini"


def test_parse_rejects_blank():
    assert parse_chain_entry("") is None
    assert parse_chain_entry("   ") is None
    assert parse_chain_entry(None) is None  # type: ignore[arg-type]


def test_parse_rejects_missing_separator():
    assert parse_chain_entry("openai-gpt-5") is None


def test_parse_rejects_blank_halves():
    assert parse_chain_entry(":gpt-5") is None
    assert parse_chain_entry("openai:") is None


# ─── get_provider_chain DB-backed tests ────────────────────
async def _seed_workspace_failover(
    db_session, workspace, *, enabled: bool, chain: list[str] | None = None
):
    """In-place edit of workspace.home_config_json[providers] block."""
    home = dict(workspace.home_config_json or {})
    providers = dict(home.get("providers") or {})
    providers["failover_enabled"] = enabled
    if chain is not None:
        providers["failover_chain"] = chain
    home["providers"] = providers
    workspace.home_config_json = home
    await db_session.flush()


async def test_empty_chain_synthesises_primary(db_session, workspace):
    cfg = ProviderFailoverConfig(
        enabled=True,
        chain_raw=[],
        failover_max_attempts=3,
        cooldown_threshold=3,
        cooldown_seconds=300,
    )
    chain = await get_provider_chain(
        db_session,
        workspace_id=workspace.id,
        primary_upstream="openai:gpt-5",
        config=cfg,
        redis=FakeRedis(),
    )
    assert len(chain) == 1
    assert chain[0].upstream_label == "openai:gpt-5"


async def test_chain_dedup_keeps_first(db_session, workspace):
    cfg = ProviderFailoverConfig(
        enabled=True,
        chain_raw=["openai:gpt-5", "openai:gpt-5", "deepseek:v3"],
        failover_max_attempts=5,
        cooldown_threshold=3,
        cooldown_seconds=300,
    )
    chain = await get_provider_chain(
        db_session,
        workspace_id=workspace.id,
        primary_upstream="openai:gpt-5",
        config=cfg,
        redis=FakeRedis(),
    )
    assert [(e.provider_kind, e.model_id) for e in chain] == [
        ("openai", "gpt-5"),
        ("deepseek", "v3"),
    ]


async def test_chain_skips_cooldown_entries(db_session, workspace):
    redis = FakeRedis()
    # Trip cooldown on entry 0.
    for _ in range(3):
        await health_svc.record_failure(
            redis,
            provider_kind="openai",
            model_id="gpt-5",
            failure_kind=health_svc.FailureKind.RATE_LIMIT,
            cooldown_threshold=3,
            cooldown_seconds=300,
        )
    cfg = ProviderFailoverConfig(
        enabled=True,
        chain_raw=["openai:gpt-5", "deepseek:v3", "anthropic:sonnet"],
        failover_max_attempts=5,
        cooldown_threshold=3,
        cooldown_seconds=300,
    )
    chain = await get_provider_chain(
        db_session,
        workspace_id=workspace.id,
        primary_upstream="openai:gpt-5",
        config=cfg,
        redis=redis,
    )
    labels = [e.upstream_label for e in chain]
    assert "openai:gpt-5" not in labels
    assert labels == ["deepseek:v3", "anthropic:sonnet"]


async def test_all_cooldown_falls_back_to_full_chain(db_session, workspace):
    redis = FakeRedis()
    for kind, model in (("openai", "gpt-5"), ("deepseek", "v3")):
        for _ in range(3):
            await health_svc.record_failure(
                redis,
                provider_kind=kind,
                model_id=model,
                failure_kind=health_svc.FailureKind.RATE_LIMIT,
                cooldown_threshold=3,
                cooldown_seconds=300,
            )
    cfg = ProviderFailoverConfig(
        enabled=True,
        chain_raw=["openai:gpt-5", "deepseek:v3"],
        failover_max_attempts=5,
        cooldown_threshold=3,
        cooldown_seconds=300,
    )
    chain = await get_provider_chain(
        db_session,
        workspace_id=workspace.id,
        primary_upstream="openai:gpt-5",
        config=cfg,
        redis=redis,
    )
    # Even though both are in cooldown, we return the full chain so the
    # runner can still attempt (cooldown may be stale across processes).
    labels = [e.upstream_label for e in chain]
    assert labels == ["openai:gpt-5", "deepseek:v3"]


async def test_chain_capped_at_max_attempts(db_session, workspace):
    cfg = ProviderFailoverConfig(
        enabled=True,
        chain_raw=[
            "openai:gpt-5",
            "deepseek:v3",
            "anthropic:sonnet",
            "openrouter:gpt-4",
        ],
        failover_max_attempts=2,
        cooldown_threshold=3,
        cooldown_seconds=300,
    )
    chain = await get_provider_chain(
        db_session,
        workspace_id=workspace.id,
        primary_upstream="openai:gpt-5",
        config=cfg,
        redis=FakeRedis(),
    )
    assert [e.upstream_label for e in chain] == [
        "openai:gpt-5",
        "deepseek:v3",
    ]


async def test_workspace_chain_wins_over_platform_default(
    db_session, workspace
):
    await _seed_workspace_failover(
        db_session, workspace, enabled=True, chain=["deepseek:v3"]
    )
    from app.services.provider_chain import get_workspace_failover_config

    cfg = await get_workspace_failover_config(
        db_session, workspace_id=workspace.id
    )
    assert cfg.enabled is True
    assert cfg.chain_raw == ["deepseek:v3"]


async def test_failover_enabled_default_off(db_session, workspace):
    from app.services.provider_chain import get_workspace_failover_config

    cfg = await get_workspace_failover_config(
        db_session, workspace_id=workspace.id
    )
    assert cfg.enabled is False
