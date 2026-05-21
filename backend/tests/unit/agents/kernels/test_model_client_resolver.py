"""Unit tests for the M2.5.8 agent-default branch in ``resolve_for_agent``.

The resolver opens its own session via ``get_session_factory`` so a
realistic test only needs to control what that session sees. We
monkeypatch the two seams independently:

* ``_read_agent_default_model`` — returns the agent's stored default
  (or ``None``) without touching the DB.
* ``_resolve_from_db`` — returns a synthesised ``ResolvedModel`` for a
  given ``prefer_kind``, simulating a workspace with a known set of
  enabled providers.

These tests are intentionally narrow: they verify routing, not the
underlying SQL.
"""

from __future__ import annotations

import uuid

import pytest

from app.agents.kernels import model_client


def _resolved(kind: str, model: str = "default") -> model_client.ResolvedModel:
    return model_client.ResolvedModel(
        provider_kind=kind,
        model_name=model,
        api_key="sk-test",
        base_url=None,
        source="db",
    )


@pytest.mark.asyncio
async def test_agent_default_wins_when_kind_enabled(monkeypatch):
    enabled_kinds = {"openai", "anthropic"}

    async def _read(_agent_id):
        return "anthropic:claude-opus-4"

    async def _from_db(*, workspace_id, prefer_kind=None):
        del workspace_id
        if prefer_kind is not None and prefer_kind not in enabled_kinds:
            return None
        kind = prefer_kind or next(iter(enabled_kinds))
        return _resolved(kind, model="auto")

    monkeypatch.setattr(model_client, "_read_agent_default_model", _read)
    monkeypatch.setattr(model_client, "_resolve_from_db", _from_db)

    resolved = await model_client.resolve_for_agent(
        workspace_id=uuid.uuid4(), agent_id=uuid.uuid4()
    )
    assert resolved is not None
    assert resolved.provider_kind == "anthropic"
    assert resolved.model_name == "claude-opus-4"
    assert resolved.source == "agent_default"


@pytest.mark.asyncio
async def test_agent_default_falls_back_when_kind_missing(monkeypatch):
    enabled_kinds = {"openai"}

    async def _read(_agent_id):
        return "anthropic:claude-opus-4"

    async def _from_db(*, workspace_id, prefer_kind=None):
        del workspace_id
        if prefer_kind is not None and prefer_kind not in enabled_kinds:
            return None
        return _resolved("openai", model="auto")

    monkeypatch.setattr(model_client, "_read_agent_default_model", _read)
    monkeypatch.setattr(model_client, "_resolve_from_db", _from_db)

    resolved = await model_client.resolve_for_agent(
        workspace_id=uuid.uuid4(), agent_id=uuid.uuid4()
    )
    assert resolved is not None
    assert resolved.provider_kind == "openai"
    assert resolved.source != "agent_default"


@pytest.mark.asyncio
async def test_override_wins_over_agent_default(monkeypatch):
    async def _read(_agent_id):
        raise AssertionError("agent_default should not be consulted")

    async def _from_db(*, workspace_id, prefer_kind=None):
        del workspace_id
        return _resolved(prefer_kind or "openai")

    monkeypatch.setattr(model_client, "_read_agent_default_model", _read)
    monkeypatch.setattr(model_client, "_resolve_from_db", _from_db)

    resolved = await model_client.resolve_for_agent(
        workspace_id=uuid.uuid4(),
        agent_id=uuid.uuid4(),
        override="openai:gpt-4o-mini",
    )
    assert resolved is not None
    assert resolved.provider_kind == "openai"
    assert resolved.model_name == "gpt-4o-mini"


@pytest.mark.asyncio
async def test_no_agent_default_falls_through_to_workspace(monkeypatch):
    async def _read(_agent_id):
        return None

    captured: dict[str, object] = {}

    async def _from_db(*, workspace_id, prefer_kind=None):
        captured["prefer_kind"] = prefer_kind
        captured["workspace_id"] = workspace_id
        return _resolved("openai")

    monkeypatch.setattr(model_client, "_read_agent_default_model", _read)
    monkeypatch.setattr(model_client, "_resolve_from_db", _from_db)

    ws = uuid.uuid4()
    resolved = await model_client.resolve_for_agent(
        workspace_id=ws, agent_id=uuid.uuid4()
    )
    assert resolved is not None
    assert resolved.provider_kind == "openai"
    assert captured["prefer_kind"] is None
    assert captured["workspace_id"] == ws
