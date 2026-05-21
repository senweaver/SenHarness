"""Unit tests for :func:`list_served_models_for_workspace` (M2.5.7).

The ``/v1/models`` listing dedupes the union of agent-declared
``served_model_name`` values and workspace alias map keys. Order is
stable (alphabetical) so OpenAI clients caching the listing don't
flap on harmless re-orderings.
"""

from __future__ import annotations

import uuid


async def _make_agent(db_session, *, workspace, served: str | None) -> None:
    from app.services import agent as agent_svc

    a = await agent_svc.create_agent(
        db_session,
        workspace_id=workspace.id,
        created_by=None,
        name=f"agent-{uuid.uuid4().hex[:8]}",
        description="served-list test",
        persona_md="x",
    )
    if served is not None:
        a.served_model_name = served
    await db_session.flush()


async def test_empty_workspace_returns_empty_list(db_session, workspace):
    from app.services.served_model import list_served_models_for_workspace

    out = await list_served_models_for_workspace(
        db_session, workspace_id=workspace.id
    )
    assert out == []


async def test_union_of_agent_field_and_alias_map(db_session, workspace):
    """5 agents (each with a distinct served name) + 3 alias map keys
    (one of which collides with an agent) → 7 distinct entries.
    """
    from app.services.served_model import (
        list_served_models_for_workspace,
        upsert_alias,
    )

    for served in ("ws-fast", "ws-thinking", "ws-mini", "ws-coder", "ws-agent"):
        await _make_agent(db_session, workspace=workspace, served=served)

    # 3 alias keys; ``ws-fast`` collides with an existing agent's name.
    await upsert_alias(
        db_session,
        workspace_id=workspace.id,
        served_name="ws-fast",
        upstream="deepseek:deepseek-chat",
    )
    await upsert_alias(
        db_session,
        workspace_id=workspace.id,
        served_name="alias-only-a",
        upstream="openai:gpt-4o-mini",
    )
    await upsert_alias(
        db_session,
        workspace_id=workspace.id,
        served_name="alias-only-b",
        upstream="anthropic:claude-3-5-sonnet",
    )
    await db_session.flush()

    out = await list_served_models_for_workspace(
        db_session, workspace_id=workspace.id
    )
    served_names = [e.served_name for e in out]

    # 5 unique agent names + 2 unique alias-only keys = 7
    assert len(served_names) == 7
    assert sorted(served_names) == served_names  # alphabetical

    by_name = {e.served_name: e for e in out}

    # Alias-redirected entry uses the alias upstream + carries the
    # collision agent_id reference.
    assert by_name["ws-fast"].matched_via == "workspace_alias"
    assert by_name["ws-fast"].upstream == "deepseek:deepseek-chat"
    assert by_name["ws-fast"].agent_id is not None

    # Alias-only entry has no agent_id.
    assert by_name["alias-only-a"].matched_via == "workspace_alias"
    assert by_name["alias-only-a"].agent_id is None

    # Agent-only entry has the agent_id and falls through to served=upstream.
    assert by_name["ws-mini"].matched_via == "agent_field"
    assert by_name["ws-mini"].upstream == "ws-mini"
    assert by_name["ws-mini"].agent_id is not None


async def test_dedupes_multiple_agents_sharing_served_name(
    db_session, workspace
):
    """Two agents with the same ``served_model_name`` collapse to one row."""
    from app.services.served_model import list_served_models_for_workspace

    await _make_agent(db_session, workspace=workspace, served="ws-fast")
    await _make_agent(db_session, workspace=workspace, served="ws-fast")
    await _make_agent(db_session, workspace=workspace, served="ws-thinking")

    out = await list_served_models_for_workspace(
        db_session, workspace_id=workspace.id
    )
    assert sorted(e.served_name for e in out) == ["ws-fast", "ws-thinking"]


async def test_excludes_soft_deleted_agents(db_session, workspace):
    """A soft-deleted agent's served name must not show up."""
    from datetime import UTC, datetime

    from app.services import agent as agent_svc
    from app.services.served_model import list_served_models_for_workspace

    a = await agent_svc.create_agent(
        db_session,
        workspace_id=workspace.id,
        created_by=None,
        name="ghost",
        description="x",
        persona_md="x",
    )
    a.served_model_name = "ws-ghost"
    await db_session.flush()

    a.deleted_at = datetime.now(UTC).replace(tzinfo=None)
    await db_session.flush()

    out = await list_served_models_for_workspace(
        db_session, workspace_id=workspace.id
    )
    assert "ws-ghost" not in [e.served_name for e in out]
