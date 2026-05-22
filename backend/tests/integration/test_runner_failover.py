"""Runner-level integration tests for M2.5.3 provider failover.

We can't easily spin up a real LLM in CI, so these tests exercise the
chain wrapper :func:`app.agents.kernels.native._failover.run_with_failover`
directly with a fake ``inner_stream`` callable — that's the same seam
the runner uses, so a working chain wrapper here means a working
end-to-end runner path.

What's covered:

* First provider raises a retryable timeout → wrapper records failure
  + writes ``provider.failover_attempted`` audit row + tries next
  provider, which succeeds.
* Cooldown threshold reached → wrapper additionally writes
  ``provider.cooldown_started`` audit row.
* Every provider in the chain fails → wrapper raises
  :class:`AllProvidersUnavailable` and writes
  ``provider.failover_exhausted``.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from sqlalchemy import select

from app.agents.kernels.base import RunEvent, RunEventKind, RunRequest
from app.agents.kernels.model_client import ResolvedModel
from app.agents.kernels.native._failover import (
    AllProvidersUnavailable,
    ProviderFailoverHint,
    run_with_failover,
)
from app.db.models.audit import AuditEvent
from app.services import provider_health as health_svc
from app.services.provider_chain import (
    ProviderChainEntry,
    ProviderFailoverConfig,
)

pytestmark = pytest.mark.asyncio


# ─── Helpers ───────────────────────────────────────────────
def _make_req(workspace_id, agent_id, identity_id) -> RunRequest:
    return RunRequest(
        run_id=uuid.uuid4(),
        workspace_id=workspace_id,
        agent_id=agent_id,
        session_id=uuid.uuid4(),
        identity_id=identity_id,
        user_text="hello",
        message_history=[],
        toolbox=[],
        skills=[],
        policy={},
    )


def _entry(provider: str, model: str) -> ProviderChainEntry:
    return ProviderChainEntry(
        provider_kind=provider,
        model_id=model,
        upstream_label=f"{provider}:{model}",
    )


def _config(*, threshold: int = 3, cooldown_seconds: int = 300) -> ProviderFailoverConfig:
    return ProviderFailoverConfig(
        enabled=True,
        chain_raw=[],
        failover_max_attempts=10,
        cooldown_threshold=threshold,
        cooldown_seconds=cooldown_seconds,
    )


def _make_model_for_chain(entry: ProviderChainEntry) -> Any:
    """Sentinel model — the chain wrapper passes it straight to inner_stream
    which only checks identity. We use a tuple so it has stable equality.
    """
    return (entry.provider_kind, entry.model_id, "model")


@pytest.fixture(autouse=True)
def _reset_health():
    health_svc.reset_cache()
    yield
    health_svc.reset_cache()


# ─── Tests ─────────────────────────────────────────────────
async def test_first_provider_timeout_failover_to_second(
    db_session, workspace, agent, identity, monkeypatch
):
    """First provider raises a timeout-shaped exception → wrapper falls
    back to the second provider, which streams a successful turn."""
    chain = [_entry("openai", "gpt-5"), _entry("deepseek", "v3")]
    config = _config(threshold=3, cooldown_seconds=120)

    primary = ResolvedModel(
        provider_kind="openai",
        model_name="gpt-5",
        api_key="x",
        source="db",
    )
    primary_model = _make_model_for_chain(chain[0])

    captured_history: list[Any] = []
    captured_user: list[str] = []

    # Stub the per-attempt model resolution so we don't need real
    # provider rows in the DB.
    async def _stub_resolve(*, req, entry, attempt_index, primary_resolved, primary_model):
        if attempt_index == 0:
            return primary_resolved, primary_model
        return (
            ResolvedModel(
                provider_kind=entry.provider_kind,
                model_name=entry.model_id,
                api_key="x",
                source="db",
            ),
            _make_model_for_chain(entry),
        )

    monkeypatch.setattr(
        "app.agents.kernels.native._failover._resolve_for_attempt",
        _stub_resolve,
    )

    async def fake_inner_stream(
        req,
        *,
        model,
        resolved,
        served_name,
        raise_provider_errors,
    ):
        captured_history.append(list(req.message_history))
        captured_user.append(req.user_text)
        if resolved.provider_kind == "openai":
            raise ProviderFailoverHint(
                original=TimeoutError("read timed out"),
                failure_kind=health_svc.FailureKind.TIMEOUT,
            )
        # second provider: stream a delta + final
        yield RunEvent(RunEventKind.DELTA, {"text": "hello from deepseek"})
        yield RunEvent(
            RunEventKind.FINAL,
            {
                "message_id": str(uuid.uuid4()),
                "summary": None,
                "served_model": served_name,
            },
        )

    req = _make_req(workspace.id, agent.id, identity.id)

    events: list[RunEvent] = []
    async for ev in run_with_failover(
        req,
        primary_resolved=primary,
        primary_model=primary_model,
        served_name="ws-fast",
        chain=chain,
        config=config,
        redis=None,  # in-memory health tracker, fail-open redis
        inner_stream=fake_inner_stream,
    ):
        events.append(ev)

    # The deepseek attempt's frames must reach the caller.
    kinds = [e.kind for e in events]
    assert RunEventKind.DELTA in kinds
    assert RunEventKind.FINAL in kinds

    # Both attempts saw the same message_history + user_text — the
    # chain wrapper must NEVER reformat between attempts.
    assert captured_user == ["hello", "hello"]
    assert captured_history == [[], []]

    # Provider health: openai bumped, deepseek cleared.
    openai = await health_svc.get_health(None, provider_kind="openai", model_id="gpt-5")
    deepseek = await health_svc.get_health(None, provider_kind="deepseek", model_id="v3")
    assert openai.consecutive_failures == 1
    assert openai.last_failure_kind == health_svc.FailureKind.TIMEOUT
    assert deepseek.consecutive_failures == 0
    assert deepseek.last_success_at is not None

    # Audit rows must be visible on a fresh session because the
    # wrapper opens its own DB session.
    from app.db.session import get_session_factory

    factory = get_session_factory()
    async with factory() as fresh:
        actions = (
            (
                await fresh.execute(
                    select(AuditEvent.action).where(
                        AuditEvent.workspace_id == workspace.id,
                        AuditEvent.action.in_(
                            [
                                "provider.failover_attempted",
                                "provider.failover_succeeded",
                                "provider.cooldown_started",
                            ]
                        ),
                    )
                )
            )
            .scalars()
            .all()
        )
        assert "provider.failover_attempted" in actions
        assert "provider.failover_succeeded" in actions
        # threshold=3 not yet hit, cooldown_started must NOT fire.
        assert "provider.cooldown_started" not in actions


async def test_cooldown_audit_when_threshold_reached(
    db_session, workspace, agent, identity, monkeypatch
):
    """When the consecutive failure counter hits the cooldown threshold
    on the same attempt, the wrapper writes an extra
    ``provider.cooldown_started`` audit row.
    """
    chain = [_entry("openai", "gpt-5"), _entry("deepseek", "v3")]
    config = _config(threshold=1, cooldown_seconds=60)

    primary = ResolvedModel(
        provider_kind="openai",
        model_name="gpt-5",
        api_key="x",
        source="db",
    )
    primary_model = _make_model_for_chain(chain[0])

    async def _stub_resolve(*, req, entry, attempt_index, primary_resolved, primary_model):
        if attempt_index == 0:
            return primary_resolved, primary_model
        return (
            ResolvedModel(
                provider_kind=entry.provider_kind,
                model_name=entry.model_id,
                api_key="x",
                source="db",
            ),
            _make_model_for_chain(entry),
        )

    monkeypatch.setattr(
        "app.agents.kernels.native._failover._resolve_for_attempt",
        _stub_resolve,
    )

    async def fake_inner_stream(req, *, model, resolved, served_name, raise_provider_errors):
        if resolved.provider_kind == "openai":
            raise ProviderFailoverHint(
                original=ConnectionError("ECONNRESET"),
                failure_kind=health_svc.FailureKind.CONNECTION,
            )
        yield RunEvent(RunEventKind.DELTA, {"text": "ok"})
        yield RunEvent(
            RunEventKind.FINAL,
            {"message_id": str(uuid.uuid4()), "summary": None},
        )

    req = _make_req(workspace.id, agent.id, identity.id)

    async for _ in run_with_failover(
        req,
        primary_resolved=primary,
        primary_model=primary_model,
        served_name="ws-fast",
        chain=chain,
        config=config,
        redis=None,
        inner_stream=fake_inner_stream,
    ):
        pass

    from app.db.session import get_session_factory

    factory = get_session_factory()
    async with factory() as fresh:
        rows = (
            (
                await fresh.execute(
                    select(AuditEvent).where(
                        AuditEvent.workspace_id == workspace.id,
                        AuditEvent.action == "provider.cooldown_started",
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(rows) >= 1
        meta = rows[-1].metadata_json or {}
        assert meta.get("provider_kind") == "openai"
        assert meta.get("cooldown_seconds") == 60


async def test_chain_exhausted_raises_typed_error(
    db_session, workspace, agent, identity, monkeypatch
):
    """Every chain entry raises a retryable failure → wrapper raises
    :class:`AllProvidersUnavailable` and writes the
    ``provider.failover_exhausted`` audit row.
    """
    chain = [_entry("openai", "gpt-5"), _entry("deepseek", "v3")]
    config = _config(threshold=3, cooldown_seconds=120)

    primary = ResolvedModel(
        provider_kind="openai",
        model_name="gpt-5",
        api_key="x",
        source="db",
    )
    primary_model = _make_model_for_chain(chain[0])

    async def _stub_resolve(*, req, entry, attempt_index, primary_resolved, primary_model):
        if attempt_index == 0:
            return primary_resolved, primary_model
        return (
            ResolvedModel(
                provider_kind=entry.provider_kind,
                model_name=entry.model_id,
                api_key="x",
                source="db",
            ),
            _make_model_for_chain(entry),
        )

    monkeypatch.setattr(
        "app.agents.kernels.native._failover._resolve_for_attempt",
        _stub_resolve,
    )

    async def fake_inner_stream(req, *, model, resolved, served_name, raise_provider_errors):
        raise ProviderFailoverHint(
            original=Exception("503 service unavailable"),
            failure_kind=health_svc.FailureKind.SERVER_5XX,
        )
        # Unreachable but keeps the function recognised as an async generator
        # by the type checker.
        yield  # pragma: no cover

    req = _make_req(workspace.id, agent.id, identity.id)

    with pytest.raises(AllProvidersUnavailable) as excinfo:
        async for _ in run_with_failover(
            req,
            primary_resolved=primary,
            primary_model=primary_model,
            served_name="ws-fast",
            chain=chain,
            config=config,
            redis=None,
            inner_stream=fake_inner_stream,
        ):
            pass
    assert len(excinfo.value.attempts) == 2

    from app.db.session import get_session_factory

    factory = get_session_factory()
    async with factory() as fresh:
        actions = (
            (
                await fresh.execute(
                    select(AuditEvent.action).where(
                        AuditEvent.workspace_id == workspace.id,
                        AuditEvent.action == "provider.failover_exhausted",
                    )
                )
            )
            .scalars()
            .all()
        )
        assert "provider.failover_exhausted" in actions
