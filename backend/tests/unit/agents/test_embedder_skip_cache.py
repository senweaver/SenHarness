"""Negative-cache contract for `app.agents.harness.embedder`.

These tests verify the local + Redis layering without standing up a
real Redis: a fake async client mimics ``exists`` / ``set`` semantics
just well enough that the embedder code-paths exercise both layers.
"""

from __future__ import annotations

import time
import uuid

import pytest

from app.agents.harness import embedder


class _FakeRedis:
    """Minimal aioredis stand-in: TTL is honoured via wall-clock."""

    def __init__(self) -> None:
        self.store: dict[str, tuple[str, float]] = {}
        self.exists_calls = 0
        self.set_calls = 0

    async def exists(self, key: str) -> int:
        self.exists_calls += 1
        item = self.store.get(key)
        if item is None:
            return 0
        _value, expire_at = item
        if expire_at < time.time():
            self.store.pop(key, None)
            return 0
        return 1

    async def set(self, key: str, value: str, *, ex: int) -> bool:
        self.set_calls += 1
        self.store[key] = (value, time.time() + ex)
        return True


@pytest.fixture(autouse=True)
def _clear_local_cache() -> None:
    embedder._BACKEND_SKIP_CACHE.clear()
    yield
    embedder._BACKEND_SKIP_CACHE.clear()


@pytest.fixture
def fake_redis(monkeypatch: pytest.MonkeyPatch) -> _FakeRedis:
    fake = _FakeRedis()
    monkeypatch.setattr(embedder, "_safe_redis_client", lambda: fake)
    return fake


@pytest.mark.asyncio
async def test_mark_skip_writes_both_local_and_redis(fake_redis: _FakeRedis) -> None:
    pid = uuid.uuid4()
    await embedder._mark_skip(pid)

    assert pid in embedder._BACKEND_SKIP_CACHE
    assert fake_redis.set_calls == 1
    assert embedder._skip_key(pid) in fake_redis.store


@pytest.mark.asyncio
async def test_is_skipped_hits_local_cache_first(fake_redis: _FakeRedis) -> None:
    pid = uuid.uuid4()
    await embedder._mark_skip(pid)
    fake_redis.exists_calls = 0

    assert await embedder._is_skipped(pid) is True
    assert fake_redis.exists_calls == 0, "local cache should short-circuit Redis"


@pytest.mark.asyncio
async def test_is_skipped_picks_up_other_workers_writes(fake_redis: _FakeRedis) -> None:
    pid = uuid.uuid4()
    fake_redis.store[embedder._skip_key(pid)] = (
        "1",
        time.time() + embedder._BACKEND_SKIP_TTL_SEC,
    )

    assert await embedder._is_skipped(pid) is True
    assert fake_redis.exists_calls == 1
    assert pid in embedder._BACKEND_SKIP_CACHE, "redis hit should hydrate local cache"


@pytest.mark.asyncio
async def test_is_skipped_returns_false_when_neither_layer_set(
    fake_redis: _FakeRedis,
) -> None:
    pid = uuid.uuid4()
    assert await embedder._is_skipped(pid) is False
    assert fake_redis.exists_calls == 1


@pytest.mark.asyncio
async def test_redis_unavailable_falls_back_to_local(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(embedder, "_safe_redis_client", lambda: None)
    pid = uuid.uuid4()

    await embedder._mark_skip(pid)
    assert await embedder._is_skipped(pid) is True

    # local TTL expiry still works without Redis.
    embedder._BACKEND_SKIP_CACHE[pid] = time.time() - 1.0
    assert await embedder._is_skipped(pid) is False


@pytest.mark.asyncio
async def test_redis_hiccup_does_not_raise(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Boom:
        async def exists(self, _key: str) -> int:
            raise RuntimeError("connection refused")

        async def set(self, _key: str, _value: str, *, ex: int) -> bool:
            raise RuntimeError("connection refused")

    monkeypatch.setattr(embedder, "_safe_redis_client", lambda: _Boom())
    pid = uuid.uuid4()

    assert await embedder._is_skipped(pid) is False
    await embedder._mark_skip(pid)
    # local cache still flipped on so the next call short-circuits even
    # though Redis was down for both reads and writes.
    assert await embedder._is_skipped(pid) is True
