"""D18 E2E verification — OpenClaw adapter (Phase 5 · Agent OS).

Covers the round-trip:

1. Login as demo; ``POST /api/v1/backends`` creates an adapter, returns a
   one-time ``api_key``. ``GET /api/v1/backends`` lists it back.
2. A fake-worker coroutine registers with ``POST /gw/openclaw/register``,
   long-polls via ``POST /gw/openclaw/poll``, and emits ``delta`` + ``final``
   events for every ``run`` message it receives.
3. Bind an Agent with ``backend_kind="openclaw"`` + ``backend_adapter_id``
   to the adapter. Run one turn using ``services.agent_runner`` (no HTTP
   WebSocket needed); assert we get back the fake worker's final text.
4. Assert gateway_messages state machine: request → delivered → acked, plus
   two event rows (delta, final).
5. Cancel path: fire another turn, cancel half-way before the worker emits
   final. Assert a ``kind=cancel`` request row landed so the worker can
   abort cleanly.
6. ``POST /backends/{id}/rotate-key`` invalidates the old key and the new
   one authenticates successfully.
7. ``DELETE /backends/{id}`` flags the adapter unreachable; subsequent
   runs fail with ``openclaw.adapter_not_found``.

Run with:  ``python -m scripts.d18_verify_openclaw``
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import uuid

import httpx
from sqlalchemy import select

# Force both backend kernels to register (lifespan doesn't fire under ASGI transport).
import app.agents.kernels.openclaw as _kernel_openclaw  # noqa: F401
import app.agents.kernels.native as _kernel_native  # noqa: F401
from app.db.models.agent import Agent, AgentVisibility, AutonomyLevel, BackendKind
from app.db.models.gateway_message import (
    GatewayMessage,
    GatewayMessageDirection,
    GatewayMessageStatus,
)
from app.db.models.session import Session as SessionModel
from app.db.models.session import SessionKind
from app.db.session import get_session_factory
from app.main import app
from app.services import agent_runner as runner

logging.basicConfig(level=logging.WARNING)

DEMO_EMAIL = "demo@senharness.app"
DEMO_PASSWORD = "senharness"


# ─── Login helper ─────────────────────────────────────────
async def _login(client: httpx.AsyncClient) -> tuple[str, uuid.UUID, uuid.UUID]:
    r = await client.post(
        "/api/v1/auth/login",
        json={"email": DEMO_EMAIL, "password": DEMO_PASSWORD},
    )
    r.raise_for_status()
    token = r.json()["access_token"]
    me = await client.get(
        "/api/v1/me",
        headers={"Authorization": f"Bearer {token}"},
    )
    me.raise_for_status()
    ws_id = uuid.UUID(me.json()["current_workspace_id"])
    identity_id = uuid.UUID(me.json()["id"])
    return token, ws_id, identity_id


def _auth_headers(token: str, ws_id: uuid.UUID) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "X-Workspace-Id": str(ws_id),
    }


# ─── Fake worker ──────────────────────────────────────────
class FakeWorker:
    """Tiny in-process simulation of an OpenClaw-compatible remote worker.

    Polls the gateway, honours cancel events, and emits a two-part stream
    (`delta` + `final`) for every real ``run`` request.
    """

    def __init__(self, client: httpx.AsyncClient, api_key: str) -> None:
        self.client = client
        self.api_key = api_key
        self.final_text = "hello from fake openclaw worker"
        self.seen_cancel_for: set[uuid.UUID] = set()
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()

    def _hdr(self) -> dict[str, str]:
        return {"X-Api-Key": self.api_key}

    async def register(self) -> None:
        r = await self.client.post(
            "/api/v1/gw/openclaw/register",
            headers=self._hdr(),
            json={
                "worker_version": "d18-fake-0.1",
                "capabilities": {"streaming": True, "parallel_tools": False},
            },
        )
        r.raise_for_status()

    async def _emit(self, run_id: str, seq: int, kind: str, data: dict) -> None:
        await self.client.post(
            "/api/v1/gw/openclaw/emit",
            headers=self._hdr(),
            json={"run_id": run_id, "seq": seq, "kind": kind, "data": data},
        )

    async def start(self) -> None:
        await self.register()
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task

    async def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                r = await self.client.post(
                    "/api/v1/gw/openclaw/poll",
                    headers=self._hdr(),
                    json={"max_messages": 4, "wait_ms": 1000},
                )
                if r.status_code != 200:
                    await asyncio.sleep(0.2)
                    continue
                messages = r.json().get("messages", [])
            except httpx.RequestError:
                await asyncio.sleep(0.2)
                continue

            for m in messages:
                run_id = m["run_id"]
                if m["kind"] == "cancel":
                    self.seen_cancel_for.add(uuid.UUID(run_id))
                    # Acknowledge cancellation as a final error.
                    await self._emit(
                        run_id, 0, "error",
                        {"code": "cancelled", "message": "worker honoured cancel"},
                    )
                    continue
                if m["kind"] != "run":
                    continue
                # Two-part stream. seq must be strictly increasing for a run.
                await self._emit(run_id, 0, "delta", {"text": self.final_text})
                await asyncio.sleep(0.05)
                await self._emit(
                    run_id, 1, "final",
                    {"text": self.final_text, "stop_reason": "end_turn"},
                )


# ─── DB helpers ──────────────────────────────────────────
async def _count_rows(*, run_id: uuid.UUID) -> dict[str, int]:
    factory = get_session_factory()
    async with factory() as db:
        rows = (
            await db.execute(
                select(GatewayMessage).where(GatewayMessage.run_id == run_id)
            )
        ).scalars().all()
        out = {
            "request": 0,
            "event": 0,
            "event_kinds": 0,
            "request_acked": 0,
        }
        kinds = set()
        for r in rows:
            if r.direction == GatewayMessageDirection.REQUEST:
                out["request"] += 1
                if r.status == GatewayMessageStatus.ACKED:
                    out["request_acked"] += 1
            elif r.direction == GatewayMessageDirection.EVENT:
                out["event"] += 1
                kinds.add(r.kind)
        out["event_kinds"] = len(kinds)
        return out


async def _create_session(ws_id: uuid.UUID, identity_id: uuid.UUID, agent_id: uuid.UUID) -> uuid.UUID:
    factory = get_session_factory()
    async with factory() as db:
        sess = SessionModel(
            workspace_id=ws_id,
            kind=SessionKind.P2P,
            owner_identity_id=identity_id,
            subject_id=agent_id,
            title="d18 smoke",
            metadata_json={},
        )
        db.add(sess)
        await db.commit()
        await db.refresh(sess)
        return sess.id


async def _create_openclaw_agent(
    ws_id: uuid.UUID,
    identity_id: uuid.UUID,
    adapter_id: uuid.UUID,
) -> uuid.UUID:
    factory = get_session_factory()
    async with factory() as db:
        agent = Agent(
            workspace_id=ws_id,
            name="d18-openclaw-agent",
            description="d18 verify",
            backend_kind=BackendKind.OPENCLAW,
            backend_adapter_id=adapter_id,
            visibility=AgentVisibility.PRIVATE,
            autonomy_level=AutonomyLevel.L2,
            metadata_json={},
            created_by=identity_id,
        )
        db.add(agent)
        await db.commit()
        await db.refresh(agent)
        return agent.id


# ─── Checkpoint functions ────────────────────────────────
async def step_create_adapter(
    client: httpx.AsyncClient, token: str, ws_id: uuid.UUID
) -> tuple[uuid.UUID, str]:
    r = await client.post(
        "/api/v1/backends",
        headers=_auth_headers(token, ws_id),
        json={"name": "d18-adapter", "kind": "openclaw", "endpoint": None},
    )
    assert r.status_code == 201, r.text
    body = r.json()
    adapter_id = uuid.UUID(body["adapter"]["id"])
    api_key = body["api_key"]
    assert len(api_key) >= 24

    # List echoes back.
    lst = await client.get("/api/v1/backends", headers=_auth_headers(token, ws_id))
    assert lst.status_code == 200
    ids = [a["id"] for a in lst.json()]
    assert str(adapter_id) in ids
    print(f"  [step1] adapter created id={adapter_id}  (OK)")
    return adapter_id, api_key


async def step_run_turn(
    ws_id: uuid.UUID,
    identity_id: uuid.UUID,
    agent_id: uuid.UUID,
    session_id: uuid.UUID,
    expected_text: str,
) -> uuid.UUID:
    factory = get_session_factory()
    async with factory() as db:
        result = await runner.run_agent_one_shot(
            db,
            workspace_id=ws_id,
            agent_id=agent_id,
            session_id=session_id,
            identity_id=identity_id,
            user_text="ping",
            iteration_budget=4,
        )
        await db.commit()
    assert result.error is None, f"run failed: {result.error}"
    assert expected_text in result.final_text, (
        f"expected worker reply, got {result.final_text!r}"
    )
    # Grab the most recent request row for state-machine assertions.
    async with factory() as db:
        rows = (
            await db.execute(
                select(GatewayMessage)
                .where(GatewayMessage.agent_id == agent_id)
                .where(GatewayMessage.direction == GatewayMessageDirection.REQUEST)
                .order_by(GatewayMessage.created_at.desc())
                .limit(1)
            )
        ).scalars().all()
        assert rows, "no request rows recorded"
        run_id = rows[0].run_id
    counts = await _count_rows(run_id=run_id)
    assert counts["request"] >= 1
    assert counts["request_acked"] >= 1, f"request not acked: {counts}"
    assert counts["event"] >= 2, f"expected ≥2 event rows, got {counts}"
    print(f"  [step2] run_id={run_id} counts={counts}  (OK)")
    return run_id


async def step_cancel_path(
    worker: FakeWorker,
    ws_id: uuid.UUID,
    adapter_id: uuid.UUID,
) -> None:
    """Directly exercise ``OpenClawBackend.cancel`` by enqueueing a request
    then cancelling before emit. We bypass the turn wrapper because it polls
    synchronously, which would wait for the worker's ``final``."""

    from app.agents.kernels.openclaw.adapter import OpenClawBackend
    from app.repositories.gateway import GatewayRepository

    backend = OpenClawBackend()
    run_id = uuid.uuid4()
    factory = get_session_factory()
    async with factory() as db:
        await GatewayRepository(db).enqueue_request(
            workspace_id=ws_id,
            adapter_id=adapter_id,
            run_id=run_id,
            session_id=None,
            agent_id=None,
            payload={"user_text": "this will be cancelled", "run_id": str(run_id)},
        )
        await db.commit()

    # Give the fake worker a moment to poll so the request is "in flight".
    await asyncio.sleep(0.3)
    await backend.cancel(run_id)

    # Verify a kind=cancel request row was added + pending ones flipped.
    async with factory() as db:
        rows = (
            await db.execute(
                select(GatewayMessage)
                .where(GatewayMessage.run_id == run_id)
                .where(GatewayMessage.direction == GatewayMessageDirection.REQUEST)
            )
        ).scalars().all()
        kinds = sorted({r.kind for r in rows})
        assert "cancel" in kinds, f"cancel row missing; saw {kinds}"
    # Give worker time to pick the cancel up + emit error.
    await asyncio.sleep(1.2)
    assert run_id in worker.seen_cancel_for, (
        "worker never saw the cancel event"
    )
    print(f"  [step3] cancel path OK (run_id={run_id})")


async def step_rotate_key(
    client: httpx.AsyncClient,
    token: str,
    ws_id: uuid.UUID,
    adapter_id: uuid.UUID,
    old_key: str,
) -> str:
    r = await client.post(
        f"/api/v1/backends/{adapter_id}/rotate-key",
        headers=_auth_headers(token, ws_id),
        json={},
    )
    assert r.status_code == 200, r.text
    new_key = r.json()["api_key"]
    assert new_key != old_key
    # Old key must now fail auth.
    bad = await client.post(
        "/api/v1/gw/openclaw/register",
        headers={"X-Api-Key": old_key},
        json={"capabilities": {}},
    )
    assert bad.status_code == 401, f"old key should be rejected, got {bad.status_code}"
    # New key must succeed.
    ok = await client.post(
        "/api/v1/gw/openclaw/register",
        headers={"X-Api-Key": new_key},
        json={"capabilities": {}},
    )
    assert ok.status_code == 200, ok.text
    print("  [step4] rotate-key OK (old rejected, new accepted)")
    return new_key


async def step_delete(
    client: httpx.AsyncClient,
    token: str,
    ws_id: uuid.UUID,
    adapter_id: uuid.UUID,
    ws_identity_id: uuid.UUID,
) -> None:
    r = await client.delete(
        f"/api/v1/backends/{adapter_id}",
        headers=_auth_headers(token, ws_id),
    )
    assert r.status_code == 204, r.text
    # Create a new agent wired to the deleted adapter + run — should error out
    # with openclaw.adapter_not_found (not 500 / not hang).
    agent_id = await _create_openclaw_agent(ws_id, ws_identity_id, adapter_id)
    session_id = await _create_session(ws_id, ws_identity_id, agent_id)

    factory = get_session_factory()
    async with factory() as db:
        result = await runner.run_agent_one_shot(
            db,
            workspace_id=ws_id,
            agent_id=agent_id,
            session_id=session_id,
            identity_id=ws_identity_id,
            user_text="ping deleted",
            iteration_budget=2,
        )
        await db.commit()
    assert result.error is not None, (
        f"run should fail after adapter delete, got {result.final_text!r}"
    )
    assert "not_found" in (result.error or "").lower() or "disabled" in (
        result.error or ""
    ).lower(), (
        f"expected adapter_not_found / disabled error, got {result.error!r}"
    )
    print(f"  [step5] adapter deleted; agent run rejected with {result.error!r}  (OK)")


# ─── Main ────────────────────────────────────────────────
async def main() -> None:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://test"
    ) as client:
        token, ws_id, identity_id = await _login(client)
        print(f"  [auth ] logged in; ws={ws_id}")

        adapter_id, api_key = await step_create_adapter(client, token, ws_id)

        worker = FakeWorker(client, api_key)
        await worker.start()
        try:
            agent_id = await _create_openclaw_agent(ws_id, identity_id, adapter_id)
            session_id = await _create_session(ws_id, identity_id, agent_id)

            await step_run_turn(
                ws_id=ws_id,
                identity_id=identity_id,
                agent_id=agent_id,
                session_id=session_id,
                expected_text=worker.final_text,
            )
            await step_cancel_path(worker, ws_id, adapter_id)

            api_key = await step_rotate_key(client, token, ws_id, adapter_id, api_key)
            # Worker keeps using the old key — rebind its in-memory key so
            # subsequent polls (none after this point) still work.
            worker.api_key = api_key

            await step_delete(client, token, ws_id, adapter_id, identity_id)
        finally:
            await worker.stop()

    print("\n[PASS] D18 OpenClaw adapter verification complete")


if __name__ == "__main__":
    asyncio.run(main())
