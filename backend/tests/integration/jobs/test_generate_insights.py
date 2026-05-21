"""ARQ task: ``generate_insights`` end-to-end with a mocked aux LLM.

Covers:

* Happy path → aux returns structured items, markdown lands as an
  assistant message in the return session, audit
  ``insights.cross_session_summarized`` is written.
* Empty backlog → "no insights yet" markdown, ``artifact_count=0``
  audit.
* Aux failure → heuristic fallback runs and ``insights.aux_skipped``
  audit lands with ``reason='aux_failure'``.
* Privacy gate → another identity's artifacts in the same workspace
  are NOT included in the summary input.
"""

from __future__ import annotations

import uuid
from unittest.mock import patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.agents.auxiliary_client import AuxiliaryConfig, AuxiliaryTask

pytestmark = pytest.mark.asyncio


async def _bootstrap_workspace(async_client) -> tuple[dict, str, str]:
    email = f"insights-job-{uuid.uuid4().hex[:8]}@example.com"
    password = "insights-test-password-very-long"
    r = await async_client.post(
        "/api/v1/auth/register",
        json={"email": email, "name": "Insights Job", "password": password},
    )
    assert r.status_code == 201, r.text
    r = await async_client.post(
        "/api/v1/auth/login", json={"email": email, "password": password}
    )
    token = r.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    r = await async_client.post(
        "/api/v1/workspaces",
        headers=headers,
        json={"name": "Insights WS", "slug": f"ijob-{uuid.uuid4().hex[:6]}"},
    )
    assert r.status_code in (200, 201), r.text
    ws_id = r.json()["id"]
    headers["X-Workspace-Id"] = ws_id
    r = await async_client.post(
        "/api/v1/sessions", headers=headers, json={"kind": "p2p"}
    )
    sid = r.json()["id"]
    return headers, ws_id, sid


def _identity_id_from_token(headers: dict) -> str:
    from app.core.security import decode_token

    raw = headers["Authorization"].split(" ", 1)[1]
    return str(decode_token(raw, expected_kind="access")["sub"])


async def _seed_artifact(
    *,
    workspace_id: str,
    session_id: str,
    identity_id: str,
    error_kind: str | None = None,
    invoked_tools: list[str] | None = None,
    judge_score: float | None = None,
) -> str:
    from app.db.session import get_session_factory
    from app.services import session_artifact as artifact_svc

    factory: async_sessionmaker = get_session_factory()
    async with factory() as db:
        events: list[dict] = [{"kind": "final", "data": {"text": "ok"}}]
        if invoked_tools:
            for name in invoked_tools:
                events.insert(
                    0, {"kind": "tool_call", "data": {"name": name, "args": {}}}
                )
        row = await artifact_svc.capture_artifact(
            db,
            run_id=uuid.uuid4(),
            workspace_id=uuid.UUID(workspace_id),
            session_id=uuid.UUID(session_id),
            agent_id=None,
            identity_id=uuid.UUID(identity_id),
            user_text="seed",
            events=events,
            final_outcome="error" if error_kind else "success",
            error_kind=error_kind,
        )
        if judge_score is not None:
            row.judge_score = float(judge_score)
            await db.flush([row])
        await db.commit()
        return str(row.id)


async def _list_audit_actions(*, workspace_id: str, action: str) -> list[dict]:
    from app.db.models.audit import AuditEvent
    from app.db.session import get_session_factory

    factory = get_session_factory()
    async with factory() as db:
        rows = (
            (
                await db.execute(
                    select(AuditEvent).where(
                        AuditEvent.workspace_id == uuid.UUID(workspace_id),
                        AuditEvent.action == action,
                    )
                )
            )
            .scalars()
            .all()
        )
    return [
        {
            "summary": r.summary,
            "metadata": dict(r.metadata_json or {}),
            "resource_id": r.resource_id,
        }
        for r in rows
    ]


async def _list_messages(*, session_id: str) -> list[dict]:
    from app.db.session import get_session_factory
    from app.repositories.session import MessageRepository

    factory = get_session_factory()
    async with factory() as db:
        msgs = await MessageRepository(db).list_for_session(
            session_id=uuid.UUID(session_id), limit=100
        )
    return [
        {
            "role": str(m.role.value if hasattr(m.role, "value") else m.role),
            "text": (m.content_json or {}).get("text"),
            "metadata": dict(m.metadata_json or {}),
        }
        for m in msgs
    ]


def _aux_config() -> AuxiliaryConfig:
    return AuxiliaryConfig(task=AuxiliaryTask.SUMMARIZE, model="test:fake")


async def test_generate_insights_happy_path(async_client):
    from app.jobs import insights as insights_mod

    headers, ws_id, sid = await _bootstrap_workspace(async_client)
    identity_id = _identity_id_from_token(headers)

    aid_a = await _seed_artifact(
        workspace_id=ws_id,
        session_id=sid,
        identity_id=identity_id,
        error_kind="ToolNotFound",
        invoked_tools=["read_file", "write_file"],
        judge_score=-1.0,
    )
    aid_b = await _seed_artifact(
        workspace_id=ws_id,
        session_id=sid,
        identity_id=identity_id,
        error_kind="ToolNotFound",
        invoked_tools=["read_file"],
        judge_score=0.0,
    )

    async def fake_call_aux_chat(*, config, system, user, response_format, timeout_s):
        _ = (config, system, user, timeout_s)
        return response_format.model_validate(
            {
                "items": [
                    {
                        "title": "Recurring ToolNotFound",
                        "summary": "Two of your last runs failed with ToolNotFound.",
                        "category": "frequent_failure",
                        "evidence_artifact_ids": [aid_a, aid_b],
                    }
                ]
            }
        )

    async def fake_resolve_aux_config(db, *, workspace_id):
        _ = (db, workspace_id)
        return _aux_config()

    with (
        patch.object(insights_mod, "call_aux_chat", fake_call_aux_chat),
        patch.object(insights_mod, "_resolve_aux_config", fake_resolve_aux_config),
    ):
        result = await insights_mod.generate_insights(
            {},
            workspace_id=ws_id,
            identity_id=identity_id,
            return_session_id=sid,
            days=30,
        )

    assert result["status"] == "ok"
    assert result["artifact_count"] == 2
    assert result["item_count"] == 1
    assert result["aux_model"] == "test:fake"
    assert result["degraded"] is False

    msgs = await _list_messages(session_id=sid)
    assistant_msgs = [m for m in msgs if m["role"] == "assistant"]
    assert len(assistant_msgs) == 1
    body = assistant_msgs[0]["text"]
    assert "Cross-session insights" in body
    assert "Recurring ToolNotFound" in body
    # Evidence link points to the chat session, not the artifact id.
    assert f"(/?session={sid})" in body
    assert assistant_msgs[0]["metadata"]["kind"] == "cross_session_insights"
    assert assistant_msgs[0]["metadata"]["item_count"] == 1

    audits = await _list_audit_actions(
        workspace_id=ws_id, action="insights.cross_session_summarized"
    )
    assert len(audits) == 1
    assert audits[0]["metadata"]["artifact_count"] == 2
    assert audits[0]["metadata"]["item_count"] == 1


async def test_generate_insights_empty_backlog(async_client):
    from app.jobs import insights as insights_mod

    headers, ws_id, sid = await _bootstrap_workspace(async_client)
    identity_id = _identity_id_from_token(headers)

    result = await insights_mod.generate_insights(
        {},
        workspace_id=ws_id,
        identity_id=identity_id,
        return_session_id=sid,
        days=30,
    )
    assert result["status"] == "empty"
    assert result["artifact_count"] == 0

    msgs = await _list_messages(session_id=sid)
    assistant_msgs = [m for m in msgs if m["role"] == "assistant"]
    assert len(assistant_msgs) == 1
    body = assistant_msgs[0]["text"]
    assert "No insights yet" in body
    assert assistant_msgs[0]["metadata"]["kind"] == "cross_session_insights"

    audits = await _list_audit_actions(
        workspace_id=ws_id, action="insights.cross_session_summarized"
    )
    assert len(audits) == 1
    assert audits[0]["metadata"]["artifact_count"] == 0


async def test_generate_insights_privacy_filters_other_identity(async_client):
    """Artifacts owned by a different identity must not feed the aux prompt."""
    from app.jobs import insights as insights_mod

    headers, ws_id, sid = await _bootstrap_workspace(async_client)
    identity_id = _identity_id_from_token(headers)

    # Seed two artifacts owned by the caller and one owned by an
    # unrelated identity (synthesised UUID — the integration test
    # doesn't need a real second user, only a foreign identity_id
    # column on the artifact row).
    await _seed_artifact(
        workspace_id=ws_id,
        session_id=sid,
        identity_id=identity_id,
        error_kind="MyError",
        judge_score=-1.0,
    )
    await _seed_artifact(
        workspace_id=ws_id,
        session_id=sid,
        identity_id=identity_id,
        error_kind="MyError",
        judge_score=-1.0,
    )
    foreign_identity = str(uuid.uuid4())
    await _seed_artifact(
        workspace_id=ws_id,
        session_id=sid,
        identity_id=foreign_identity,
        error_kind="OtherUserError",
        judge_score=-1.0,
    )

    captured: dict = {}

    async def capture_user_prompt(
        *, config, system, user, response_format, timeout_s
    ):
        _ = (config, system, timeout_s)
        captured["user"] = user
        return response_format.model_validate({"items": []})

    async def fake_resolve_aux_config(db, *, workspace_id):
        _ = (db, workspace_id)
        return _aux_config()

    with (
        patch.object(insights_mod, "call_aux_chat", capture_user_prompt),
        patch.object(insights_mod, "_resolve_aux_config", fake_resolve_aux_config),
    ):
        result = await insights_mod.generate_insights(
            {},
            workspace_id=ws_id,
            identity_id=identity_id,
            return_session_id=sid,
            days=30,
        )
    # Only the two same-identity artifacts make it into the aux input.
    assert result["artifact_count"] == 2
    assert "OtherUserError" not in captured.get("user", "")
    assert "MyError" in captured.get("user", "")


async def test_generate_insights_aux_failure_falls_back_to_heuristic(
    async_client,
):
    from app.jobs import insights as insights_mod

    headers, ws_id, sid = await _bootstrap_workspace(async_client)
    identity_id = _identity_id_from_token(headers)
    await _seed_artifact(
        workspace_id=ws_id,
        session_id=sid,
        identity_id=identity_id,
        error_kind="HeuristicMode",
        judge_score=-1.0,
    )
    await _seed_artifact(
        workspace_id=ws_id,
        session_id=sid,
        identity_id=identity_id,
        error_kind="HeuristicMode",
        judge_score=-1.0,
    )

    async def fake_call_aux_chat(*, config, system, user, response_format, timeout_s):
        _ = (config, system, user, response_format, timeout_s)
        return None

    async def fake_resolve_aux_config(db, *, workspace_id):
        _ = (db, workspace_id)
        return _aux_config()

    with (
        patch.object(insights_mod, "call_aux_chat", fake_call_aux_chat),
        patch.object(insights_mod, "_resolve_aux_config", fake_resolve_aux_config),
    ):
        result = await insights_mod.generate_insights(
            {},
            workspace_id=ws_id,
            identity_id=identity_id,
            return_session_id=sid,
            days=30,
        )

    assert result["status"] == "degraded"
    assert result["degraded"] is True

    skipped = await _list_audit_actions(
        workspace_id=ws_id, action="insights.aux_skipped"
    )
    assert len(skipped) == 1
    assert skipped[0]["metadata"]["reason"] == "aux_failure"

    msgs = await _list_messages(session_id=sid)
    assistant_msgs = [m for m in msgs if m["role"] == "assistant"]
    assert len(assistant_msgs) == 1
    assert "degraded" in assistant_msgs[0]["text"].lower()
    assert "HeuristicMode" in assistant_msgs[0]["text"]
