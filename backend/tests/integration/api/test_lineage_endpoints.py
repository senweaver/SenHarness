"""End-to-end tests for the M4.3 lineage replay routes.

Covers each of the two routes:

* ``GET /sessions/{session_id}/messages/{message_id}/lineage``
* ``GET /sessions/{session_id}/lineage-summaries``

with a happy path, a 404 ``lineage.not_compressed`` shape, and the
cross-workspace isolation case demanded by the cross-cutting checklist.
"""

from __future__ import annotations

import uuid

import pytest

pytestmark = pytest.mark.asyncio


async def _bootstrap(async_client) -> tuple[dict, str]:
    email = f"lineage-{uuid.uuid4().hex[:8]}@example.com"
    password = "lineage-test-password-very-long"
    r = await async_client.post(
        "/api/v1/auth/register",
        json={"email": email, "name": "Lineage Tester", "password": password},
    )
    assert r.status_code == 201, r.text
    r = await async_client.post("/api/v1/auth/login", json={"email": email, "password": password})
    token = r.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    r = await async_client.post(
        "/api/v1/workspaces",
        headers=headers,
        json={"name": "Lineage WS", "slug": f"lineage-{uuid.uuid4().hex[:6]}"},
    )
    assert r.status_code in (200, 201), r.text
    ws_id = r.json()["id"]
    headers["X-Workspace-Id"] = ws_id
    return headers, ws_id


async def _new_session(async_client, headers) -> str:
    r = await async_client.post("/api/v1/sessions", headers=headers, json={"kind": "p2p"})
    assert r.status_code in (200, 201), r.text
    return r.json()["id"]


async def _seed_compressed_summary(*, workspace_id: str, session_id: str) -> tuple[str, list[str]]:
    """Append 3 originals + 1 summary directly through the session
    service so we don't need a full agent run inside the test."""
    from app.db.session import get_session_factory
    from app.services import lineage_replay as lineage_svc
    from app.services import session as session_svc

    factory = get_session_factory()
    async with factory() as db:
        sess = await session_svc.get_session_or_404(
            db,
            uuid.UUID(session_id),
            workspace_id=uuid.UUID(workspace_id),
        )
        a = await session_svc.append_message(
            db,
            session_obj=sess,
            role=session_svc.MessageRole.USER,
            content_json={"text": "first"},
        )
        b = await session_svc.append_message(
            db,
            session_obj=sess,
            role=session_svc.MessageRole.ASSISTANT,
            content_json={"text": "first reply"},
        )
        c = await session_svc.append_message(
            db,
            session_obj=sess,
            role=session_svc.MessageRole.USER,
            content_json={"text": "second"},
        )
        summary = await session_svc.append_message(
            db,
            session_obj=sess,
            role=session_svc.MessageRole.SYSTEM,
            content_json={"text": "compacted"},
        )
        ref = lineage_svc.mark_message_as_compressed(summary, [a, b, c], strategy="sliding_window")
        summary.original_turns_ref = ref
        for original in (a, b, c):
            original.compressed_into_summary_id = summary.id
        await db.flush()
        await db.commit()
        return str(summary.id), [str(a.id), str(b.id), str(c.id)]


# ─── Happy paths ─────────────────────────────────────────────
async def test_get_lineage_replay_happy_path(async_client):
    headers, ws_id = await _bootstrap(async_client)
    sid = await _new_session(async_client, headers)
    summary_id, originals = await _seed_compressed_summary(workspace_id=ws_id, session_id=sid)

    r = await async_client.get(
        f"/api/v1/sessions/{sid}/messages/{summary_id}/lineage",
        headers=headers,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["summary_message_id"] == summary_id
    assert body["original_turn_count"] == 3
    assert body["compaction_strategy"] == "sliding_window"
    returned_ids = {n["message_id"] for n in body["original_turns"]}
    assert returned_ids == set(originals)
    for node in body["original_turns"]:
        assert len(node["text_excerpt"]) <= 200


async def test_list_lineage_summaries_happy_path(async_client):
    headers, ws_id = await _bootstrap(async_client)
    sid = await _new_session(async_client, headers)
    summary_id, _ = await _seed_compressed_summary(workspace_id=ws_id, session_id=sid)

    r = await async_client.get(f"/api/v1/sessions/{sid}/lineage-summaries", headers=headers)
    assert r.status_code == 200, r.text
    rows = r.json()
    assert len(rows) == 1
    assert rows[0]["summary_message_id"] == summary_id
    assert rows[0]["turn_count"] == 3


# ─── Not-a-summary returns 404 ───────────────────────────────
async def test_get_lineage_replay_for_plain_message_404(async_client):
    headers, ws_id = await _bootstrap(async_client)
    sid = await _new_session(async_client, headers)

    from app.db.session import get_session_factory
    from app.services import session as session_svc

    factory = get_session_factory()
    async with factory() as db:
        sess = await session_svc.get_session_or_404(
            db, uuid.UUID(sid), workspace_id=uuid.UUID(ws_id)
        )
        msg = await session_svc.append_message(
            db,
            session_obj=sess,
            role=session_svc.MessageRole.USER,
            content_json={"text": "plain"},
        )
        await db.commit()
        plain_id = str(msg.id)

    r = await async_client.get(
        f"/api/v1/sessions/{sid}/messages/{plain_id}/lineage",
        headers=headers,
    )
    assert r.status_code == 404
    body = r.json()
    assert body.get("code") == "lineage.not_compressed"


# ─── Cross-workspace isolation ───────────────────────────────
async def test_get_lineage_replay_other_workspace_404(async_client):
    headers_a, ws_a = await _bootstrap(async_client)
    sid_a = await _new_session(async_client, headers_a)
    summary_id, _ = await _seed_compressed_summary(workspace_id=ws_a, session_id=sid_a)

    headers_b, _ = await _bootstrap(async_client)
    r = await async_client.get(
        f"/api/v1/sessions/{sid_a}/messages/{summary_id}/lineage",
        headers=headers_b,
    )
    assert r.status_code in (403, 404)


async def test_list_lineage_summaries_other_workspace_404(async_client):
    headers_a, ws_a = await _bootstrap(async_client)
    sid_a = await _new_session(async_client, headers_a)
    await _seed_compressed_summary(workspace_id=ws_a, session_id=sid_a)

    headers_b, _ = await _bootstrap(async_client)
    r = await async_client.get(f"/api/v1/sessions/{sid_a}/lineage-summaries", headers=headers_b)
    assert r.status_code in (403, 404)


async def test_unauthenticated_lineage_read_blocked(async_client):
    sid = uuid.uuid4()
    mid = uuid.uuid4()
    r = await async_client.get(f"/api/v1/sessions/{sid}/messages/{mid}/lineage")
    assert r.status_code in (401, 403)
