"""Integration: ``POST /skills/evolve/trigger`` (M2.3).

Covers the auth gate (workspace admin only), the rate limit
(``skills_evolve_trigger 2/300s``), and the happy path that runs the
dispatcher synchronously and returns the structured result.
"""

from __future__ import annotations

import uuid

import pytest

from app.core.security import create_access_token
from app.db.models.identity import PlatformRole
from app.db.models.membership import Membership, MembershipStatus
from app.db.models.role import BuiltinRole
from app.services import evolver_workflow as wf

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


def _stub_result(workspace_id: uuid.UUID) -> wf.WorkflowExecutionResult:
    return wf.WorkflowExecutionResult(
        workspace_id=workspace_id,
        engine="workflow",
        artifacts_drained=12,
        artifacts_summarized=12,
        proposals_created=3,
        skipped=False,
        skip_reason=None,
        duration_ms=789,
        error=None,
        invocation_kind="manual",
        aux_model="test:test",
    )


async def test_trigger_happy_path_returns_result(
    async_client, db_session, workspace, identity, monkeypatch
):
    workspace.home_config_json = {"evolver": {"enabled": True}}
    await db_session.flush()
    await db_session.commit()

    captured: dict[str, object] = {}

    async def _stub_evolve(
        db, *, workspace_id, invocation_kind, actor_identity_id, bypass_min_artifacts
    ):
        captured["workspace_id"] = workspace_id
        captured["invocation_kind"] = invocation_kind
        captured["actor_identity_id"] = actor_identity_id
        captured["bypass_min_artifacts"] = bypass_min_artifacts
        return _stub_result(workspace_id)

    monkeypatch.setattr("app.api.v1.skills_evolve.evolve_workspace_skills", _stub_evolve)

    headers = _bearer(identity.id, workspace_id=workspace.id)
    resp = await async_client.post(
        "/api/v1/skills/evolve/trigger",
        headers=headers,
        json={"workspace_id": str(workspace.id), "bypass_min_artifacts": True},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["workspace_id"] == str(workspace.id)
    assert body["proposals_created"] == 3
    assert body["engine"] == "workflow"
    assert body["invocation_kind"] == "manual"
    assert body["skipped"] is False
    assert body["aux_model"] == "test:test"
    assert captured["invocation_kind"] == "manual"
    assert captured["bypass_min_artifacts"] is True
    assert captured["actor_identity_id"] == identity.id


async def test_trigger_member_role_returns_403(async_client, db_session, workspace, monkeypatch):
    """A non-admin member must not be able to fire the trigger."""
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

    async def _stub_evolve(**_kwargs):  # pragma: no cover - must not fire
        nonlocal invoked
        invoked = True
        return _stub_result(workspace.id)

    monkeypatch.setattr("app.api.v1.skills_evolve.evolve_workspace_skills", _stub_evolve)

    headers = _bearer(member_identity.id, workspace_id=workspace.id)
    resp = await async_client.post(
        "/api/v1/skills/evolve/trigger",
        headers=headers,
        json={"workspace_id": str(workspace.id)},
    )
    assert resp.status_code == 403
    assert invoked is False


async def test_trigger_platform_admin_bypass(async_client, db_session, workspace, monkeypatch):
    """Platform admins can fire the trigger without a membership row."""
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

    async def _stub_evolve(db, *, workspace_id, **_kwargs):
        return _stub_result(workspace_id)

    monkeypatch.setattr("app.api.v1.skills_evolve.evolve_workspace_skills", _stub_evolve)

    headers = _bearer(admin_identity.id, workspace_id=workspace.id)
    resp = await async_client.post(
        "/api/v1/skills/evolve/trigger",
        headers=headers,
        json={"workspace_id": str(workspace.id)},
    )
    assert resp.status_code == 200, resp.text


async def test_trigger_rate_limited_after_two_calls(
    async_client, db_session, workspace, identity, monkeypatch
):
    """Rate limit is 2/300s — third call in the same window must 429."""
    workspace.home_config_json = {"evolver": {"enabled": True}}
    await db_session.flush()
    await db_session.commit()

    call_count = 0

    async def _stub_evolve(db, *, workspace_id, **_kwargs):
        nonlocal call_count
        call_count += 1
        return _stub_result(workspace_id)

    monkeypatch.setattr("app.api.v1.skills_evolve.evolve_workspace_skills", _stub_evolve)

    headers = _bearer(identity.id, workspace_id=workspace.id)
    statuses: list[int] = []
    for _ in range(3):
        resp = await async_client.post(
            "/api/v1/skills/evolve/trigger",
            headers=headers,
            json={"workspace_id": str(workspace.id)},
        )
        statuses.append(resp.status_code)

    assert statuses[:2] == [200, 200], statuses
    assert statuses[2] == 429, statuses
    assert call_count == 2
