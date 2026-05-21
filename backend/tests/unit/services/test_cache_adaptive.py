"""Pure unit tests for :mod:`app.services.cache_adaptive` (M2.5.9).

Same fake-Redis trick as :mod:`test_provider_health` so the adaptive
disable window can be exercised without a real Redis. The tests
restore the module-level threshold + duration on teardown so a test
that calls ``configure_thresholds`` doesn't bleed into siblings.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from app.services import cache_adaptive


# ─── Fake redis ─────────────────────────────────────────────
class FakeRedis:
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


@pytest.fixture(autouse=True)
def _reset_state():
    cache_adaptive.reset_cache()
    original_threshold = cache_adaptive.ADAPTIVE_DISABLE_THRESHOLD
    original_duration = cache_adaptive.ADAPTIVE_DISABLE_DURATION_SECONDS
    yield
    cache_adaptive.reset_cache()
    cache_adaptive.configure_thresholds(
        threshold=original_threshold,
        duration_seconds=original_duration,
    )


# ─── Hit / miss bookkeeping ────────────────────────────────
async def test_hit_resets_consecutive_misses():
    redis = FakeRedis()
    ws = uuid.uuid4()
    for _ in range(3):
        snap = await cache_adaptive.record_cache_result(
            redis,
            workspace_id=ws,
            provider_kind="anthropic",
            hit=False,
        )
    assert snap.consecutive_misses == 3

    snap = await cache_adaptive.record_cache_result(
        redis,
        workspace_id=ws,
        provider_kind="anthropic",
        hit=True,
        hit_tokens=128,
    )
    assert snap.consecutive_misses == 0
    assert snap.total_hits == 1
    assert snap.total_misses == 3
    assert snap.last_hit_at is not None


async def test_five_consecutive_misses_trip_disable_window():
    redis = FakeRedis()
    ws = uuid.uuid4()
    cache_adaptive.configure_thresholds(threshold=5, duration_seconds=60)

    for _ in range(4):
        snap = await cache_adaptive.record_cache_result(
            redis,
            workspace_id=ws,
            provider_kind="anthropic",
            hit=False,
        )
    assert snap.disabled_until is None

    snap = await cache_adaptive.record_cache_result(
        redis,
        workspace_id=ws,
        provider_kind="anthropic",
        hit=False,
    )
    assert snap.consecutive_misses == 5
    assert snap.disabled_until is not None
    assert snap.extras.get("just_disabled") is True
    assert snap.disabled_until > datetime.now(UTC)


async def test_is_cache_disabled_predicate_within_window():
    redis = FakeRedis()
    ws = uuid.uuid4()
    cache_adaptive.configure_thresholds(threshold=2, duration_seconds=60)
    for _ in range(2):
        await cache_adaptive.record_cache_result(
            redis,
            workspace_id=ws,
            provider_kind="anthropic",
            hit=False,
        )
    disabled = await cache_adaptive.is_cache_disabled(
        redis, workspace_id=ws, provider_kind="anthropic"
    )
    assert disabled is True


async def test_is_cache_disabled_false_after_window_expired():
    redis = FakeRedis()
    ws = uuid.uuid4()
    cache_adaptive.configure_thresholds(threshold=2, duration_seconds=60)
    for _ in range(2):
        await cache_adaptive.record_cache_result(
            redis,
            workspace_id=ws,
            provider_kind="anthropic",
            hit=False,
        )

    cache_adaptive.reset_cache()
    key = f"cache_adaptive:{ws}:anthropic"
    redis.store[key]["disabled_until"] = (
        (datetime.now(UTC) - timedelta(seconds=10)).isoformat()
    )
    disabled = await cache_adaptive.is_cache_disabled(
        redis, workspace_id=ws, provider_kind="anthropic"
    )
    assert disabled is False


async def test_recovery_audit_signal():
    redis = FakeRedis()
    ws = uuid.uuid4()
    cache_adaptive.configure_thresholds(threshold=2, duration_seconds=60)
    for _ in range(2):
        await cache_adaptive.record_cache_result(
            redis,
            workspace_id=ws,
            provider_kind="anthropic",
            hit=False,
        )

    # Force the disable window into the past so a fresh hit registers
    # as recovery rather than a still-disabled hit.
    cache_adaptive.reset_cache()
    key = f"cache_adaptive:{ws}:anthropic"
    redis.store[key]["disabled_until"] = (
        (datetime.now(UTC) - timedelta(seconds=10)).isoformat()
    )

    snap = await cache_adaptive.record_cache_result(
        redis,
        workspace_id=ws,
        provider_kind="anthropic",
        hit=True,
        hit_tokens=64,
    )
    assert snap.disabled_until is None
    assert snap.extras.get("just_recovered") is True


async def test_redis_unreachable_fails_open():
    """A degraded Redis must not flip cache into a permanent disable."""
    ws = uuid.uuid4()
    disabled = await cache_adaptive.is_cache_disabled(
        None, workspace_id=ws, provider_kind="anthropic"
    )
    assert disabled is False

    snap = await cache_adaptive.get_stats(
        None, workspace_id=ws, provider_kind="anthropic"
    )
    assert snap.consecutive_misses == 0


async def test_distinct_providers_track_independently():
    redis = FakeRedis()
    ws = uuid.uuid4()
    cache_adaptive.configure_thresholds(threshold=3, duration_seconds=60)

    for _ in range(3):
        await cache_adaptive.record_cache_result(
            redis,
            workspace_id=ws,
            provider_kind="anthropic",
            hit=False,
        )
    or_snap = await cache_adaptive.get_stats(
        redis, workspace_id=ws, provider_kind="openrouter"
    )
    anthropic_snap = await cache_adaptive.get_stats(
        redis, workspace_id=ws, provider_kind="anthropic"
    )
    assert anthropic_snap.disabled_until is not None
    assert or_snap.disabled_until is None


async def test_cross_workspace_isolation():
    redis = FakeRedis()
    ws_a = uuid.uuid4()
    ws_b = uuid.uuid4()
    cache_adaptive.configure_thresholds(threshold=3, duration_seconds=60)

    for _ in range(3):
        await cache_adaptive.record_cache_result(
            redis,
            workspace_id=ws_a,
            provider_kind="anthropic",
            hit=False,
        )
    a_disabled = await cache_adaptive.is_cache_disabled(
        redis, workspace_id=ws_a, provider_kind="anthropic"
    )
    b_disabled = await cache_adaptive.is_cache_disabled(
        redis, workspace_id=ws_b, provider_kind="anthropic"
    )
    assert a_disabled is True
    assert b_disabled is False
