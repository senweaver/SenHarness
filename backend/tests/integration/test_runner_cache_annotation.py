"""Runner-level integration tests for M2.5.9 provider-side cache markers.

The runner's cache wiring lives in
:mod:`app.agents.kernels.native._cache_wiring`. Driving the full
``_pydantic_ai_stream`` would require a real LLM key; instead we
exercise the same prepare → finalize seam the runner uses with a
stub ``Agent`` that just exposes ``model_settings``. That gives us
end-to-end coverage of:

* Anthropic happy path: ``anthropic_cache_*`` settings actually land
  on the agent's ``model_settings`` bag, an audit row is written.
* Adaptive disable: 5 consecutive misses → ``cache.adaptive_disabled``
  audit row + the next prepare() short-circuits.
* Hit signal: a usage object with ``cache_read_input_tokens`` flips
  the tracker into the recovered state and writes ``cache.hit``.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from sqlalchemy import select

from app.agents.kernels.native import _cache_wiring as wiring
from app.db.models.audit import AuditEvent
from app.services import cache_adaptive

pytestmark = pytest.mark.asyncio


class _FakeRedis:
    """Local fake — same shape as :mod:`test_cache_adaptive.FakeRedis`."""

    def __init__(self) -> None:
        self.store: dict[str, dict[str, str]] = {}
        self.ttls: dict[str, int] = {}

    async def hgetall(self, key: str) -> dict[str, str]:
        return dict(self.store.get(key, {}))

    async def hset(self, key: str, *, mapping: dict[str, Any]) -> int:
        bucket = self.store.setdefault(key, {})
        for k, v in mapping.items():
            bucket[k] = str(v)
        return len(mapping)

    async def expire(self, key: str, ttl_seconds: int) -> bool:
        self.ttls[key] = int(ttl_seconds)
        return True

    async def delete(self, key: str) -> int:
        existed = self.store.pop(key, None)
        self.ttls.pop(key, None)
        return 1 if existed is not None else 0


class _FakeAgent:
    """Stand-in for a pydantic-ai ``Agent``.

    Exposes only ``model_settings`` because that's the field the
    cache wiring mutates.
    """

    def __init__(self) -> None:
        self.model_settings: dict[str, Any] = {}


class _FakeUsage:
    def __init__(self, cache_tokens: int) -> None:
        self.cache_read_input_tokens = cache_tokens
        self.input_tokens = 1000
        self.output_tokens = 200


async def _enable_cache_for_workspace(workspace, db_session) -> None:
    """Drop the workspace into the home_config_json shape the cache
    wiring expects + commit so a fresh DB session can read it."""
    home = dict(workspace.home_config_json or {})
    providers = dict(home.get("providers") or {})
    providers["cache_control"] = {
        "enabled": True,
        "min_prompt_tokens": 1,
        "max_breakpoints": 4,
        "ttl": "5m",
        "adaptive_disable_threshold": 5,
        "adaptive_disable_duration_seconds": 60,
    }
    home["providers"] = providers
    workspace.home_config_json = home
    await db_session.commit()


@pytest.fixture(autouse=True)
def _reset_state():
    cache_adaptive.reset_cache()
    yield
    cache_adaptive.reset_cache()


# ─── Tests ─────────────────────────────────────────────────
async def test_anthropic_prepare_lands_settings_and_audit(
    db_session, workspace
):
    await _enable_cache_for_workspace(workspace, db_session)

    redis = _FakeRedis()
    agent = _FakeAgent()
    result = await wiring.prepare(
        agent=agent,
        workspace_id=workspace.id,
        provider_kind="anthropic",
        redis=redis,
    )

    assert result.enabled is True
    assert result.annotated is True
    assert agent.model_settings["anthropic_cache"] == "5m"
    assert agent.model_settings["anthropic_cache_messages"] == "5m"

    rows = (
        await db_session.execute(
            select(AuditEvent).where(AuditEvent.action == "cache.annotated")
        )
    ).scalars().all()
    assert any(r.workspace_id == workspace.id for r in rows)


async def test_unsupported_provider_skips_silently(db_session, workspace):
    await _enable_cache_for_workspace(workspace, db_session)
    agent = _FakeAgent()
    result = await wiring.prepare(
        agent=agent,
        workspace_id=workspace.id,
        provider_kind="openai",
        redis=_FakeRedis(),
    )
    assert result.enabled is False
    assert result.annotated is False
    assert agent.model_settings == {}

    rows = (
        await db_session.execute(
            select(AuditEvent).where(AuditEvent.action == "cache.annotated")
        )
    ).scalars().all()
    assert all(r.workspace_id != workspace.id for r in rows)


async def test_finalize_records_hit_and_writes_audit(
    db_session, workspace
):
    await _enable_cache_for_workspace(workspace, db_session)
    redis = _FakeRedis()
    agent = _FakeAgent()
    result = await wiring.prepare(
        agent=agent,
        workspace_id=workspace.id,
        provider_kind="anthropic",
        redis=redis,
    )
    assert result.enabled is True

    hit_tokens = await wiring.finalize(
        result=result,
        usage=_FakeUsage(cache_tokens=512),
        redis=redis,
        actor_identity_id=None,
    )
    assert hit_tokens == 512

    snapshot = await cache_adaptive.get_stats(
        redis,
        workspace_id=workspace.id,
        provider_kind="anthropic",
    )
    assert snapshot.total_hits == 1
    assert snapshot.consecutive_misses == 0

    rows = (
        await db_session.execute(
            select(AuditEvent).where(AuditEvent.action == "cache.hit")
        )
    ).scalars().all()
    assert any(r.workspace_id == workspace.id for r in rows)


async def test_five_consecutive_misses_disable_and_short_circuit(
    db_session, workspace
):
    await _enable_cache_for_workspace(workspace, db_session)
    redis = _FakeRedis()

    for _ in range(5):
        agent = _FakeAgent()
        result = await wiring.prepare(
            agent=agent,
            workspace_id=workspace.id,
            provider_kind="anthropic",
            redis=redis,
        )
        await wiring.finalize(
            result=result,
            usage=_FakeUsage(cache_tokens=0),
            redis=redis,
            actor_identity_id=None,
        )

    rows = (
        await db_session.execute(
            select(AuditEvent).where(
                AuditEvent.action == "cache.adaptive_disabled"
            )
        )
    ).scalars().all()
    assert any(r.workspace_id == workspace.id for r in rows)

    next_agent = _FakeAgent()
    next_result = await wiring.prepare(
        agent=next_agent,
        workspace_id=workspace.id,
        provider_kind="anthropic",
        redis=redis,
    )
    assert next_result.disabled_by_adaptive is True
    assert next_result.annotated is False
    assert next_agent.model_settings == {}

    skip_rows = (
        await db_session.execute(
            select(AuditEvent).where(
                AuditEvent.action == "cache.adaptive_skipped"
            )
        )
    ).scalars().all()
    assert any(r.workspace_id == workspace.id for r in skip_rows)


async def test_workspace_disabled_skips_wiring(db_session, workspace):
    home = dict(workspace.home_config_json or {})
    providers = dict(home.get("providers") or {})
    providers["cache_control"] = {"enabled": False}
    home["providers"] = providers
    workspace.home_config_json = home
    await db_session.commit()

    agent = _FakeAgent()
    result = await wiring.prepare(
        agent=agent,
        workspace_id=workspace.id,
        provider_kind="anthropic",
        redis=_FakeRedis(),
    )
    assert result.enabled is False
    assert result.annotated is False
    assert agent.model_settings == {}
