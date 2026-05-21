"""Integration tests for the M4.6 ``/admin/jobs/*`` routes.

Five endpoints, with RBAC + happy paths covered:

* ``GET /admin/jobs/queues`` (workspace admin / platform admin).
* ``GET /admin/jobs/recent`` (status filter + scope check).
* ``GET /admin/jobs/health`` (totals).
* ``POST /admin/jobs/{job_id}/retry`` (platform-admin-only,
  workspace admin must be 403).
"""

from __future__ import annotations

import uuid
from datetime import timedelta

import pytest

from app.core.security import create_access_token, utcnow_naive
from app.db.models.identity import PlatformRole
from app.db.models.job_run import JobRun, JobRunStatus

pytestmark = pytest.mark.asyncio


def _bearer(
    identity_id: uuid.UUID, *, workspace_id: uuid.UUID | None = None
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


async def _seed_failed_job(db_session, workspace) -> JobRun:
    row = JobRun(
        job_id=f"failed-{uuid.uuid4().hex[:8]}",
        function_name="curator_tick",
        workspace_id=workspace.id,
        status=JobRunStatus.FAILED_PERMANENT,
        enqueued_at=utcnow_naive() - timedelta(minutes=20),
        started_at=utcnow_naive() - timedelta(minutes=19),
        finished_at=utcnow_naive() - timedelta(minutes=18),
        duration_ms=120_000,
        retry_count=2,
        args_json={"args": [], "kwargs": {"workspace_id": str(workspace.id)}},
        error_class="RuntimeError",
        error_message="simulated permanent failure",
    )
    db_session.add(row)
    await db_session.flush([row])
    await db_session.commit()
    return row


# ── /queues ───────────────────────────────────────────────────
async def test_queues_workspace_admin_returns_scoped_data(
    async_client, db_session, workspace, identity
):
    await _seed_failed_job(db_session, workspace)
    headers = _bearer(identity.id, workspace_id=workspace.id)
    resp = await async_client.get(
        "/api/v1/admin/jobs/queues", headers=headers
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "by_function" in body
    assert "redis_queue" in body
    fn_names = {row["function_name"] for row in body["by_function"]}
    assert "curator_tick" in fn_names


async def test_queues_rejects_member_without_admin_role(
    async_client, db_session, workspace
):
    """A non-admin workspace member must get 403 even with X-Workspace-Id."""
    from app.db.models.membership import Membership, MembershipStatus
    from app.db.models.role import BuiltinRole
    from app.services import auth as auth_svc

    member = await auth_svc.register(
        db_session,
        email=f"m-{uuid.uuid4().hex[:8]}@example.com",
        name="Member",
        password="correct horse battery staple",
        create_personal_workspace=False,
    )
    db_session.add(
        Membership(
            workspace_id=workspace.id,
            identity_id=member.identity.id,
            role=BuiltinRole.MEMBER.value,
            status=MembershipStatus.ACTIVE,
        )
    )
    await db_session.flush()
    await db_session.commit()

    headers = _bearer(member.identity.id, workspace_id=workspace.id)
    resp = await async_client.get(
        "/api/v1/admin/jobs/queues", headers=headers
    )
    assert resp.status_code == 403


# ── /recent ───────────────────────────────────────────────────
async def test_recent_filters_by_status(
    async_client, db_session, workspace, identity
):
    failed_row = await _seed_failed_job(db_session, workspace)

    # And one success row that must NOT match a `status=failed_permanent` filter.
    db_session.add(
        JobRun(
            job_id=f"ok-{uuid.uuid4().hex[:8]}",
            function_name="judge_session_artifact",
            workspace_id=workspace.id,
            status=JobRunStatus.SUCCESS,
            finished_at=utcnow_naive(),
            duration_ms=10,
            retry_count=0,
            args_json={},
        )
    )
    await db_session.flush()
    await db_session.commit()

    headers = _bearer(identity.id, workspace_id=workspace.id)
    resp = await async_client.get(
        "/api/v1/admin/jobs/recent?status=failed_permanent",
        headers=headers,
    )
    assert resp.status_code == 200
    rows = resp.json()
    job_ids = {r["job_id"] for r in rows}
    assert failed_row.job_id in job_ids
    for row in rows:
        assert row["status"] == "failed_permanent"


async def test_recent_invalid_status_returns_400(
    async_client, db_session, workspace, identity
):
    headers = _bearer(identity.id, workspace_id=workspace.id)
    resp = await async_client.get(
        "/api/v1/admin/jobs/recent?status=bogus", headers=headers
    )
    assert resp.status_code == 400


# ── /health ───────────────────────────────────────────────────
async def test_health_returns_totals(
    async_client, db_session, workspace, identity
):
    await _seed_failed_job(db_session, workspace)
    headers = _bearer(identity.id, workspace_id=workspace.id)
    resp = await async_client.get(
        "/api/v1/admin/jobs/health", headers=headers
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "totals" in body
    totals = body["totals"]
    for key in (
        "queued",
        "running",
        "success",
        "failed",
        "failed_permanent",
        "failed_permanent_total",
    ):
        assert key in totals
    assert totals["failed_permanent_total"] >= 1


# ── /retry ────────────────────────────────────────────────────
async def test_retry_workspace_admin_is_forbidden(
    async_client, db_session, workspace, identity
):
    """The retry endpoint is platform-admin-only per the M4.6 RBAC table."""
    failed_row = await _seed_failed_job(db_session, workspace)
    headers = _bearer(identity.id, workspace_id=workspace.id)
    resp = await async_client.post(
        f"/api/v1/admin/jobs/{failed_row.job_id}/retry",
        headers=headers,
    )
    assert resp.status_code == 403


async def test_retry_platform_admin_re_enqueues(
    async_client, db_session, workspace, monkeypatch
):
    from app.services import auth as auth_svc

    admin = await auth_svc.register(
        db_session,
        email=f"pa-{uuid.uuid4().hex[:8]}@example.com",
        name="Platform Admin",
        password="correct horse battery staple",
        create_personal_workspace=False,
    )
    admin.identity.platform_role = PlatformRole.PLATFORM_ADMIN
    failed_row = await _seed_failed_job(db_session, workspace)
    await db_session.commit()

    captured: dict = {}

    async def _fake_enqueue(function_name, *args, **kwargs):
        captured["function_name"] = function_name
        captured["args"] = args
        captured["kwargs"] = kwargs
        return f"reissued-{uuid.uuid4().hex[:8]}"

    monkeypatch.setattr(
        "app.worker.queue.enqueue", _fake_enqueue
    )

    headers = _bearer(admin.identity.id, workspace_id=workspace.id)
    resp = await async_client.post(
        f"/api/v1/admin/jobs/{failed_row.job_id}/retry", headers=headers
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["enqueued"] is True
    assert body["new_job_id"] is not None
    assert captured["function_name"] == failed_row.function_name


async def test_retry_for_unknown_job_returns_404(
    async_client, db_session, workspace
):
    from app.services import auth as auth_svc

    admin = await auth_svc.register(
        db_session,
        email=f"pa2-{uuid.uuid4().hex[:8]}@example.com",
        name="Platform Admin",
        password="correct horse battery staple",
        create_personal_workspace=False,
    )
    admin.identity.platform_role = PlatformRole.PLATFORM_ADMIN
    await db_session.commit()

    headers = _bearer(admin.identity.id, workspace_id=workspace.id)
    resp = await async_client.post(
        "/api/v1/admin/jobs/no-such-job/retry", headers=headers
    )
    assert resp.status_code == 404
