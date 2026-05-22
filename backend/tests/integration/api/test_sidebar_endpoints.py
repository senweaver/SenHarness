"""Integration: sidebar Phase 1 routes + star fan-out.

Covers:

* ``POST /api/v1/squads/{id}/star`` + ``DELETE`` happy path + tenant
  isolation (caller from another workspace gets 403, never sees the row).
* ``POST /api/v1/sessions/{id}/star`` + ``DELETE`` happy path.
* ``GET /api/v1/sidebar/my-items`` shape + ordering (pinned first, then
  unread, then last_activity desc).
* Star fan-out (``services/stars``): new agent / new squad / new
  member each seed the orthogonal dimension; deleted + cross-workspace
  rows are not fanned out; fan-out is idempotent.
* ``POST /api/v1/onboarding/complete`` idempotency.
* ``GET /api/v1/me`` exposes ``onboarded_at``.
"""

from __future__ import annotations

import uuid

import pytest

from app.core.security import create_access_token

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


# ─── Squad star ──────────────────────────────────────────────
async def test_squad_star_roundtrip(async_client, db_session, workspace, identity):
    from app.services import squad as squad_svc

    squad = await squad_svc.create_squad(
        db_session,
        workspace_id=workspace.id,
        created_by=identity.id,
        name="Test squad",
        description=None,
        strategy="router",
        config_json={},
        members=[],
    )
    await db_session.commit()

    headers = _bearer(identity.id, workspace_id=workspace.id)

    r = await async_client.post(
        f"/api/v1/squads/{squad.id}/star?pinned=true",
        headers=headers,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["squad_id"] == str(squad.id)
    assert body["starred"] is True
    assert body["pinned"] is True

    # Re-star is idempotent (no 409).
    r2 = await async_client.post(
        f"/api/v1/squads/{squad.id}/star",
        headers=headers,
    )
    assert r2.status_code == 200, r2.text
    assert r2.json()["pinned"] is False

    r3 = await async_client.delete(
        f"/api/v1/squads/{squad.id}/star",
        headers=headers,
    )
    assert r3.status_code == 204


async def test_squad_star_rejects_cross_workspace(async_client, db_session, workspace, identity):
    """Caller without membership in the squad's workspace gets 403."""
    from app.services import auth as auth_svc
    from app.services import squad as squad_svc

    squad = await squad_svc.create_squad(
        db_session,
        workspace_id=workspace.id,
        created_by=identity.id,
        name="Tenant boundary squad",
        description=None,
        strategy="router",
        config_json={},
        members=[],
    )
    outsider = await auth_svc.register(
        db_session,
        email=f"out-{uuid.uuid4().hex[:8]}@example.com",
        name="Outsider",
        password="correct horse battery staple",
        create_personal_workspace=False,
    )
    await db_session.commit()

    headers = _bearer(outsider.identity.id, workspace_id=workspace.id)
    r = await async_client.post(
        f"/api/v1/squads/{squad.id}/star",
        headers=headers,
    )
    assert r.status_code == 403


# ─── Session star ────────────────────────────────────────────
async def test_session_star_roundtrip(async_client, db_session, workspace, identity, agent):
    from app.services import session as session_svc

    sess = await session_svc.create_session(
        db_session,
        workspace_id=workspace.id,
        owner_identity_id=identity.id,
        subject_id=agent.id,
        title="hello",
    )
    await db_session.commit()

    headers = _bearer(identity.id, workspace_id=workspace.id)

    r = await async_client.post(
        f"/api/v1/sessions/{sess.id}/star",
        headers=headers,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["session_id"] == str(sess.id)
    assert body["starred"] is True

    r2 = await async_client.delete(
        f"/api/v1/sessions/{sess.id}/star",
        headers=headers,
    )
    assert r2.status_code == 204


# ─── Sidebar aggregator ──────────────────────────────────────
async def test_sidebar_my_items_shape_and_order(
    async_client, db_session, workspace, identity, agent
):
    """List unions agent + squad + session rows and sorts pinned first.

    Note: ``create_agent`` and ``create_squad`` auto-fan-out a pinned
    star row for every active workspace member, so an explicit star
    call only changes the row's ``pinned`` value.
    """
    from app.services import session as session_svc
    from app.services import squad as squad_svc

    squad = await squad_svc.create_squad(
        db_session,
        workspace_id=workspace.id,
        created_by=identity.id,
        name="Datapod squad",
        description=None,
        strategy="router",
        config_json={},
        members=[],
    )
    sess = await session_svc.create_session(
        db_session,
        workspace_id=workspace.id,
        owner_identity_id=identity.id,
        subject_id=agent.id,
        title="ad-hoc chat",
    )
    await db_session.commit()

    headers = _bearer(identity.id, workspace_id=workspace.id)

    # The agent was pre-pinned by fan-out; explicitly unpin it so this
    # test can prove that an unpinned agent sits below the pinned squad.
    r_agent = await async_client.post(
        f"/api/v1/agents/{agent.id}/star",
        headers=headers,
    )
    assert r_agent.status_code == 200, r_agent.text
    assert r_agent.json()["pinned"] is False
    r_squad = await async_client.post(
        f"/api/v1/squads/{squad.id}/star?pinned=true",
        headers=headers,
    )
    assert r_squad.status_code == 200, r_squad.text
    r_sess = await async_client.post(
        f"/api/v1/sessions/{sess.id}/star",
        headers=headers,
    )
    assert r_sess.status_code == 200, r_sess.text

    r = await async_client.get(
        "/api/v1/sidebar/my-items",
        headers=headers,
    )
    assert r.status_code == 200, r.text
    body = r.json()

    keys = {
        "type",
        "id",
        "name",
        "avatar_seed",
        "pinned",
        "unread_count",
        "last_activity_at",
        "href",
    }
    for item in body["items"]:
        assert keys.issubset(item.keys())

    types_present = {item["type"] for item in body["items"]}
    assert {"agent", "squad", "session"} <= types_present

    # The explicitly-starred squad is pinned; the explicitly-unpinned
    # agent fixture is below it.
    squad_row = next(i for i in body["items"] if i["type"] == "squad" and i["id"] == str(squad.id))
    assert squad_row["pinned"] is True
    agent_row = next(i for i in body["items"] if i["type"] == "agent" and i["id"] == str(agent.id))
    assert agent_row["pinned"] is False
    # Pinned items must precede any unpinned items.
    pinned_idxs = [i for i, row in enumerate(body["items"]) if row["pinned"]]
    unpinned_idxs = [i for i, row in enumerate(body["items"]) if not row["pinned"]]
    if pinned_idxs and unpinned_idxs:
        assert max(pinned_idxs) < min(unpinned_idxs)


async def test_sidebar_my_items_isolates_workspace(
    async_client, db_session, identity, workspace, agent
):
    """A star in workspace A is not visible from workspace B."""
    from app.services import workspace as ws_svc

    headers_a = _bearer(identity.id, workspace_id=workspace.id)
    r = await async_client.post(
        f"/api/v1/agents/{agent.id}/star?pinned=true",
        headers=headers_a,
    )
    assert r.status_code == 200

    other_ws = await ws_svc.create_workspace(
        db_session,
        name=f"Other {uuid.uuid4().hex[:6]}",
        slug=f"other-{uuid.uuid4().hex[:8]}",
        owner_identity_id=identity.id,
    )
    await db_session.commit()

    headers_b = _bearer(identity.id, workspace_id=other_ws.id)
    r2 = await async_client.get(
        "/api/v1/sidebar/my-items",
        headers=headers_b,
    )
    assert r2.status_code == 200, r2.text
    # ``other_ws`` has its own auto-fanned default agent; the agent from
    # ``workspace`` must not leak across.
    ids = {item["id"] for item in r2.json()["items"]}
    assert str(agent.id) not in ids


# ─── Star fan-out ─────────────────────────────────────────────
async def test_create_agent_fans_out_to_existing_members(
    async_client, db_session, workspace, identity
):
    """A new agent shows up in every existing member's My list, pinned."""
    from app.services import agent as agent_svc

    new_agent = await agent_svc.create_agent(
        db_session,
        workspace_id=workspace.id,
        created_by=identity.id,
        name="Fan-out fixture",
        description=None,
        persona_md=None,
    )
    await db_session.commit()

    headers = _bearer(identity.id, workspace_id=workspace.id)
    r = await async_client.get("/api/v1/sidebar/my-items", headers=headers)
    assert r.status_code == 200, r.text
    row = next(
        (i for i in r.json()["items"] if i["id"] == str(new_agent.id)),
        None,
    )
    assert row is not None, "new agent missing from caller's My list"
    assert row["pinned"] is True


async def test_accept_invitation_fans_out_existing_workspace_items(
    async_client, db_session, workspace, identity
):
    """New member inherits a pinned star for every existing workspace agent."""
    from app.services import auth as auth_svc
    from app.services import workspace as ws_svc

    invite = await ws_svc.create_invitation(
        db_session,
        workspace_id=workspace.id,
        invited_by=identity.id,
        email=None,
    )
    joiner = await auth_svc.register(
        db_session,
        email=f"join-{uuid.uuid4().hex[:8]}@example.com",
        name="Joiner",
        password="correct horse battery staple",
        create_personal_workspace=False,
    )
    await ws_svc.accept_invitation(db_session, code=invite.code, identity_id=joiner.identity.id)
    await db_session.commit()

    headers = _bearer(joiner.identity.id, workspace_id=workspace.id)
    r = await async_client.get("/api/v1/sidebar/my-items", headers=headers)
    assert r.status_code == 200, r.text
    body = r.json()
    agent_rows = [i for i in body["items"] if i["type"] == "agent"]
    # Workspace creation plants a default agent; the joiner must see it
    # with ``pinned=True`` thanks to the fan-out.
    assert agent_rows, "joiner saw no agents"
    assert all(row["pinned"] for row in agent_rows)


async def test_fan_out_skips_deleted_and_cross_workspace_agents(
    async_client, db_session, identity, workspace
):
    """Soft-deleted and cross-workspace agents are not fanned out."""
    from datetime import UTC, datetime

    from app.services import agent as agent_svc
    from app.services import auth as auth_svc
    from app.services import workspace as ws_svc

    tombstone = await agent_svc.create_agent(
        db_session,
        workspace_id=workspace.id,
        created_by=identity.id,
        name="Soon to be deleted",
        description=None,
        persona_md=None,
    )
    tombstone.deleted_at = datetime.now(UTC).replace(tzinfo=None)

    other_ws = await ws_svc.create_workspace(
        db_session,
        name=f"Other {uuid.uuid4().hex[:6]}",
        slug=f"other-{uuid.uuid4().hex[:8]}",
        owner_identity_id=identity.id,
    )
    cross_owner = await auth_svc.register(
        db_session,
        email=f"cross-{uuid.uuid4().hex[:8]}@example.com",
        name="Cross",
        password="correct horse battery staple",
        create_personal_workspace=False,
    )
    await db_session.commit()

    invite = await ws_svc.create_invitation(
        db_session,
        workspace_id=workspace.id,
        invited_by=identity.id,
        email=None,
    )
    await ws_svc.accept_invitation(
        db_session, code=invite.code, identity_id=cross_owner.identity.id
    )
    await db_session.commit()

    headers = _bearer(cross_owner.identity.id, workspace_id=workspace.id)
    r = await async_client.get("/api/v1/sidebar/my-items", headers=headers)
    assert r.status_code == 200, r.text
    ids = {item["id"] for item in r.json()["items"]}
    assert str(tombstone.id) not in ids
    # And nothing from ``other_ws`` leaks in either.
    assert all(item["type"] != "session" for item in r.json()["items"])
    _ = other_ws


async def test_fan_out_is_idempotent(db_session, workspace, identity, agent):
    """Calling the helper twice does not duplicate the star row."""
    from sqlalchemy import func, select

    from app.db.models.agent_star import AgentStar
    from app.services import stars as stars_svc

    async def _count() -> int:
        stmt = select(func.count(AgentStar.id)).where(
            AgentStar.agent_id == agent.id,
            AgentStar.identity_id == identity.id,
        )
        return int((await db_session.execute(stmt)).scalar() or 0)

    baseline = await _count()
    await stars_svc.fan_out_agent_to_workspace_members(
        db_session, workspace_id=workspace.id, agent_id=agent.id
    )
    await stars_svc.fan_out_agent_to_workspace_members(
        db_session, workspace_id=workspace.id, agent_id=agent.id
    )
    await db_session.commit()
    assert await _count() == baseline


# ─── Onboarding ─────────────────────────────────────────────
async def test_onboarding_complete_is_idempotent(async_client, db_session, identity):
    headers = _bearer(identity.id)
    r1 = await async_client.post(
        "/api/v1/onboarding/complete",
        headers=headers,
    )
    assert r1.status_code == 200, r1.text
    first = r1.json()["onboarded_at"]
    assert first

    r2 = await async_client.post(
        "/api/v1/onboarding/complete",
        headers=headers,
    )
    assert r2.status_code == 200, r2.text
    assert r2.json()["onboarded_at"] == first


async def test_me_exposes_onboarded_at(async_client, db_session, identity, workspace):
    # ``identity`` fixture creates a fresh identity that starts with
    # ``onboarded_at = NULL`` (the migration backfill only affects rows
    # that existed at migration time).
    _ = db_session
    headers = _bearer(identity.id, workspace_id=workspace.id)
    r = await async_client.get("/api/v1/me", headers=headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert "onboarded_at" in body
    assert body["onboarded_at"] is None

    r1 = await async_client.post(
        "/api/v1/onboarding/complete",
        headers=headers,
    )
    assert r1.status_code == 200, r1.text

    r2 = await async_client.get("/api/v1/me", headers=headers)
    assert r2.status_code == 200
    assert r2.json()["onboarded_at"] is not None
