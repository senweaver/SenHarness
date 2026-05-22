"""End-to-end DB integration test for the M2.5.8 session_search summarise.

Seeds 10 messages in a real workspace, drives ``run_session_search``
with summarise enabled, and asserts:

1. The ts_vector ranking actually returns the expected hits (the raw
   path still works after the M2.5.8 wrapping).
2. Aux LLM call is intercepted at the module seam, returns a structured
   summary citing two real ids + one fabricated id.
3. The fabricated id is filtered out — every returned
   ``evidence_message_ids`` entry corresponds to a row that exists in
   the messages table.
4. The ``summarize.invoked`` audit row lands and the ``summarize.evidence_filtered``
   row lands when there is a fabricated id.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from sqlalchemy import select

from app.agents.tools import session_search as tool
from app.agents.tools._context import ToolRunContext, set_context
from app.agents.tools.session_search import (
    AUDIT_EVIDENCE_FILTERED,
    AUDIT_INVOKED,
    SessionSearchArgs,
    SessionSearchSummary,
    run_session_search,
)
from app.db.models.audit import AuditEvent
from app.db.models.message import Message, MessageRole
from app.db.models.session import Session as ChatSession

pytestmark = pytest.mark.asyncio


_QUERY_SEED = "deployment runbook"
_TOPICAL_BODIES = [
    "We agreed the Tuesday deployment runbook needs a rollback step.",
    "Production deployment runbook should call out the canary cohort.",
    "Any deployment runbook update lands in /docs/ops next week.",
    "Ops team owns the deployment runbook hand-off.",
    "Reminder: deployment runbook rehearsal next Friday.",
]
_NOISE_BODIES = [
    "Lunch order for the offsite is pad thai.",
    "Calendar invite went out for the planning sync.",
    "Pet picture: behold the Q3 mascot.",
    "Reminder to expense the offsite hotel.",
    "Random snippet about birding in Yokohama.",
]


async def _seed_messages(db_session, *, workspace_id, owner_identity_id):
    chat = ChatSession(
        workspace_id=workspace_id,
        owner_identity_id=owner_identity_id,
        title="search smoke",
    )
    db_session.add(chat)
    await db_session.flush()

    rows: list[Message] = []
    for i, body in enumerate(_TOPICAL_BODIES + _NOISE_BODIES):
        rows.append(
            Message(
                workspace_id=workspace_id,
                session_id=chat.id,
                role=MessageRole.USER if i % 2 == 0 else MessageRole.ASSISTANT,
                content_json={"text": body},
            )
        )
    db_session.add_all(rows)
    await db_session.flush()
    await db_session.commit()
    return chat, rows


def _set_context(*, workspace_id, identity_id) -> ToolRunContext:
    ctx = ToolRunContext(
        run_id=uuid.uuid4(),
        workspace_id=workspace_id,
        session_id=uuid.uuid4(),
        identity_id=identity_id,
        agent_id=uuid.uuid4(),
        scratch_base=Path("."),
        policy={},
    )
    set_context(ctx)
    return ctx


async def test_summarize_e2e_returns_only_real_evidence_ids(
    db_session, workspace, identity, monkeypatch
):
    chat, msgs = await _seed_messages(
        db_session,
        workspace_id=workspace.id,
        owner_identity_id=identity.id,
    )
    real_ids = [m.id for m in msgs[:5]]
    fabricated_id = uuid.uuid4()

    captured_payload: dict = {}

    async def _stub_summarise(*, workspace_id, query, hits, summary_max_chars):
        captured_payload["workspace_id"] = workspace_id
        captured_payload["query"] = query
        captured_payload["hits"] = hits
        captured_payload["max_chars"] = summary_max_chars
        cited: list[uuid.UUID] = []
        for hit in hits[:2]:
            try:
                cited.append(uuid.UUID(hit["message_id"]))
            except ValueError:
                pass
        cited.append(fabricated_id)
        return SessionSearchSummary(
            summary=(
                "The team is consolidating the deployment runbook with a "
                "rollback step and a canary cohort callout, owned by Ops, "
                "with a rehearsal scheduled."
            ),
            bullet_points=[
                "Add an explicit rollback step.",
                "Call out the canary cohort.",
                "Rehearsal next Friday.",
            ],
            evidence_message_ids=cited,
        )

    monkeypatch.setattr(tool, "_summarise_hits", _stub_summarise)

    # Avoid hitting Redis from CI — make the breaker / rate gates pass.
    async def _open(*, bucket, workspace_id, trip_at):
        return False

    async def _consume(*, bucket, workspace_id, limit, period_seconds=60):
        return True

    async def _bump(*, bucket, workspace_id, window_seconds, recover_seconds=None):
        return 0

    async def _reset(*, bucket, workspace_id):
        return None

    async def _settings(db, *, workspace_id):
        return {
            "summarize_rate_per_minute": 30,
            "summarize_fail_strikes": 3,
            "summarize_fail_window_seconds": 300,
            "summarize_breaker_recover_seconds": 1800,
        }

    monkeypatch.setattr(tool, "is_breaker_open", _open)
    monkeypatch.setattr(tool, "consume_rate", _consume)
    monkeypatch.setattr(tool, "bump_failure", _bump)
    monkeypatch.setattr(tool, "reset_failure", _reset)
    monkeypatch.setattr(tool, "get_workspace_aux_settings", _settings)

    _set_context(workspace_id=workspace.id, identity_id=identity.id)

    out = await run_session_search(SessionSearchArgs(query=_QUERY_SEED, summarize=True, limit=8))

    assert out["summarized"] is True
    assert "deployment runbook" in (out["summary"] or "").lower()
    assert len(out["bullet_points"]) >= 1

    # Every returned evidence id MUST be one of the seeded message ids.
    returned_ids = [uuid.UUID(x) for x in out["evidence_message_ids"]]
    assert returned_ids, "summary must cite at least one real id"
    seeded_id_set = {m.id for m in msgs}
    for mid in returned_ids:
        assert mid in seeded_id_set, mid
    assert fabricated_id not in returned_ids

    # The aux LLM saw at most ``limit`` raw hits; each hit body matches
    # one of the seeded bodies (no other workspace leaked in).
    assert captured_payload["query"] == _QUERY_SEED
    assert captured_payload["workspace_id"] == workspace.id
    seeded_bodies = _TOPICAL_BODIES + _NOISE_BODIES
    for hit in captured_payload["hits"]:
        body = hit["body"] or ""
        assert any(body in seeded for seeded in seeded_bodies)
        assert hit["session_id"] == str(chat.id)

    # Audit rows must include both ``invoked`` and ``evidence_filtered``.
    rows = (
        (
            await db_session.execute(
                select(AuditEvent).where(
                    AuditEvent.workspace_id == workspace.id,
                    AuditEvent.resource_type == "session_search",
                )
            )
        )
        .scalars()
        .all()
    )
    actions = [r.action for r in rows]
    assert AUDIT_INVOKED in actions
    assert AUDIT_EVIDENCE_FILTERED in actions

    invoked_row = next(r for r in rows if r.action == AUDIT_INVOKED)
    meta = invoked_row.metadata_json or {}
    assert meta["evidence_count"] == len(returned_ids)
    assert meta["evidence_filtered"] >= 1
    assert meta["hit_count"] >= 1

    # The raw query must NEVER end up in the audit metadata. Hash only.
    assert "query" not in meta
    assert "query_hash" in meta
    _ = real_ids


async def test_summarize_with_session_filter_scopes_results(
    db_session, workspace, identity, monkeypatch
):
    """When ``session_id`` is passed, only messages from that session
    should be considered (existing M2 V2 behaviour preserved post-wrap).
    """
    chat_a, msgs_a = await _seed_messages(
        db_session, workspace_id=workspace.id, owner_identity_id=identity.id
    )
    chat_b, msgs_b = await _seed_messages(
        db_session, workspace_id=workspace.id, owner_identity_id=identity.id
    )

    captured: dict = {}

    async def _stub(*, workspace_id, query, hits, summary_max_chars):
        captured["session_ids"] = {h["session_id"] for h in hits}
        return SessionSearchSummary(summary="ok", bullet_points=[], evidence_message_ids=[])

    async def _open(*_, **__):
        return False

    async def _consume(*_, **__):
        return True

    async def _settings(db, *, workspace_id):
        return {
            "summarize_rate_per_minute": 30,
            "summarize_fail_strikes": 3,
            "summarize_fail_window_seconds": 300,
            "summarize_breaker_recover_seconds": 1800,
        }

    async def _bump(**_):
        return 0

    async def _reset(**_):
        return None

    monkeypatch.setattr(tool, "_summarise_hits", _stub)
    monkeypatch.setattr(tool, "is_breaker_open", _open)
    monkeypatch.setattr(tool, "consume_rate", _consume)
    monkeypatch.setattr(tool, "bump_failure", _bump)
    monkeypatch.setattr(tool, "reset_failure", _reset)
    monkeypatch.setattr(tool, "get_workspace_aux_settings", _settings)

    _set_context(workspace_id=workspace.id, identity_id=identity.id)
    await run_session_search(
        SessionSearchArgs(
            query=_QUERY_SEED,
            summarize=True,
            session_id=str(chat_a.id),
        )
    )
    assert captured["session_ids"] == {str(chat_a.id)}
    _ = msgs_a, msgs_b, chat_b
