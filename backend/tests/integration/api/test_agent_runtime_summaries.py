"""Integration: ``GET /api/v1/agent-runtime/summaries``.

Verifies:

* Happy path: counters surface per-workspace for every membership the
  caller has, with running counts matching the seeded inflight rows.
* Cross-workspace isolation: a workspace the caller does NOT belong to
  is invisible.
* Auth: an unauthenticated request returns 401.
"""

from __future__ import annotations

import uuid

import pytest

from app.core.security import create_access_token
from app.db.models.inflight_run import InflightRun, InflightRunState
from app.db.models.membership import Membership, MembershipStatus
from app.db.models.role import BuiltinRole
from app.db.session import get_session_factory

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
    return {"Authorization": f"Bearer {token}"}


async def _seed_inflight(
    *,
    workspace_id: uuid.UUID,
    identity_id: uuid.UUID,
    state: InflightRunState = InflightRunState.RUNNING,
) -> uuid.UUID:
    factory = get_session_factory()
    async with factory() as db:
        from app.repositories.session import SessionRepository

        sess = await SessionRepository(db).create(
            workspace_id=workspace_id,
            owner_identity_id=identity_id,
            title=f"summary-{uuid.uuid4().hex[:6]}",
        )
        run_id = uuid.uuid4()
        row = InflightRun(
            workspace_id=workspace_id,
            run_id=run_id,
            session_id=sess.id,
            identity_id=identity_id,
            backend_kind="native",
            request_snapshot={"trigger": "summaries-test"},
            state=state,
        )
        db.add(row)
        await db.flush([row])
        await db.commit()
    return run_id


async def _make_workspace(async_client) -> tuple[uuid.UUID, uuid.UUID]:
    email = f"summaries-{uuid.uuid4().hex[:8]}@example.com"
    r = await async_client.post(
        "/api/v1/auth/register",
        json={
            "email": email,
            "name": "Summaries Owner",
            "password": "summaries-test-password",
        },
    )
    assert r.status_code == 201, r.text
    body = r.json()
    return uuid.UUID(body["workspace"]["id"]), uuid.UUID(body["identity"]["id"])


async def _attach_membership(
    *, workspace_id: uuid.UUID, identity_id: uuid.UUID, role: str
) -> None:
    factory = get_session_factory()
    async with factory() as db:
        db.add(
            Membership(
                workspace_id=workspace_id,
                identity_id=identity_id,
                role=role,
                status=MembershipStatus.ACTIVE,
            )
        )
        await db.commit()


async def test_returns_summaries_for_caller_memberships(async_client):
    ws_a, owner_id = await _make_workspace(async_client)
    ws_b, other_owner_id = await _make_workspace(async_client)

    # Owner of ws_a also joins ws_b so the response should include both.
    await _attach_membership(
        workspace_id=ws_b,
        identity_id=owner_id,
        role=BuiltinRole.MEMBER.value,
    )

    await _seed_inflight(workspace_id=ws_a, identity_id=owner_id)
    await _seed_inflight(workspace_id=ws_a, identity_id=owner_id)
    await _seed_inflight(workspace_id=ws_b, identity_id=other_owner_id)

    resp = await async_client.get(
        "/api/v1/agent-runtime/summaries",
        headers=_bearer(owner_id),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "summaries" in body
    assert "timestamp" in body
    by_ws = {s["workspace_id"]: s for s in body["summaries"]}
    assert str(ws_a) in by_ws
    assert str(ws_b) in by_ws
    assert by_ws[str(ws_a)]["running"] == 2
    assert by_ws[str(ws_b)]["running"] == 1


async def test_hides_workspaces_without_membership(async_client):
    ws_mine, my_id = await _make_workspace(async_client)
    ws_theirs, their_id = await _make_workspace(async_client)

    await _seed_inflight(workspace_id=ws_theirs, identity_id=their_id)

    resp = await async_client.get(
        "/api/v1/agent-runtime/summaries",
        headers=_bearer(my_id),
    )
    assert resp.status_code == 200
    ws_ids = {s["workspace_id"] for s in resp.json()["summaries"]}
    assert str(ws_mine) in ws_ids
    assert str(ws_theirs) not in ws_ids


async def test_unauthenticated_returns_401(async_client):
    resp = await async_client.get("/api/v1/agent-runtime/summaries")
    assert resp.status_code == 401
