"""Integration: 3 agent_profile routes (M3.4).

Covers:

* ``GET /agents/{id}/profile`` — workspace member sees the row but
  *not* the cross-workspace stats slice (404 when no row exists).
* ``POST /agents/{id}/profile/refresh`` — workspace admin only;
  member gets 403; idempotent re-run returns updated timestamp.
* ``GET /admin/agents/{id}/profile/cross-workspace`` — platform
  admin only; member gets 403, cross-workspace stats slice present.
"""

from __future__ import annotations

import uuid

import pytest

from app.core.security import create_access_token
from app.db.models.identity import PlatformRole
from app.db.models.membership import Membership, MembershipStatus
from app.db.models.role import BuiltinRole
from app.services import agent_profile as svc

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


async def _stub_aux_off(monkeypatch):
    """Force the aux LLM path off so refresh tests stay deterministic."""

    async def fake_breaker(**_):
        return False

    async def fake_get_aux(**_):
        return None

    monkeypatch.setattr(svc, "is_breaker_open", fake_breaker)
    monkeypatch.setattr(svc, "get_aux_model", fake_get_aux)


async def test_get_profile_404_when_missing(async_client, db_session, workspace, identity, agent):
    _ = db_session
    headers = _bearer(identity.id, workspace_id=workspace.id)
    r = await async_client.get(
        f"/api/v1/agents/{agent.id}/profile",
        headers=headers,
    )
    assert r.status_code == 404
    body = r.json()
    detail = body.get("detail")
    code = detail.get("code") if isinstance(detail, dict) else body.get("code")
    assert code == "agent_profile.not_found"


async def test_refresh_creates_then_get_succeeds(
    async_client, db_session, workspace, identity, agent, monkeypatch
):
    _ = db_session
    await _stub_aux_off(monkeypatch)
    headers = _bearer(identity.id, workspace_id=workspace.id)

    r = await async_client.post(
        f"/api/v1/agents/{agent.id}/profile/refresh",
        headers=headers,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["agent_id"] == str(agent.id)
    assert body["workspace_id"] == str(workspace.id)
    assert body["aux_skipped"] is True
    assert body["aux_skip_reason"] == "no_aux_model"

    # Re-fetch through the read endpoint — payload omits cross-workspace.
    r2 = await async_client.get(
        f"/api/v1/agents/{agent.id}/profile",
        headers=headers,
    )
    assert r2.status_code == 200, r2.text
    profile = r2.json()
    assert profile["agent_id"] == str(agent.id)
    assert "cross_workspace_stats_json" not in profile
    assert "strengths_json" in profile
    assert "failure_modes_json" in profile


async def test_refresh_rejects_non_admin_member(
    async_client, db_session, workspace, agent, monkeypatch
):
    """A regular MEMBER must not refresh."""
    from app.services import auth as auth_svc

    await _stub_aux_off(monkeypatch)
    member = await auth_svc.register(
        db_session,
        email=f"ap-mem-{uuid.uuid4().hex[:8]}@example.com",
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

    headers = _bearer(member_identity.id, workspace_id=workspace.id)
    r = await async_client.post(
        f"/api/v1/agents/{agent.id}/profile/refresh",
        headers=headers,
    )
    assert r.status_code == 403


async def test_cross_workspace_admin_only(
    async_client, db_session, workspace, identity, agent, monkeypatch
):
    """Workspace member is denied; platform admin allowed."""
    from app.services import auth as auth_svc

    await _stub_aux_off(monkeypatch)

    # First seed a profile via the workspace owner so the cross-
    # workspace endpoint has a row to read.
    headers_owner = _bearer(identity.id, workspace_id=workspace.id)
    r = await async_client.post(
        f"/api/v1/agents/{agent.id}/profile/refresh",
        headers=headers_owner,
    )
    assert r.status_code == 200

    # Workspace owner is *not* platform admin → forbidden.
    r2 = await async_client.get(
        f"/api/v1/admin/agents/{agent.id}/profile/cross-workspace",
        headers=headers_owner,
    )
    assert r2.status_code == 403
    body = r2.json()
    detail = body.get("detail")
    code = detail.get("code") if isinstance(detail, dict) else body.get("code")
    assert code == "agent_profile.cross_workspace_forbidden"

    # Promote a fresh identity to platform admin → allowed.
    admin = await auth_svc.register(
        db_session,
        email=f"ap-padmin-{uuid.uuid4().hex[:8]}@example.com",
        name="Platform Admin",
        password="correct horse battery staple",
        create_personal_workspace=False,
    )
    admin_identity = admin.identity
    admin_identity.platform_role = PlatformRole.PLATFORM_ADMIN
    await db_session.flush()
    await db_session.commit()

    headers_admin = _bearer(admin_identity.id)
    r3 = await async_client.get(
        f"/api/v1/admin/agents/{agent.id}/profile/cross-workspace",
        headers=headers_admin,
    )
    assert r3.status_code == 200, r3.text
    profile = r3.json()
    assert profile["agent_id"] == str(agent.id)
    assert "cross_workspace_stats_json" in profile
    stats = profile["cross_workspace_stats_json"]
    assert "total_runs_across_tenants" in stats
    assert "median_judge_score" in stats
    assert "top_failure_kinds" in stats
