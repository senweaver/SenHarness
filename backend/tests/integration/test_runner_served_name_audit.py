"""End-to-end M2.5.7 integration: runner emits served name on USAGE
+ writes ``provider.upstream_called`` audit when alias redirected.

The full ``NativeBackend.run`` cannot fire without a real LLM key,
so we exercise the resolver + audit writer at the same seam the
runner uses: ``_resolve_served_envelope`` and
``_audit_upstream_called``. This catches integration bugs (e.g. a
broken JSONB write of the alias map) without needing a working
provider account.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from app.db.models.audit import AuditEvent

pytestmark = pytest.mark.asyncio


async def test_workspace_alias_emits_provider_upstream_called(
    db_session, workspace, agent, identity
):
    """When the alias map redirects, runner writes a structured
    ``provider.upstream_called`` row carrying both names."""
    from app.agents.kernels.base import RunRequest
    from app.agents.kernels.native.runner import (
        _audit_upstream_called,
        _resolve_served_envelope,
    )
    from app.services.served_model import upsert_alias

    agent.served_model_name = "ws-fast"
    await upsert_alias(
        db_session,
        workspace_id=workspace.id,
        served_name="ws-fast",
        upstream="deepseek:deepseek-chat",
    )
    await db_session.commit()

    req = RunRequest(
        run_id=uuid.uuid4(),
        workspace_id=workspace.id,
        agent_id=agent.id,
        session_id=uuid.uuid4(),
        identity_id=identity.id,
        user_text="hello",
        message_history=[],
        toolbox=[],
        skills=[],
        policy={},
    )

    envelope = await _resolve_served_envelope(req)
    assert envelope.served_name == "ws-fast"
    assert envelope.matched_via == "workspace_alias"
    assert envelope.upstream == "deepseek:deepseek-chat"
    assert envelope.applied_override == "deepseek:deepseek-chat"

    await _audit_upstream_called(
        req=req,
        served_name=envelope.served_name,
        upstream="deepseek:deepseek-chat",
        provider_kind="deepseek",
    )

    rows = (
        await db_session.execute(
            select(AuditEvent).where(
                AuditEvent.workspace_id == workspace.id,
                AuditEvent.action == "provider.upstream_called",
            )
        )
    ).scalars().all()
    assert len(rows) >= 1
    meta = rows[-1].metadata_json or {}
    assert meta.get("served_model_name") == "ws-fast"
    assert meta.get("upstream_model") == "deepseek:deepseek-chat"
    assert meta.get("provider_kind") == "deepseek"


async def test_no_alias_no_upstream_audit(db_session, workspace, agent):
    """Agent has a served name but no alias mapping → no audit row.

    The matched_via is ``agent_field`` and the runner does not bump
    ``provider.upstream_called`` (audit cardinality stays low).
    """
    from app.agents.kernels.base import RunRequest
    from app.agents.kernels.native.runner import _resolve_served_envelope

    agent.served_model_name = "ws-fast"
    await db_session.commit()

    req = RunRequest(
        run_id=uuid.uuid4(),
        workspace_id=workspace.id,
        agent_id=agent.id,
        session_id=uuid.uuid4(),
        identity_id=uuid.uuid4(),
        user_text="hi",
        message_history=[],
        toolbox=[],
        skills=[],
        policy={},
    )

    envelope = await _resolve_served_envelope(req)
    assert envelope.served_name == "ws-fast"
    assert envelope.matched_via == "agent_field"
    # Without alias mapping, upstream is the served name itself,
    # and we leave ``model_override`` alone.
    assert envelope.upstream == "ws-fast"
    assert envelope.applied_override is None


async def test_per_turn_override_blocks_alias_redirect(db_session, workspace, agent):
    """Even with an alias, an explicit ``model_override`` from the
    composer wins. The runner must not silently second-guess the
    user's per-turn pick.
    """
    from app.agents.kernels.base import RunRequest
    from app.agents.kernels.native.runner import _resolve_served_envelope
    from app.services.served_model import upsert_alias

    agent.served_model_name = "ws-fast"
    await upsert_alias(
        db_session,
        workspace_id=workspace.id,
        served_name="ws-fast",
        upstream="deepseek:deepseek-chat",
    )
    await db_session.commit()

    req = RunRequest(
        run_id=uuid.uuid4(),
        workspace_id=workspace.id,
        agent_id=agent.id,
        session_id=uuid.uuid4(),
        identity_id=uuid.uuid4(),
        user_text="hi",
        message_history=[],
        toolbox=[],
        skills=[],
        policy={},
        model_override="anthropic:claude-3-5-sonnet",
    )

    envelope = await _resolve_served_envelope(req)
    assert envelope.matched_via == "workspace_alias"
    # served_name + envelope.upstream still show the alias, but the
    # composer's per-turn pick is preserved (no override applied).
    assert envelope.applied_override is None


async def test_usage_blob_uses_served_when_present():
    """Pure-function check on ``_build_usage_json`` — both shapes."""
    from app.api.v1.sessions import _build_usage_json

    blob = _build_usage_json(
        {
            "tokens": {"input": 10, "output": 5},
            "cost": 0.0,
            "served_model": "ws-fast",
            "upstream_model": "deepseek:deepseek-chat",
            "provider": "deepseek",
            "model": "ws-fast",
        }
    )
    assert blob["model"] == "ws-fast"
    assert blob["upstream_model"] == "deepseek:deepseek-chat"

    # When served == upstream the upstream key is suppressed (low cardinality).
    blob2 = _build_usage_json(
        {
            "tokens": {"input": 10, "output": 5},
            "cost": 0.0,
            "served_model": "openai:gpt-4o-mini",
            "upstream_model": "openai:gpt-4o-mini",
            "provider": "openai",
            "model": "openai:gpt-4o-mini",
        }
    )
    assert blob2["model"] == "openai:gpt-4o-mini"
    assert "upstream_model" not in blob2
