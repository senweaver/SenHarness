"""Model warm-up contract (`app.agents.kernels.warmup`).

We don't exercise the DB / vault here — those are covered by their own
unit suites. Instead we pin down the orchestration: chat-model picking,
graceful collection failures, and parallel timeout enforcement.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest

from app.agents.kernels import warmup as warmup_mod


def test_first_chat_model_skips_embedding_only_providers() -> None:
    from app.agents.kernels.model_catalog import CATALOG, CatalogModel

    fake_kind = "test-embedonly-kind"
    CATALOG[fake_kind] = [
        CatalogModel(
            provider=fake_kind,
            model="e1",
            name="embed-1",
            family="embedding",
            recommended=True,
            description="",
            category="embedding",
        ),
    ]
    try:
        assert warmup_mod._first_chat_model(fake_kind) is None
    finally:
        del CATALOG[fake_kind]


def test_first_chat_model_prefers_recommended() -> None:
    from app.agents.kernels.model_catalog import CATALOG, CatalogModel

    fake_kind = "test-pref-kind"
    CATALOG[fake_kind] = [
        CatalogModel(
            provider=fake_kind,
            model="alt",
            name="alt",
            family="chat",
            recommended=False,
            description="",
            category="chat",
        ),
        CatalogModel(
            provider=fake_kind,
            model="pref",
            name="pref",
            family="chat",
            recommended=True,
            description="",
            category="chat",
        ),
    ]
    try:
        assert warmup_mod._first_chat_model(fake_kind) == "pref"
    finally:
        del CATALOG[fake_kind]


@pytest.mark.asyncio
async def test_warm_model_clients_handles_empty_target_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _no_targets() -> list[warmup_mod._WarmTarget]:
        return []

    monkeypatch.setattr(warmup_mod, "_collect_targets", _no_targets)
    # Just shouldn't raise.
    await warmup_mod.warm_model_clients()


@pytest.mark.asyncio
async def test_warm_model_clients_swallows_collector_exceptions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _boom() -> list[warmup_mod._WarmTarget]:
        raise RuntimeError("db down")

    monkeypatch.setattr(warmup_mod, "_collect_targets", _boom)
    await warmup_mod.warm_model_clients()


@pytest.mark.asyncio
async def test_warm_model_clients_respects_total_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A single hanging worker can't bleed past the total budget."""
    target = warmup_mod._WarmTarget(
        workspace_id=uuid.uuid4(),
        provider_kind="openai",
        model_name="gpt-4o-mini",
        base_url=None,
        api_key="dummy",
    )

    async def _one_target() -> list[warmup_mod._WarmTarget]:
        return [target] * 4

    def _hang(_t: warmup_mod._WarmTarget) -> bool:
        import time as _time

        _time.sleep(1.0)
        return True

    monkeypatch.setattr(warmup_mod, "_collect_targets", _one_target)
    monkeypatch.setattr(warmup_mod, "_warm_one", _hang)
    monkeypatch.setattr(warmup_mod, "_WARMUP_TOTAL_BUDGET_S", 0.1)
    monkeypatch.setattr(warmup_mod, "_WARMUP_PER_TASK_TIMEOUT_S", 0.5)

    loop = asyncio.get_event_loop()
    started = loop.time()
    await warmup_mod.warm_model_clients()
    elapsed = loop.time() - started
    # asyncio.to_thread can't cancel sync work; the in-test return
    # happens at the budget, even though the threads finish their
    # ``time.sleep`` after the function has already returned.
    assert elapsed < 0.5, f"warmup should honour budget, took {elapsed:.2f}s"
