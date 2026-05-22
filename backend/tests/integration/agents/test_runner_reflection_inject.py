"""DB-backed integration tests for the M0.4 / M0.5 reflection wiring.

Drives ``ReliabilityState.should_reflect()`` + ``inject_ephemeral_system_message``
+ ``audit_reflection`` end-to-end against a real Postgres so the audit row
actually lands. The full ``Agent.iter()`` graph isn't exercised (no LLM in
tests) — we use stub pydantic-ai message objects to mimic the runner's mutation
contract.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

import pytest
from sqlalchemy import select

from app.agents.harness.reliability import ReflectionConfig, build_state
from app.agents.kernels.native._reflection import (
    audit_reflection,
    build_reflection_config,
    inject_ephemeral_system_message,
    load_workspace_reflection_settings,
)
from app.db.models.audit import AuditEvent

pytestmark = pytest.mark.asyncio


@dataclass
class _StubNode:
    request: Any


def _make_request_node():
    from pydantic_ai.messages import ModelRequest, UserPromptPart

    return _StubNode(request=ModelRequest(parts=[UserPromptPart(content="q")]))


# ─── Periodic trigger end-to-end ──────────────────────────────────
async def test_eight_iterations_inject_one_audit_row(db_session, workspace, identity):
    """Eight ticks → one PERIODIC injection → exactly one audit row."""
    cfg = ReflectionConfig(interval_iterations=8, interval_tool_calls=999)
    state = build_state(policy={}, max_iterations=12, reflection_config=cfg)
    fired = 0
    for _ in range(8):
        state.tick_iteration()
        decision = state.should_reflect()
        if decision.should_inject:
            fired += 1
            node = _make_request_node()
            assert inject_ephemeral_system_message(node, decision.rendered_prompt or "")
            await audit_reflection(
                workspace_id=workspace.id,
                actor_identity_id=identity.id,
                run_id=uuid.uuid4(),
                session_id=None,
                kind=decision.kind,  # type: ignore[arg-type]
                iteration=state.iteration_count,
                tool_call_count=state.tool_call_count,
                prompt_chars=len(decision.rendered_prompt or ""),
                truncated=decision.truncated,
            )
    assert fired == 1
    rows = (
        (
            await db_session.execute(
                select(AuditEvent).where(
                    AuditEvent.workspace_id == workspace.id,
                    AuditEvent.action == "reflection.injected",
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(rows) == 1
    meta = rows[0].metadata_json
    assert meta["kind"] == "periodic"
    assert meta["iteration"] == 8
    assert meta["truncated"] is False
    assert meta["prompt_chars"] > 0


async def test_sixteen_tool_calls_inject_one_audit_row(db_session, workspace, identity):
    """16 tool calls (with no iter trigger) → one TOOL_CALL injection."""
    cfg = ReflectionConfig(interval_iterations=999, interval_tool_calls=15)
    state = build_state(policy={}, max_iterations=12, reflection_config=cfg)
    fired = 0
    for _ in range(16):
        state.tick_iteration()
        state.tick_tool_call()
        decision = state.should_reflect()
        if decision.should_inject:
            fired += 1
            await audit_reflection(
                workspace_id=workspace.id,
                actor_identity_id=identity.id,
                run_id=uuid.uuid4(),
                session_id=None,
                kind=decision.kind,  # type: ignore[arg-type]
                iteration=state.iteration_count,
                tool_call_count=state.tool_call_count,
                prompt_chars=len(decision.rendered_prompt or ""),
                truncated=decision.truncated,
            )
    assert fired == 1
    rows = (
        (
            await db_session.execute(
                select(AuditEvent).where(
                    AuditEvent.workspace_id == workspace.id,
                    AuditEvent.action == "reflection.injected",
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(rows) == 1
    assert rows[0].metadata_json["kind"] == "tool_call"


async def test_audit_metadata_never_includes_prompt_body(db_session, workspace, identity):
    """The audit row must hold ``prompt_chars`` but NEVER the rendered body."""
    await audit_reflection(
        workspace_id=workspace.id,
        actor_identity_id=identity.id,
        run_id=uuid.uuid4(),
        session_id=None,
        kind=__import__(
            "app.agents.harness.reliability", fromlist=["ReflectionKind"]
        ).ReflectionKind.PERIODIC,
        iteration=8,
        tool_call_count=0,
        prompt_chars=512,
        truncated=False,
    )
    rows = (
        (
            await db_session.execute(
                select(AuditEvent).where(
                    AuditEvent.action == "reflection.injected",
                    AuditEvent.workspace_id == workspace.id,
                )
            )
        )
        .scalars()
        .all()
    )
    assert rows
    for row in rows:
        assert "prompt" not in (row.metadata_json or {}) or (
            row.metadata_json.get("prompt") is None
        )
        assert "rendered_prompt" not in (row.metadata_json or {})


# ─── Workspace-level disable ──────────────────────────────────────
async def test_workspace_disable_blocks_inject(db_session, workspace, identity):
    """Setting ``home_config_json.reflection.enabled=False`` short-circuits
    injection regardless of agent policy."""
    workspace.home_config_json = {
        **(workspace.home_config_json or {}),
        "reflection": {"enabled": False},
    }
    await db_session.flush()
    await db_session.commit()

    cfg = await build_reflection_config(workspace_id=workspace.id, agent_policy={})
    assert cfg.enabled is False
    state = build_state(policy={}, max_iterations=12, reflection_config=cfg)
    for _ in range(20):
        state.tick_iteration()
        state.tick_tool_call()
        decision = state.should_reflect()
        assert decision.should_inject is False


async def test_workspace_settings_loader_returns_dict(db_session, workspace):
    workspace.home_config_json = {
        "reflection": {"interval_iterations": 4},
        "other": "x",
    }
    await db_session.flush()
    await db_session.commit()
    settings = await load_workspace_reflection_settings(workspace.id)
    assert settings.get("reflection", {}).get("interval_iterations") == 4
    assert settings.get("other") == "x"


async def test_missing_workspace_returns_empty():
    settings = await load_workspace_reflection_settings(uuid.uuid4())
    assert settings == {}
