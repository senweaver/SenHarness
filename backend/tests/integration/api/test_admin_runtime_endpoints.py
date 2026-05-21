"""Integration: ``/api/v1/admin/runtime/*`` (M4.1 Runtime Console).

Exercises the three console routes end-to-end with a real Postgres +
Redis pair:

* ``GET  /admin/runtime/inflight-runs`` — happy + 403 + state filter.
* ``GET  /admin/runtime/stats`` — counter shape + 403.
* ``POST /admin/runtime/inflight-runs/{run_id}/force-recycle`` —
  cancels the kernel task + flips state + 404 for unknown run.
* Rate-limit floor on ``runtime_console_recycle`` (5 / 60s).

The kernel's ``cancel`` is not exercised by the real native backend;
each test installs a stub via ``monkeypatch`` so the endpoint can
verify the dispatch contract without spinning a streaming run.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from app.core.security import create_access_token
from app.db.models.audit import AuditEvent
from app.db.models.identity import PlatformRole
from app.db.models.inflight_run import InflightRun, InflightRunState
from app.db.models.membership import Membership, MembershipStatus
from app.db.models.role import BuiltinRole
from app.db.session import get_session_factory
from app.repositories.inflight_run import InflightRunRepository

pytestmark = pytest.mark.asyncio


def _bearer(
    identity_id: uuid.UUID,
    *,
    workspace_id: uuid.UUID | None = None,
) -> dict[str, str]:
    token, _, _ = create_access_token(
        identity_id=str(identity_id),
        workspace_id=str(workspace_id) if workspace_id is not None else None,
        roles=[],
    )
    headers = {"Authorization": f"Bearer {token}"}
    if workspace_id is not None:
        headers["X-Workspace-Id"] = str(workspace_id)
    return headers


async def _seed_inflight(
    *,
    workspace_id: uuid.UUID,
    identity_id: uuid.UUID,
    state: InflightRunState = InflightRunState.RUNNING,
    error_kind: str | None = None,
) -> tuple[uuid.UUID, uuid.UUID]:
    """Insert a session + spine row directly. Returns ``(session_id, run_id)``."""
    factory = get_session_factory()
    async with factory() as db:
        from app.repositories.session import SessionRepository

        sess = await SessionRepository(db).create(
            workspace_id=workspace_id,
            owner_identity_id=identity_id,
            title=f"runtime-{uuid.uuid4().hex[:6]}",
        )
        run_id = uuid.uuid4()
        row = InflightRun(
            workspace_id=workspace_id,
            run_id=run_id,
            session_id=sess.id,
            identity_id=identity_id,
            backend_kind="native",
            request_snapshot={"trigger": "runtime-console-test"},
            state=state,
            error_kind=error_kind,
        )
        db.add(row)
        await db.flush([row])
        await db.commit()
    return sess.id, run_id


async def _new_member(
    async_client, *, role: str, workspace_id: uuid.UUID
) -> uuid.UUID:
    factory = get_session_factory()
    from app.services import auth as auth_svc

    email = f"rt-{uuid.uuid4().hex[:8]}@example.com"
    async with factory() as db:
        result = await auth_svc.register(
            db,
            email=email,
            name="Runtime Tester",
            password="runtime-console-test-password",
            create_personal_workspace=False,
        )
        identity_id = result.identity.id
        db.add(
            Membership(
                workspace_id=workspace_id,
                identity_id=identity_id,
                role=role,
                status=MembershipStatus.ACTIVE,
            )
        )
        await db.commit()
    return identity_id


async def _make_workspace(async_client) -> tuple[uuid.UUID, uuid.UUID]:
    """Returns ``(workspace_id, owner_identity_id)``."""
    email = f"runtime-{uuid.uuid4().hex[:8]}@example.com"
    r = await async_client.post(
        "/api/v1/auth/register",
        json={
            "email": email,
            "name": "Runtime Owner",
            "password": "runtime-console-test-password",
        },
    )
    assert r.status_code == 201, r.text
    body = r.json()
    return uuid.UUID(body["workspace"]["id"]), uuid.UUID(body["identity"]["id"])


# ─── List ────────────────────────────────────────────────────
async def test_list_inflight_runs_happy_path(async_client):
    ws_id, owner_id = await _make_workspace(async_client)
    _, run_id = await _seed_inflight(
        workspace_id=ws_id, identity_id=owner_id
    )

    resp = await async_client.get(
        "/api/v1/admin/runtime/inflight-runs",
        headers=_bearer(owner_id, workspace_id=ws_id),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total"] >= 1
    assert any(row["run_id"] == str(run_id) for row in body["rows"])
    one = next(row for row in body["rows"] if row["run_id"] == str(run_id))
    assert one["state_bucket"] == "running"
    assert one["backend_kind"] == "native"


async def test_list_inflight_runs_filters_by_state(async_client):
    ws_id, owner_id = await _make_workspace(async_client)
    _, running_id = await _seed_inflight(
        workspace_id=ws_id, identity_id=owner_id
    )
    _, zombie_id = await _seed_inflight(
        workspace_id=ws_id,
        identity_id=owner_id,
        state=InflightRunState.LOST,
        error_kind="heartbeat_timeout",
    )

    resp = await async_client.get(
        "/api/v1/admin/runtime/inflight-runs?state=zombie",
        headers=_bearer(owner_id, workspace_id=ws_id),
    )
    assert resp.status_code == 200
    rows = resp.json()["rows"]
    run_ids = {row["run_id"] for row in rows}
    assert str(zombie_id) in run_ids
    assert str(running_id) not in run_ids
    assert all(row["state_bucket"] == "zombie" for row in rows)


async def test_list_inflight_runs_member_role_returns_403(async_client):
    ws_id, _owner_id = await _make_workspace(async_client)
    member_id = await _new_member(
        async_client, role=BuiltinRole.MEMBER.value, workspace_id=ws_id
    )

    resp = await async_client.get(
        "/api/v1/admin/runtime/inflight-runs",
        headers=_bearer(member_id, workspace_id=ws_id),
    )
    assert resp.status_code == 403, resp.text


async def test_list_inflight_runs_platform_admin_passes(async_client):
    ws_id, _owner_id = await _make_workspace(async_client)
    factory = get_session_factory()
    from app.services import auth as auth_svc

    async with factory() as db:
        admin = await auth_svc.register(
            db,
            email=f"pa-{uuid.uuid4().hex[:8]}@example.com",
            name="Platform Admin",
            password="runtime-console-test-password",
            create_personal_workspace=False,
        )
        admin.identity.platform_role = PlatformRole.PLATFORM_ADMIN
        await db.commit()
        admin_id = admin.identity.id

    resp = await async_client.get(
        "/api/v1/admin/runtime/inflight-runs",
        headers=_bearer(admin_id, workspace_id=ws_id),
    )
    assert resp.status_code == 200, resp.text


# ─── Stats ───────────────────────────────────────────────────
async def test_stats_endpoint_returns_counter_shape(async_client):
    ws_id, owner_id = await _make_workspace(async_client)
    await _seed_inflight(workspace_id=ws_id, identity_id=owner_id)
    await _seed_inflight(
        workspace_id=ws_id,
        identity_id=owner_id,
        state=InflightRunState.PAUSED,
    )

    resp = await async_client.get(
        "/api/v1/admin/runtime/stats",
        headers=_bearer(owner_id, workspace_id=ws_id),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    for key in ("running", "paused", "lost", "zombie", "killed", "total_active"):
        assert key in body
    assert body["running"] >= 1
    assert body["paused"] >= 1
    assert body["total_active"] == body["running"] + body["paused"]


# ─── Force recycle ───────────────────────────────────────────
async def test_force_recycle_happy_path(async_client, monkeypatch):
    ws_id, owner_id = await _make_workspace(async_client)
    _, run_id = await _seed_inflight(
        workspace_id=ws_id, identity_id=owner_id
    )

    cancelled: list[str] = []

    class _StubBackend:
        async def cancel(self, target: uuid.UUID) -> None:
            cancelled.append(str(target))

    monkeypatch.setattr(
        "app.agents.kernels.registry.get_backend",
        lambda kind: _StubBackend(),
    )

    resp = await async_client.post(
        f"/api/v1/admin/runtime/inflight-runs/{run_id}/force-recycle",
        headers=_bearer(owner_id, workspace_id=ws_id),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["state"] == "cancelled"
    assert body["previous_state"] == "running"
    assert body["cancel_dispatched"] is True
    assert cancelled == [str(run_id)]

    factory = get_session_factory()
    async with factory() as db:
        repo = InflightRunRepository(db)
        row = await repo.get_by_run_id(run_id=run_id)
        assert row is not None
        assert row.state == InflightRunState.CANCELLED
        assert row.error_kind == "admin_force_recycle"

        audit = (
            await db.execute(
                select(AuditEvent).where(
                    AuditEvent.action == "inflight_run.force_recycled",
                    AuditEvent.workspace_id == ws_id,
                )
            )
        ).scalars().all()
        assert len(audit) >= 1


async def test_force_recycle_unknown_run_returns_404(async_client):
    ws_id, owner_id = await _make_workspace(async_client)
    bogus = uuid.uuid4()
    resp = await async_client.post(
        f"/api/v1/admin/runtime/inflight-runs/{bogus}/force-recycle",
        headers=_bearer(owner_id, workspace_id=ws_id),
    )
    assert resp.status_code == 404, resp.text


async def test_force_recycle_member_role_returns_403(async_client):
    ws_id, owner_id = await _make_workspace(async_client)
    member_id = await _new_member(
        async_client, role=BuiltinRole.MEMBER.value, workspace_id=ws_id
    )
    _, run_id = await _seed_inflight(
        workspace_id=ws_id, identity_id=owner_id
    )

    resp = await async_client.post(
        f"/api/v1/admin/runtime/inflight-runs/{run_id}/force-recycle",
        headers=_bearer(member_id, workspace_id=ws_id),
    )
    assert resp.status_code == 403


async def test_force_recycle_rate_limited(async_client, monkeypatch):
    """5 / 60s — extra calls within the window eventually return 429.

    The fixed-window bucket is keyed on the test client's IP, which
    means earlier sibling tests in this module may have consumed
    slots; the assertion is therefore "at least one 429 and at least
    one 200" rather than a positional check.
    """
    ws_id, owner_id = await _make_workspace(async_client)

    class _StubBackend:
        async def cancel(self, target: uuid.UUID) -> None:
            return None

    monkeypatch.setattr(
        "app.agents.kernels.registry.get_backend",
        lambda kind: _StubBackend(),
    )

    statuses: list[int] = []
    for _ in range(8):
        _, run_id = await _seed_inflight(
            workspace_id=ws_id, identity_id=owner_id
        )
        resp = await async_client.post(
            f"/api/v1/admin/runtime/inflight-runs/{run_id}/force-recycle",
            headers=_bearer(owner_id, workspace_id=ws_id),
        )
        statuses.append(resp.status_code)

    # Expect both the success path and the rate-limit floor to fire.
    assert any(s == 429 for s in statuses), statuses
    assert any(s == 200 for s in statuses), statuses
