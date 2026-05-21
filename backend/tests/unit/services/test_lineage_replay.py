"""Service-layer unit tests for the M4.3 lineage replay surface.

Covers:

* happy path — a summary message resolves back to its original turns;
* not-a-summary — a message without ``original_turns_ref`` returns
  ``None`` (the API layer maps that to 404 ``lineage.not_compressed``);
* cross-workspace isolation — a summary id resolved with a foreign
  workspace must raise :class:`NotFound` rather than leak the row;
* the per-session summaries listing returns one row per compressed
  summary with the right ``turn_count`` projection.
"""

from __future__ import annotations

import uuid
from datetime import datetime

import pytest

from app.core.errors import NotFound
from app.services import lineage_replay as lineage_svc
from app.services import session as session_svc

pytestmark = pytest.mark.asyncio


async def _seed_session(db_session, workspace, identity):
    return await session_svc.create_session(
        db_session,
        workspace_id=workspace.id,
        owner_identity_id=identity.id,
    )


async def _append(db_session, session_obj, *, role, text):
    return await session_svc.append_message(
        db_session,
        session_obj=session_obj,
        role=role,
        content_json={"text": text},
    )


async def _seed_compressed_pair(db_session, sess):
    """Seed three originals + one summary; return (summary, originals)."""
    a = await _append(
        db_session, sess, role=session_svc.MessageRole.USER, text="first user"
    )
    b = await _append(
        db_session,
        sess,
        role=session_svc.MessageRole.ASSISTANT,
        text="first assistant " * 20,
    )
    c = await _append(
        db_session, sess, role=session_svc.MessageRole.USER, text="second user"
    )
    summary = await _append(
        db_session,
        sess,
        role=session_svc.MessageRole.SYSTEM,
        text="compacted summary text",
    )
    ref = lineage_svc.mark_message_as_compressed(
        summary, [a, b, c], strategy="sliding_window"
    )
    summary.original_turns_ref = ref
    for original in (a, b, c):
        original.compressed_into_summary_id = summary.id
    await db_session.flush()
    return summary, [a, b, c]


async def test_get_lineage_replay_happy_path(db_session, workspace, identity):
    sess = await _seed_session(db_session, workspace, identity)
    summary, originals = await _seed_compressed_pair(db_session, sess)

    replay = await lineage_svc.get_lineage_replay(
        db_session,
        workspace_id=workspace.id,
        session_id=sess.id,
        message_id=summary.id,
    )
    assert replay is not None
    assert replay.summary_message_id == summary.id
    assert replay.original_turn_count == 3
    assert replay.compaction_strategy == "sliding_window"
    assert isinstance(replay.compressed_at, datetime)
    assert {n.message_id for n in replay.original_turns} == {
        m.id for m in originals
    }
    long_excerpt = next(
        n
        for n in replay.original_turns
        if str(n.role) == "assistant"
    ).text_excerpt
    # Excerpts must respect the 200-char cap.
    assert len(long_excerpt) <= 200


async def test_get_lineage_replay_returns_none_when_not_a_summary(
    db_session, workspace, identity
):
    sess = await _seed_session(db_session, workspace, identity)
    plain = await _append(
        db_session, sess, role=session_svc.MessageRole.USER, text="plain"
    )

    replay = await lineage_svc.get_lineage_replay(
        db_session,
        workspace_id=workspace.id,
        session_id=sess.id,
        message_id=plain.id,
    )
    assert replay is None


async def test_get_lineage_replay_cross_workspace_blocked(
    db_session, workspace, identity
):
    sess = await _seed_session(db_session, workspace, identity)
    summary, _ = await _seed_compressed_pair(db_session, sess)

    foreign_ws = uuid.uuid4()
    with pytest.raises(NotFound):
        await lineage_svc.get_lineage_replay(
            db_session,
            workspace_id=foreign_ws,
            session_id=sess.id,
            message_id=summary.id,
        )


async def test_list_compressed_summaries_in_session(
    db_session, workspace, identity
):
    sess = await _seed_session(db_session, workspace, identity)
    summary, _ = await _seed_compressed_pair(db_session, sess)

    rows = await lineage_svc.list_compressed_summaries_in_session(
        db_session, workspace_id=workspace.id, session_id=sess.id
    )
    assert len(rows) == 1
    assert rows[0]["summary_message_id"] == summary.id
    assert rows[0]["turn_count"] == 3
    assert rows[0]["compaction_strategy"] == "sliding_window"
    assert isinstance(rows[0]["compressed_at"], datetime)
