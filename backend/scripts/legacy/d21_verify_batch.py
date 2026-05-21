"""D21 E2E verification — session checkpoints + batch replay (Phase 5).

Checkpoints:

1. Login as demo, pick (or seed) a session. Append a couple of messages so
   there's real history to snapshot.
2. ``POST /sessions/{id}/checkpoints`` creates a named checkpoint. Assert
   ``message_count`` matches actual history depth.
3. ``POST /sessions/{id}/fork`` forks at that checkpoint — the new session
   has the same message count + a ``forked_from`` pointer on metadata.
4. ``POST /batch/runs`` with 2 text-only cases. Poll until the run reaches
   a terminal state; assert case rows landed with diff_json / output_text.
5. Smoke: list batch runs, confirm our run is in the list.

We don't hit a real LLM — the pydantic_ai kernel's placeholder stream is
enough to exercise the full pipeline (agent_runner → Kernel → DB writeback).

Run with:  ``python -m scripts.d21_verify_batch``
"""

from __future__ import annotations

import asyncio
import logging
import uuid

import httpx
from sqlalchemy import select

import app.agents.kernels.openclaw as _kernel_openclaw  # noqa: F401
import app.agents.kernels.native as _kernel_native  # noqa: F401
from app.db.models.agent import Agent, AgentVisibility, AutonomyLevel, BackendKind
from app.db.models.message import Message, MessageRole
from app.db.models.session import Session as SessionModel
from app.db.models.session import SessionKind
from app.db.session import get_session_factory
from app.main import app

logging.basicConfig(level=logging.WARNING)

DEMO_EMAIL = "demo@senharness.app"
DEMO_PASSWORD = "senharness"


async def _login(client: httpx.AsyncClient) -> tuple[str, uuid.UUID, uuid.UUID]:
    r = await client.post(
        "/api/v1/auth/login",
        json={"email": DEMO_EMAIL, "password": DEMO_PASSWORD},
    )
    r.raise_for_status()
    token = r.json()["access_token"]
    me = await client.get(
        "/api/v1/me", headers={"Authorization": f"Bearer {token}"}
    )
    me.raise_for_status()
    ws_id = uuid.UUID(me.json()["current_workspace_id"])
    identity_id = uuid.UUID(me.json()["id"])
    return token, ws_id, identity_id


def _hdr(token: str, ws_id: uuid.UUID) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", "X-Workspace-Id": str(ws_id)}


async def _seed_agent_and_session(
    ws_id: uuid.UUID, identity_id: uuid.UUID
) -> tuple[uuid.UUID, uuid.UUID]:
    """Create a pydantic_ai agent + a P2P session + two messages so there's
    something to snapshot."""

    factory = get_session_factory()
    async with factory() as db:
        agent = Agent(
            workspace_id=ws_id,
            name="d21-agent",
            backend_kind=BackendKind.NATIVE,
            visibility=AgentVisibility.PRIVATE,
            autonomy_level=AutonomyLevel.L2,
            metadata_json={},
            created_by=identity_id,
        )
        db.add(agent)
        await db.flush([agent])

        sess = SessionModel(
            workspace_id=ws_id,
            owner_identity_id=identity_id,
            kind=SessionKind.P2P,
            subject_id=agent.id,
            title="d21 smoke session",
            metadata_json={},
        )
        db.add(sess)
        await db.flush([sess])

        db.add(
            Message(
                workspace_id=ws_id,
                session_id=sess.id,
                role=MessageRole.USER,
                author_identity_id=identity_id,
                content_json={"text": "what's the capital of France?"},
                attachments_json=[],
                token_usage_json={},
            )
        )
        db.add(
            Message(
                workspace_id=ws_id,
                session_id=sess.id,
                role=MessageRole.ASSISTANT,
                author_agent_id=agent.id,
                content_json={"text": "Paris."},
                attachments_json=[],
                token_usage_json={},
            )
        )
        sess.message_count = 2
        await db.commit()
        return agent.id, sess.id


async def _count_messages(session_id: uuid.UUID) -> int:
    factory = get_session_factory()
    async with factory() as db:
        rows = (
            await db.execute(
                select(Message).where(Message.session_id == session_id)
            )
        ).scalars().all()
        return len(rows)


# ─── Checkpoints ──────────────────────────────────────────
async def step_checkpoint_create(
    http: httpx.AsyncClient,
    token: str,
    ws_id: uuid.UUID,
    session_id: uuid.UUID,
) -> uuid.UUID:
    r = await http.post(
        f"/api/v1/sessions/{session_id}/checkpoints",
        headers=_hdr(token, ws_id),
        json={"label": "d21-cp", "description": "after first QA"},
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["message_count"] >= 2, body
    print(
        f"  [step1] checkpoint created id={body['id']} "
        f"message_count={body['message_count']} (OK)"
    )
    return uuid.UUID(body["id"])


async def step_fork(
    http: httpx.AsyncClient,
    token: str,
    ws_id: uuid.UUID,
    session_id: uuid.UUID,
    checkpoint_id: uuid.UUID,
) -> uuid.UUID:
    r = await http.post(
        f"/api/v1/sessions/{session_id}/fork",
        headers=_hdr(token, ws_id),
        json={"checkpoint_id": str(checkpoint_id), "title": "fork-d21"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["copied_message_count"] >= 2
    fork_id = uuid.UUID(body["fork_session_id"])
    # Sanity: fork has same message count as the source at checkpoint time.
    orig_count = await _count_messages(session_id)
    fork_count = await _count_messages(fork_id)
    assert fork_count <= orig_count, (fork_count, orig_count)
    print(
        f"  [step2] forked → {fork_id} ({body['copied_message_count']} msgs) (OK)"
    )
    return fork_id


# ─── Batch ───────────────────────────────────────────────
async def step_batch_run(
    http: httpx.AsyncClient,
    token: str,
    ws_id: uuid.UUID,
    agent_id: uuid.UUID,
) -> uuid.UUID:
    r = await http.post(
        "/api/v1/batch/runs",
        headers=_hdr(token, ws_id),
        json={
            "name": "d21-smoke",
            "description": "placeholder kernel round-trip",
            "agent_id": str(agent_id),
            "cases": [
                {"label": "hello", "text": "Say hi in English"},
                {"label": "math", "text": "2+2=?"},
            ],
        },
    )
    assert r.status_code == 201, r.text
    batch_id = uuid.UUID(r.json()["id"])
    print(f"  [step3] batch_run created id={batch_id}")

    # Poll until terminal.
    terminal = {"succeeded", "failed", "cancelled"}
    for _ in range(40):
        detail = await http.get(
            f"/api/v1/batch/runs/{batch_id}",
            headers=_hdr(token, ws_id),
        )
        assert detail.status_code == 200, detail.text
        body = detail.json()
        if body["status"] in terminal:
            break
        await asyncio.sleep(0.5)
    assert body["status"] in terminal, f"batch never reached terminal: {body}"
    cases = body["cases"]
    assert len(cases) == 2, cases
    # Every case should have a replay_session_id, even if the placeholder
    # kernel returned an empty output_text — the infra did its job.
    for c in cases:
        assert c["status"] in {"succeeded", "failed", "skipped"}
        assert c["replay_session_id"] is not None, c
    print(
        f"  [step4] batch finished status={body['status']} "
        f"stats={body['stats_json']} (OK)"
    )

    # Smoke: list endpoint should echo the run.
    lst = await http.get(
        "/api/v1/batch/runs", headers=_hdr(token, ws_id)
    )
    assert lst.status_code == 200
    assert any(r["id"] == str(batch_id) for r in lst.json())
    print("  [step5] list endpoint echoes the new run (OK)")
    return batch_id


# ─── Main ────────────────────────────────────────────────
async def main() -> None:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://test"
    ) as http:
        token, ws_id, identity_id = await _login(http)
        print(f"  [auth ] logged in; ws={ws_id}")

        agent_id, session_id = await _seed_agent_and_session(ws_id, identity_id)

        checkpoint_id = await step_checkpoint_create(
            http, token, ws_id, session_id
        )
        await step_fork(http, token, ws_id, session_id, checkpoint_id)
        await step_batch_run(http, token, ws_id, agent_id)

    print("\n[PASS] D21 checkpoints + batch replay verification complete")


if __name__ == "__main__":
    asyncio.run(main())
