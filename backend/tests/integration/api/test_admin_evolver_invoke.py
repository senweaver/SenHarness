"""Integration tests for ``POST /admin/workspaces/{id}/evolver/invoke``.

Covers the auth gate (RBAC), the rate limit, and the happy path that
delegates into :func:`invoke_evolver_subagent` and serialises the
result struct back to the client.
"""

from __future__ import annotations

import uuid

import pytest

from app.agents.builtin import evolver_agent as ev
from app.agents.builtin.evolver_agent import (
    EvolverDisabledError,
    EvolverInvokeResult,
)
from app.core.security import create_access_token
from app.db.models.identity import PlatformRole
from app.db.models.membership import Membership, MembershipStatus
from app.db.models.role import BuiltinRole

pytestmark = pytest.mark.asyncio


def _bearer(identity_id: uuid.UUID, *, workspace_id: uuid.UUID | None = None) -> dict[str, str]:
    token, _, _ = create_access_token(
        identity_id=str(identity_id),
        workspace_id=str(workspace_id) if workspace_id is not None else None,
        roles=[],
    )
    headers = {"Authorization": f"Bearer {token}"}
    if workspace_id is not None:
        headers["X-Workspace-Id"] = str(workspace_id)
    return headers


async def _make_workspace_admin(db_session, workspace, identity):
    """Owner membership is created by the ``workspace`` fixture; this
    helper is a no-op for that case but kept so future tests can swap
    in a non-owner admin without rewriting the call sites.
    """
    from app.repositories.workspace import MembershipRepository

    mem = await MembershipRepository(db_session).get_by_identity_and_workspace(
        identity.id, workspace.id
    )
    assert mem is not None
    assert mem.role == BuiltinRole.OWNER.value
    return mem


async def test_invoke_happy_path_returns_result(
    async_client, db_session, workspace, identity, monkeypatch
):
    workspace.home_config_json = {"evolver": {"enabled": True}}
    await db_session.flush()
    await db_session.commit()
    await _make_workspace_admin(db_session, workspace, identity)

    expected_run_id = uuid.uuid4()

    async def _stub_invoke(**kwargs):
        return EvolverInvokeResult(
            run_id=expected_run_id,
            proposals_created=2,
            skipped=False,
            duration_ms=1234,
            final_message="ok",
            error=None,
            aux_model="test:test",
            timed_out=False,
        )

    monkeypatch.setattr("app.api.v1.admin_evolver.invoke_evolver_subagent", _stub_invoke)

    headers = _bearer(identity.id, workspace_id=workspace.id)
    resp = await async_client.post(
        f"/api/v1/admin/workspaces/{workspace.id}/evolver/invoke",
        headers=headers,
        json={"triggering_run_ids": []},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["run_id"] == str(expected_run_id)
    assert body["proposals_created"] == 2
    assert body["skipped"] is False
    assert body["aux_model"] == "test:test"
    assert body["timed_out"] is False


async def test_invoke_disabled_workspace_returns_409(
    async_client, db_session, workspace, identity, monkeypatch
):
    workspace.home_config_json = {"evolver": {"enabled": False}}
    await db_session.flush()
    await db_session.commit()
    await _make_workspace_admin(db_session, workspace, identity)

    async def _stub_invoke(**_kwargs):
        raise EvolverDisabledError("workspace disabled")

    monkeypatch.setattr("app.api.v1.admin_evolver.invoke_evolver_subagent", _stub_invoke)

    headers = _bearer(identity.id, workspace_id=workspace.id)
    resp = await async_client.post(
        f"/api/v1/admin/workspaces/{workspace.id}/evolver/invoke",
        headers=headers,
        json={"triggering_run_ids": []},
    )
    assert resp.status_code == 409, resp.text


async def test_invoke_rejects_member_role(async_client, db_session, workspace, monkeypatch):
    """A regular MEMBER must not be able to fire the evolver."""
    from app.services import auth as auth_svc

    member = await auth_svc.register(
        db_session,
        email=f"m-{uuid.uuid4().hex[:8]}@example.com",
        name="Member",
        password="correct horse battery staple",
        create_personal_workspace=False,
    )
    member_identity = member.identity
    db_session.add(
        Membership(
            workspace_id=workspace.id,
            identity_id=member_identity.id,
            role=BuiltinRole.MEMBER.value,
            status=MembershipStatus.ACTIVE,
        )
    )
    await db_session.flush()
    await db_session.commit()

    invoked = False

    async def _stub_invoke(**_kwargs):  # pragma: no cover - must not fire
        nonlocal invoked
        invoked = True
        return EvolverInvokeResult(
            run_id=uuid.uuid4(),
            proposals_created=0,
            skipped=True,
            duration_ms=0,
            final_message=None,
        )

    monkeypatch.setattr("app.api.v1.admin_evolver.invoke_evolver_subagent", _stub_invoke)

    headers = _bearer(member_identity.id, workspace_id=workspace.id)
    resp = await async_client.post(
        f"/api/v1/admin/workspaces/{workspace.id}/evolver/invoke",
        headers=headers,
        json={"triggering_run_ids": []},
    )
    assert resp.status_code == 403
    assert invoked is False


async def test_invoke_platform_admin_bypasses_workspace_membership(
    async_client, db_session, workspace, monkeypatch
):
    """A platform admin can invoke the evolver in any workspace,
    even without a membership row.
    """
    from app.services import auth as auth_svc

    admin = await auth_svc.register(
        db_session,
        email=f"pa-{uuid.uuid4().hex[:8]}@example.com",
        name="Platform Admin",
        password="correct horse battery staple",
        create_personal_workspace=False,
    )
    admin_identity = admin.identity
    admin_identity.platform_role = PlatformRole.PLATFORM_ADMIN
    workspace.home_config_json = {"evolver": {"enabled": True}}
    await db_session.flush()
    await db_session.commit()

    expected_run_id = uuid.uuid4()

    async def _stub_invoke(**_kwargs):
        return EvolverInvokeResult(
            run_id=expected_run_id,
            proposals_created=0,
            skipped=True,
            duration_ms=42,
            final_message="No SkillPack proposals worth filing.",
            error=None,
            aux_model="test:test",
            timed_out=False,
        )

    monkeypatch.setattr("app.api.v1.admin_evolver.invoke_evolver_subagent", _stub_invoke)

    headers = _bearer(admin_identity.id, workspace_id=workspace.id)
    resp = await async_client.post(
        f"/api/v1/admin/workspaces/{workspace.id}/evolver/invoke",
        headers=headers,
        json={"triggering_run_ids": []},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["run_id"] == str(expected_run_id)
    assert body["skipped"] is True


async def test_invoke_rate_limited_after_three_calls(
    async_client, db_session, workspace, identity, monkeypatch
):
    """Rate limit is 3/300s — fourth call in the same window must
    return 429 without ever reaching the invoke handler.
    """
    workspace.home_config_json = {"evolver": {"enabled": True}}
    await db_session.flush()
    await db_session.commit()

    call_count = 0

    async def _stub_invoke(**_kwargs):
        nonlocal call_count
        call_count += 1
        return EvolverInvokeResult(
            run_id=uuid.uuid4(),
            proposals_created=0,
            skipped=True,
            duration_ms=0,
            final_message=None,
        )

    monkeypatch.setattr("app.api.v1.admin_evolver.invoke_evolver_subagent", _stub_invoke)

    headers = _bearer(identity.id, workspace_id=workspace.id)
    statuses: list[int] = []
    for _ in range(4):
        resp = await async_client.post(
            f"/api/v1/admin/workspaces/{workspace.id}/evolver/invoke",
            headers=headers,
            json={"triggering_run_ids": []},
        )
        statuses.append(resp.status_code)

    assert statuses[:3] == [200, 200, 200], statuses
    assert statuses[3] == 429, statuses
    assert call_count == 3, "fourth call must short-circuit before invoke"


# Suppress unused-import lint when ev is only referenced indirectly.
_ = ev
