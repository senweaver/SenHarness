"""Unit tests for the M2.2 evolver subagent factory.

These tests don't drive a real LLM. They exercise the pre-flight
contract:

* persona file under 800 chars (roadmap budget);
* :func:`build_evolver_agent` registers exactly the 9 tools the
  brief specifies;
* :func:`invoke_evolver_subagent` short-circuits with
  :class:`EvolverDisabledError` when the workspace is opted out;
* :func:`_resolve_aux_config` walks the documented fallthrough
  (``aux_model_evolver`` → ``SKILL_REVIEW`` → ``JUDGE``).
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager

import pytest

from app.agents.builtin import evolver_agent as ev
from app.agents.builtin.evolver_agent import (
    EVOLVER_AGENT_TIMEOUT_SECONDS,
    EVOLVER_TOOL_NAMES,
    EvolverDisabledError,
    build_evolver_agent,
    invoke_evolver_subagent,
    load_evolver_persona,
)
from app.agents.tools import BUILTIN_TOOL_REGISTRY
from app.schemas.platform_settings import EvolverSettings

pytestmark = pytest.mark.asyncio


def test_persona_under_800_chars():
    persona = load_evolver_persona()
    assert persona, "persona must load"
    assert len(persona) < 800, f"persona is {len(persona)} chars, must stay < 800"
    assert "skill curator" in persona.lower()
    assert "mark_skip" in persona


def test_tool_registry_includes_all_nine_evolver_tools():
    assert EVOLVER_AGENT_TIMEOUT_SECONDS == 300
    assert len(EVOLVER_TOOL_NAMES) == 9
    for name in EVOLVER_TOOL_NAMES:
        tool = BUILTIN_TOOL_REGISTRY[name]
        assert tool.available_for_kinds == ("evolver",), f"{name} must be evolver-gated"


def test_build_evolver_agent_attaches_nine_tools():
    """The factory must register all 9 tools onto the pydantic-ai
    Agent. We assert via a stub model so we don't need a real LLM
    provider — the real provider integration is exercised end-to-end
    by the integration test suite.
    """
    from pydantic_ai.models.test import TestModel

    agent = build_evolver_agent(model=TestModel())
    toolset = agent._function_toolset
    registered = list(toolset.tools)
    assert len(registered) == 9, f"expected 9 registered tools, got {len(registered)}: {registered}"
    for name in EVOLVER_TOOL_NAMES:
        assert name in registered, f"{name} should be registered"


async def test_invoke_disabled_workspace_raises(db_session, workspace, monkeypatch):
    """A workspace with ``evolver.enabled=False`` must short-circuit
    before the model is invoked and surface :class:`EvolverDisabledError`.
    """
    workspace.home_config_json = {"evolver": {"enabled": False}}
    await db_session.flush()

    @asynccontextmanager
    async def _factory():
        yield db_session

    monkeypatch.setattr(ev, "get_session_factory", lambda: _factory)

    with pytest.raises(EvolverDisabledError):
        await invoke_evolver_subagent(workspace_id=workspace.id)


async def test_resolve_aux_config_prefers_explicit_pin(monkeypatch):
    """``EvolverSettings.aux_model_evolver`` wins over the auxiliary
    client fallthrough so workspace operators can pin a dedicated
    cheap model for the curator without affecting the judge tier.
    """
    workspace_id = uuid.uuid4()
    cfg = EvolverSettings(enabled=True, aux_model_evolver="openai:gpt-4o-mini")

    captured: list[str] = []

    @asynccontextmanager
    async def _factory():
        captured.append("session_opened")

        class _Stub:
            async def commit(self):
                pass

        yield _Stub()

    async def _get_aux_model(*args, **kwargs):  # pragma: no cover - must not fire
        raise AssertionError("explicit pin should bypass get_aux_model")

    async def _resolve_for_workspace(**_kwargs):
        from app.agents.kernels.model_client import ResolvedModel

        return ResolvedModel(
            provider_kind="openai",
            model_name="filler",
            api_key="sk-test",
            base_url=None,
        )

    monkeypatch.setattr(ev, "get_session_factory", lambda: _factory)
    monkeypatch.setattr(ev, "get_aux_model", _get_aux_model)
    monkeypatch.setattr(ev, "resolve_for_workspace", _resolve_for_workspace)

    aux = await ev._resolve_aux_config(workspace_id=workspace_id, evolver_config=cfg)
    assert aux is not None
    assert aux.model == "openai:gpt-4o-mini"
    assert aux.api_key_ref == "sk-test"
    assert aux.extra.get("_source") == "evolver_pin"


async def test_resolve_aux_config_fallthrough_skill_review_then_judge(monkeypatch):
    """When no ``aux_model_evolver`` pin is set, the resolver asks the
    auxiliary client for ``SKILL_REVIEW`` first, falling through to
    ``JUDGE`` only when ``SKILL_REVIEW`` resolves to ``None``.
    """
    from app.agents.auxiliary_client import AuxiliaryConfig, AuxiliaryTask

    workspace_id = uuid.uuid4()
    cfg = EvolverSettings(enabled=True, aux_model_evolver=None)

    @asynccontextmanager
    async def _factory():
        class _Stub:
            async def commit(self):
                pass

        yield _Stub()

    calls: list[AuxiliaryTask] = []

    async def _get_aux_model(_db, *, workspace_id, task):
        calls.append(task)
        if task is AuxiliaryTask.SKILL_REVIEW:
            return None
        if task is AuxiliaryTask.JUDGE:
            return AuxiliaryConfig(
                task=AuxiliaryTask.JUDGE,
                model="openai:gpt-3.5-turbo",
            )
        return None

    monkeypatch.setattr(ev, "get_session_factory", lambda: _factory)
    monkeypatch.setattr(ev, "get_aux_model", _get_aux_model)

    aux = await ev._resolve_aux_config(workspace_id=workspace_id, evolver_config=cfg)
    assert aux is not None
    assert aux.model == "openai:gpt-3.5-turbo"
    assert calls == [AuxiliaryTask.SKILL_REVIEW, AuxiliaryTask.JUDGE]
