"""M2.5.3 invariant: provider failover must NEVER reformat message_history.

Roadmap design principle 5: provider failover preserves the exact
message payload across attempts so the upstream prompt cache prefix
stays stable. If the wrapper reformatted the history between attempts
the second provider would see a different prefix → cache miss → cost
spike + latency regression that defeats the point of failover.

This test runs the chain wrapper against a fake ``inner_stream`` that
records the request payload it received, then byte-equality checks the
two snapshots.
"""

from __future__ import annotations

import copy
import uuid
from typing import Any

import pytest

from app.agents.kernels.base import RunEvent, RunEventKind, RunRequest
from app.agents.kernels.model_client import ResolvedModel
from app.agents.kernels.native._failover import (
    ProviderFailoverHint,
    run_with_failover,
)
from app.services import provider_health as health_svc
from app.services.provider_chain import (
    ProviderChainEntry,
    ProviderFailoverConfig,
)

pytestmark = pytest.mark.asyncio


# Sample history with all the shapes the runner sees in production:
# user / assistant turn alternation + a non-trivial JSON content blob.
_BASE_HISTORY: list[dict[str, Any]] = [
    {"role": "user", "content_json": {"text": "summarise the docs"}},
    {
        "role": "assistant",
        "content_json": {
            "text": "Here's a summary of the SenHarness docs.",
            "tool_calls": [],
        },
    },
    {"role": "user", "content_json": {"text": "expand on the failover section"}},
]


@pytest.fixture(autouse=True)
def _reset_health():
    health_svc.reset_cache()
    yield
    health_svc.reset_cache()


def _entry(provider: str, model: str) -> ProviderChainEntry:
    return ProviderChainEntry(
        provider_kind=provider,
        model_id=model,
        upstream_label=f"{provider}:{model}",
    )


async def test_message_history_byte_equal_across_attempts(
    workspace, agent, identity, monkeypatch
):
    """First provider fails → second succeeds. Capture the history each
    attempt observed and assert byte-equality.
    """
    chain = [_entry("openai", "gpt-5"), _entry("deepseek", "v3")]

    primary = ResolvedModel(
        provider_kind="openai",
        model_name="gpt-5",
        api_key="x",
        source="db",
    )

    async def _stub_resolve(*, req, entry, attempt_index, primary_resolved, primary_model):
        if attempt_index == 0:
            return primary_resolved, ("model-openai",)
        return (
            ResolvedModel(
                provider_kind=entry.provider_kind,
                model_name=entry.model_id,
                api_key="x",
                source="db",
            ),
            ("model-deepseek",),
        )

    monkeypatch.setattr(
        "app.agents.kernels.native._failover._resolve_for_attempt",
        _stub_resolve,
    )

    captured: list[dict[str, Any]] = []

    async def fake_inner_stream(
        req, *, model, resolved, served_name, raise_provider_errors
    ):
        # Deep-copy the live request payload so subsequent in-place
        # mutations cannot retroactively change a prior snapshot.
        captured.append(
            {
                "user_text": req.user_text,
                "message_history": copy.deepcopy(req.message_history),
                "policy": copy.deepcopy(req.policy),
                "attachments": copy.deepcopy(req.attachments),
                "toolbox": list(req.toolbox),
                "skills": list(req.skills),
            }
        )
        if resolved.provider_kind == "openai":
            raise ProviderFailoverHint(
                original=TimeoutError("read timed out"),
                failure_kind=health_svc.FailureKind.TIMEOUT,
            )
        yield RunEvent(RunEventKind.DELTA, {"text": "ok"})
        yield RunEvent(
            RunEventKind.FINAL,
            {"message_id": str(uuid.uuid4()), "summary": None},
        )

    req = RunRequest(
        run_id=uuid.uuid4(),
        workspace_id=workspace.id,
        agent_id=agent.id,
        session_id=uuid.uuid4(),
        identity_id=identity.id,
        user_text="please continue",
        message_history=copy.deepcopy(_BASE_HISTORY),
        attachments=[],
        toolbox=["search_files"],
        skills=[],
        policy={"served_model_name": "ws-fast"},
    )

    config = ProviderFailoverConfig(
        enabled=True,
        chain_raw=[],
        failover_max_attempts=5,
        cooldown_threshold=3,
        cooldown_seconds=120,
    )

    async for _ in run_with_failover(
        req,
        primary_resolved=primary,
        primary_model=("model-openai",),
        served_name="ws-fast",
        chain=chain,
        config=config,
        redis=None,
        inner_stream=fake_inner_stream,
    ):
        pass

    assert len(captured) == 2, "expected exactly two provider attempts"
    a, b = captured
    assert a == b, (
        "message_history (and adjacent payload) MUST be byte-equal across "
        "failover attempts — otherwise the upstream prompt cache prefix "
        "shifts and the failover loses its point"
    )
    # Belt + braces: the user's last turn must still be present and
    # unchanged in both snapshots.
    assert a["message_history"] == _BASE_HISTORY
    assert a["user_text"] == "please continue"


async def test_message_history_shape_preserved_through_chain_exhaustion(
    workspace, agent, identity, monkeypatch
):
    """Every entry fails — even on the 'failed' code path the history
    seen by each attempt is still byte-identical.
    """
    chain = [
        _entry("openai", "gpt-5"),
        _entry("deepseek", "v3"),
        _entry("anthropic", "sonnet"),
    ]

    async def _stub_resolve(*, req, entry, attempt_index, primary_resolved, primary_model):
        return (
            ResolvedModel(
                provider_kind=entry.provider_kind,
                model_name=entry.model_id,
                api_key="x",
                source="db",
            ),
            (entry.provider_kind, entry.model_id),
        )

    monkeypatch.setattr(
        "app.agents.kernels.native._failover._resolve_for_attempt",
        _stub_resolve,
    )

    captured: list[Any] = []

    async def fake_inner_stream(
        req, *, model, resolved, served_name, raise_provider_errors
    ):
        captured.append(copy.deepcopy(req.message_history))
        raise ProviderFailoverHint(
            original=Exception("503 service unavailable"),
            failure_kind=health_svc.FailureKind.SERVER_5XX,
        )
        yield  # pragma: no cover

    req = RunRequest(
        run_id=uuid.uuid4(),
        workspace_id=workspace.id,
        agent_id=agent.id,
        session_id=uuid.uuid4(),
        identity_id=identity.id,
        user_text="continue",
        message_history=copy.deepcopy(_BASE_HISTORY),
        toolbox=[],
        skills=[],
        policy={},
    )

    config = ProviderFailoverConfig(
        enabled=True,
        chain_raw=[],
        failover_max_attempts=5,
        cooldown_threshold=3,
        cooldown_seconds=120,
    )

    from app.agents.kernels.native._failover import AllProvidersUnavailable

    with pytest.raises(AllProvidersUnavailable):
        async for _ in run_with_failover(
            req,
            primary_resolved=ResolvedModel(
                provider_kind="openai",
                model_name="gpt-5",
                api_key="x",
                source="db",
            ),
            primary_model=("openai", "gpt-5"),
            served_name="ws-fast",
            chain=chain,
            config=config,
            redis=None,
            inner_stream=fake_inner_stream,
        ):
            pass

    assert len(captured) == 3
    assert captured[0] == captured[1] == captured[2] == _BASE_HISTORY
