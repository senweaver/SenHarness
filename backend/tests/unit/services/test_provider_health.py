"""Pure unit tests for :mod:`app.services.provider_health` (M2.5.3).

These tests run with a fake in-memory Redis stand-in so they stay
fast (no testcontainers) and can verify both the in-process LRU
cache + Redis dual-write path without spinning up an external
service.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from app.services import provider_health as health_svc


# ─── Fake redis ─────────────────────────────────────────────
class FakeRedis:
    """Minimal Redis stand-in for the methods provider_health calls.

    Only implements ``hgetall`` / ``hset`` / ``expire`` / ``delete``
    because that's the entire surface the service uses. All operations
    are synchronous in the fake but exposed via ``async def`` so the
    awaiting service code calls them naturally.
    """

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
def _reset_local_cache():
    health_svc.reset_cache()
    yield
    health_svc.reset_cache()


# ─── Failure / success bookkeeping ─────────────────────────
async def test_record_failure_bumps_counter():
    redis = FakeRedis()
    snap = await health_svc.record_failure(
        redis,
        provider_kind="openai",
        model_id="gpt-5",
        failure_kind=health_svc.FailureKind.TIMEOUT,
        cooldown_threshold=3,
        cooldown_seconds=300,
    )
    assert snap.consecutive_failures == 1
    assert snap.last_failure_kind == health_svc.FailureKind.TIMEOUT
    assert snap.cooldown_until is None

    snap = await health_svc.record_failure(
        redis,
        provider_kind="openai",
        model_id="gpt-5",
        failure_kind=health_svc.FailureKind.TIMEOUT,
        cooldown_threshold=3,
        cooldown_seconds=300,
    )
    assert snap.consecutive_failures == 2
    assert snap.cooldown_until is None


async def test_record_failure_threshold_trips_cooldown():
    redis = FakeRedis()
    for _ in range(2):
        await health_svc.record_failure(
            redis,
            provider_kind="openai",
            model_id="gpt-5",
            failure_kind=health_svc.FailureKind.RATE_LIMIT,
            cooldown_threshold=3,
            cooldown_seconds=300,
        )
    snap = await health_svc.record_failure(
        redis,
        provider_kind="openai",
        model_id="gpt-5",
        failure_kind=health_svc.FailureKind.RATE_LIMIT,
        cooldown_threshold=3,
        cooldown_seconds=300,
    )
    assert snap.consecutive_failures == 3
    assert snap.cooldown_until is not None
    assert snap.extras.get("cooldown_just_started") is True
    # Cooldown must land in the future, within the configured window.
    assert snap.cooldown_until > datetime.now(UTC)
    assert snap.cooldown_until <= datetime.now(UTC) + timedelta(seconds=301)


async def test_record_success_clears_state():
    redis = FakeRedis()
    for _ in range(3):
        await health_svc.record_failure(
            redis,
            provider_kind="openai",
            model_id="gpt-5",
            failure_kind=health_svc.FailureKind.TIMEOUT,
            cooldown_threshold=3,
            cooldown_seconds=300,
        )
    cooldown = await health_svc.is_in_cooldown(
        redis, provider_kind="openai", model_id="gpt-5"
    )
    assert cooldown is True

    snap = await health_svc.record_success(
        redis, provider_kind="openai", model_id="gpt-5"
    )
    assert snap.consecutive_failures == 0
    assert snap.cooldown_until is None
    assert snap.last_success_at is not None

    cooldown = await health_svc.is_in_cooldown(
        redis, provider_kind="openai", model_id="gpt-5"
    )
    assert cooldown is False


async def test_is_in_cooldown_expires_with_time():
    redis = FakeRedis()
    snap = await health_svc.record_failure(
        redis,
        provider_kind="deepseek",
        model_id="v3",
        failure_kind=health_svc.FailureKind.SERVER_5XX,
        cooldown_threshold=1,
        cooldown_seconds=60,
    )
    assert snap.cooldown_until is not None

    # Manually expire the cooldown by rewriting Redis with a past time.
    health_svc.reset_cache()
    redis.store["provider_health:deepseek:v3"]["cooldown_until"] = (
        (datetime.now(UTC) - timedelta(seconds=10)).isoformat()
    )
    cooldown = await health_svc.is_in_cooldown(
        redis, provider_kind="deepseek", model_id="v3"
    )
    assert cooldown is False


async def test_get_health_uses_redis_after_cache_reset():
    redis = FakeRedis()
    await health_svc.record_failure(
        redis,
        provider_kind="anthropic",
        model_id="sonnet",
        failure_kind=health_svc.FailureKind.CONNECTION,
        cooldown_threshold=5,
        cooldown_seconds=60,
    )
    health_svc.reset_cache()

    snap = await health_svc.get_health(
        redis, provider_kind="anthropic", model_id="sonnet"
    )
    assert snap.consecutive_failures == 1
    assert snap.last_failure_kind == health_svc.FailureKind.CONNECTION


async def test_redis_unreachable_fails_open():
    """A degraded Redis must never trip a cooldown by accident."""

    cooldown = await health_svc.is_in_cooldown(
        None, provider_kind="openai", model_id="gpt-5"
    )
    assert cooldown is False

    snap = await health_svc.get_health(
        None, provider_kind="openai", model_id="gpt-5"
    )
    assert snap.consecutive_failures == 0


# ─── Exception classifier ───────────────────────────────────
def test_classify_timeout():
    class TimeoutException(Exception):
        pass

    assert health_svc.classify_exception(TimeoutException("read timed out")) == (
        health_svc.FailureKind.TIMEOUT
    )


def test_classify_rate_limit():
    class RateLimitError(Exception):
        pass

    assert health_svc.classify_exception(RateLimitError("429 too many")) == (
        health_svc.FailureKind.RATE_LIMIT
    )


def test_classify_5xx_via_status_code():
    class _Resp:
        status_code = 503

    class HTTPStatusError(Exception):
        def __init__(self) -> None:
            super().__init__("503 service unavailable")
            self.response = _Resp()

    assert health_svc.classify_exception(HTTPStatusError()) == (
        health_svc.FailureKind.SERVER_5XX
    )


def test_classify_auth_via_text():
    err = Exception("401 unauthorized: invalid api key")
    assert health_svc.classify_exception(err) == health_svc.FailureKind.AUTH


def test_classify_unknown_falls_back_to_other():
    err = ValueError("something weird happened")
    assert health_svc.classify_exception(err) == health_svc.FailureKind.OTHER


def test_is_retryable_failure_partition():
    retryable = {
        health_svc.FailureKind.RATE_LIMIT,
        health_svc.FailureKind.TIMEOUT,
        health_svc.FailureKind.CONNECTION,
        health_svc.FailureKind.SERVER_5XX,
    }
    not_retryable = {health_svc.FailureKind.AUTH, health_svc.FailureKind.OTHER}
    for kind in retryable:
        assert health_svc.is_retryable_failure(kind) is True
    for kind in not_retryable:
        assert health_svc.is_retryable_failure(kind) is False
