"""Integration: ``retention_physical_purge`` cron behaviour (M0.11).

Confirms that:

1. With ``physical_purge_enabled = False`` the cron writes a dry-run
   audit row and never deletes.
2. Flipping the flag to ``True`` actually deletes rows past their
   per-table retention window.
3. ``per_table_days`` overrides the platform default.
"""

from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
from sqlalchemy import select, text

from app.core.security import utcnow_naive
from app.db.models.audit import AuditEvent
from app.db.models.session_artifact import SessionArtifact
from app.db.session import get_session_factory
from app.jobs.retention import retention_physical_purge
from app.services.system_settings import (
    RetentionSettings,
    SystemSettingKey,
    set_system_setting,
)

pytestmark = pytest.mark.asyncio


async def _bootstrap(async_client) -> dict[str, str]:
    email = f"purge-{uuid.uuid4().hex[:8]}@example.com"
    password = "purge-test-password-very-long"
    r = await async_client.post(
        "/api/v1/auth/register",
        json={"email": email, "name": "Purge Tester", "password": password},
    )
    assert r.status_code == 201, r.text
    ident_id = r.json()["identity_id"]
    r = await async_client.post("/api/v1/auth/login", json={"email": email, "password": password})
    token = r.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    r = await async_client.post(
        "/api/v1/workspaces",
        headers=headers,
        json={"name": "Purge WS", "slug": f"purge-{uuid.uuid4().hex[:6]}"},
    )
    assert r.status_code in (200, 201)
    ws_id = r.json()["id"]
    headers["X-Workspace-Id"] = ws_id
    return {"workspace_id": ws_id, "identity_id": ident_id, "headers": headers}


async def _seed_old_artifact(ws_id: str, ident_id: str, *, days_ago: int) -> uuid.UUID:
    factory = get_session_factory()
    sid = uuid.uuid4()
    aid = uuid.uuid4()
    async with factory() as db:
        await db.execute(
            text(
                "INSERT INTO sessions (id, workspace_id, kind, owner_identity_id, "
                "title, title_source, state, message_count, metadata_json) "
                "VALUES (:id, :ws, 'p2p', :uid, 'seed', 'auto_truncate', 'active', "
                "0, '{}'::jsonb)"
            ),
            {"id": sid, "ws": uuid.UUID(ws_id), "uid": uuid.UUID(ident_id)},
        )
        artifact = SessionArtifact(
            id=aid,
            workspace_id=uuid.UUID(ws_id),
            run_id=uuid.uuid4(),
            session_id=sid,
            identity_id=uuid.UUID(ident_id),
            user_text_hash="0" * 64,
            turns_json=[],
            injected_skill_pack_ids=[],
            invoked_tools=[],
            iteration_count=0,
            final_outcome="success",
            finished_at=utcnow_naive(),
            deleted_at=utcnow_naive() - timedelta(days=days_ago),
        )
        db.add(artifact)
        await db.commit()
    return aid


async def _set_retention(settings_obj: RetentionSettings) -> None:
    factory = get_session_factory()
    async with factory() as db:
        await set_system_setting(db, SystemSettingKey.RETENTION, settings_obj.model_dump())
        await db.commit()


async def test_dry_run_audits_but_does_not_delete(async_client):
    ctx = await _bootstrap(async_client)
    aid = await _seed_old_artifact(ctx["workspace_id"], ctx["identity_id"], days_ago=60)
    await _set_retention(RetentionSettings(physical_purge_enabled=False))

    summary = await retention_physical_purge({})

    assert summary["dry_run"] is True
    assert summary["totals"]["candidates"] >= 1
    assert summary["totals"]["deleted"] == 0

    factory = get_session_factory()
    async with factory() as db:
        survivor = (
            await db.execute(select(SessionArtifact).where(SessionArtifact.id == aid))
        ).scalar_one_or_none()
        assert survivor is not None

        audits = (
            (await db.execute(select(AuditEvent).where(AuditEvent.action == "data.physical_purge")))
            .scalars()
            .all()
        )
        assert len(audits) >= 1
        latest = audits[-1]
        assert latest.metadata_json.get("dry_run") is True


async def test_enabled_purge_actually_deletes(async_client):
    ctx = await _bootstrap(async_client)
    aid = await _seed_old_artifact(ctx["workspace_id"], ctx["identity_id"], days_ago=60)
    await _set_retention(RetentionSettings(physical_purge_enabled=True))

    summary = await retention_physical_purge({})
    assert summary["dry_run"] is False
    assert summary["totals"]["deleted"] >= 1

    factory = get_session_factory()
    async with factory() as db:
        gone = (
            await db.execute(select(SessionArtifact).where(SessionArtifact.id == aid))
        ).scalar_one_or_none()
        assert gone is None


async def test_per_table_override_changes_retention(async_client):
    ctx = await _bootstrap(async_client)
    aid = await _seed_old_artifact(ctx["workspace_id"], ctx["identity_id"], days_ago=60)
    # Override session_artifacts to 90 days while keeping purge enabled.
    await _set_retention(
        RetentionSettings(
            default_days=30,
            per_table_days={"session_artifacts": 90},
            physical_purge_enabled=True,
        )
    )

    summary = await retention_physical_purge({})
    sa_table = summary["tables"]["session_artifacts"]
    assert sa_table["candidates"] == 0
    assert sa_table["deleted"] == 0

    factory = get_session_factory()
    async with factory() as db:
        survivor = (
            await db.execute(select(SessionArtifact).where(SessionArtifact.id == aid))
        ).scalar_one_or_none()
        assert survivor is not None
