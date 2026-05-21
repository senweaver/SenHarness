"""Unit tests for :mod:`app.services.served_model` resolver (M2.5.7).

Exercises the four resolution branches:

* Agent has no ``served_model_name`` and no fallback → empty envelope.
* Agent has no ``served_model_name`` but a fallback exists → ``matched_via='fallback'``.
* Agent has ``served_model_name`` set, no alias mapping → ``matched_via='agent_field'``.
* Agent has ``served_model_name`` set, alias mapping exists → ``matched_via='workspace_alias'``.

Plus a cross-workspace isolation test: an alias defined on workspace A
must not leak into workspace B's resolution.
"""

from __future__ import annotations

from app.schemas.served_model import ServedAliasMap


def test_alias_map_validates_keys_and_values():
    payload = ServedAliasMap(
        aliases={
            "ws-fast": "deepseek:deepseek-chat",
            "acme/chat-2025": "openai:gpt-4o-mini",
        }
    )
    assert payload.aliases["ws-fast"] == "deepseek:deepseek-chat"


def test_alias_map_rejects_blank_key():
    import pytest

    with pytest.raises(ValueError):
        ServedAliasMap(aliases={"": "openai:gpt-4o-mini"})


def test_alias_map_rejects_blank_value():
    import pytest

    with pytest.raises(ValueError):
        ServedAliasMap(aliases={"ws-fast": ""})


def test_alias_map_rejects_illegal_chars():
    import pytest

    # Spaces are not in the allowed character class.
    with pytest.raises(ValueError):
        ServedAliasMap(aliases={"ws fast": "openai:gpt-4o-mini"})


# ─── DB-backed branches ──────────────────────────────────────


async def test_empty_envelope_when_no_input(db_session, workspace):
    from app.services.served_model import resolve_served_model

    out = await resolve_served_model(
        db_session, workspace_id=workspace.id, agent=None
    )
    assert out.served_name == ""
    assert out.upstream == ""
    assert out.matched_via == "fallback"


async def test_fallback_path_when_agent_field_absent(db_session, workspace, agent):
    from app.services.served_model import resolve_served_model

    out = await resolve_served_model(
        db_session,
        workspace_id=workspace.id,
        agent=agent,
        fallback_upstream="openai:gpt-4o-mini",
    )
    assert out.served_name == "openai:gpt-4o-mini"
    assert out.upstream == "openai:gpt-4o-mini"
    assert out.matched_via == "fallback"


async def test_agent_field_passes_through_when_no_alias(
    db_session, workspace, agent
):
    from app.services.served_model import resolve_served_model

    agent.served_model_name = "ws-fast"
    await db_session.flush()

    out = await resolve_served_model(
        db_session,
        workspace_id=workspace.id,
        agent=agent,
        fallback_upstream="openai:gpt-4o-mini",
    )
    assert out.served_name == "ws-fast"
    # No alias mapping → upstream falls through to served name.
    assert out.upstream == "ws-fast"
    assert out.matched_via == "agent_field"


async def test_workspace_alias_redirects_upstream(db_session, workspace, agent):
    from app.services.served_model import (
        resolve_served_model,
        upsert_alias,
    )

    agent.served_model_name = "ws-fast"
    await upsert_alias(
        db_session,
        workspace_id=workspace.id,
        served_name="ws-fast",
        upstream="deepseek:deepseek-chat",
    )
    await db_session.flush()

    out = await resolve_served_model(
        db_session,
        workspace_id=workspace.id,
        agent=agent,
    )
    assert out.served_name == "ws-fast"
    assert out.upstream == "deepseek:deepseek-chat"
    assert out.matched_via == "workspace_alias"


async def test_alias_map_does_not_leak_across_workspaces(
    db_session, workspace, identity
):
    """An alias on workspace A must not affect workspace B."""
    from app.services import workspace as ws_svc
    from app.services.served_model import (
        resolve_served_model,
        upsert_alias,
    )

    other = await ws_svc.create_workspace(
        db_session,
        name="Other",
        slug=f"other-{workspace.id.hex[:8]}",
        owner_identity_id=identity.id,
    )
    await db_session.flush()

    await upsert_alias(
        db_session,
        workspace_id=workspace.id,
        served_name="ws-fast",
        upstream="deepseek:deepseek-chat",
    )
    await db_session.flush()

    # Workspace ``other`` has no alias; resolver returns served=upstream.
    class _StubAgent:
        served_model_name = "ws-fast"

    out = await resolve_served_model(
        db_session,
        workspace_id=other.id,
        agent=_StubAgent(),
    )
    assert out.served_name == "ws-fast"
    assert out.upstream == "ws-fast"
    assert out.matched_via == "agent_field"


async def test_upsert_then_delete_round_trip(db_session, workspace):
    from app.services.served_model import (
        delete_alias,
        get_alias_map,
        upsert_alias,
    )

    await upsert_alias(
        db_session,
        workspace_id=workspace.id,
        served_name="ws-fast",
        upstream="deepseek:deepseek-chat",
    )
    await upsert_alias(
        db_session,
        workspace_id=workspace.id,
        served_name="ws-thinking",
        upstream="openai:gpt-5",
    )
    await db_session.flush()

    aliases = await get_alias_map(db_session, workspace_id=workspace.id)
    assert aliases == {
        "ws-fast": "deepseek:deepseek-chat",
        "ws-thinking": "openai:gpt-5",
    }

    await delete_alias(
        db_session, workspace_id=workspace.id, served_name="ws-fast"
    )
    await db_session.flush()
    aliases = await get_alias_map(db_session, workspace_id=workspace.id)
    assert aliases == {"ws-thinking": "openai:gpt-5"}

    # Idempotent: deleting an unknown key is a no-op.
    await delete_alias(
        db_session, workspace_id=workspace.id, served_name="never-existed"
    )
    aliases = await get_alias_map(db_session, workspace_id=workspace.id)
    assert aliases == {"ws-thinking": "openai:gpt-5"}
