"""Integration: ``retention_sweep_cascade`` against a real DB (M0.11).

The sweep is cron-driven in production; here we invoke it directly so
the test cycle stays bounded. Each test seeds a soft-deleted identity
and asserts the cascade landed on the expected rows + advanced the
watermark + (when the cascade fakes a failure) audited
``job.failed_permanent``.
"""

from __future__ import annotations

import uuid
from datetime import timedelta
from unittest.mock import patch

import pytest
from sqlalchemy import select, text

from app.core.security import utcnow_naive
from app.db.models.audit import AuditEvent
from app.db.models.email_verification import EmailVerificationToken
from app.db.models.identity import Identity
from app.db.models.retention_watermark import (
    RetentionScopeKind,
    RetentionWatermark,
)
from app.db.models.session_artifact import SessionArtifact
from app.db.models.session_goal import SessionGoal
from app.db.session import get_session_factory
from app.jobs.retention import retention_sweep_cascade

pytestmark = pytest.mark.asyncio


async def _bootstrap(async_client) -> dict[str, str]:
    email = f"retn-{uuid.uuid4().hex[:8]}@example.com"
    password = "retention-test-password-very-long"
    r = await async_client.post(
        "/api/v1/auth/register",
        json={"email": email, "name": "Retention Tester", "password": password},
    )
    assert r.status_code == 201, r.text
    identity_id = r.json()["identity_id"]
    r = await async_client.post("/api/v1/auth/login", json={"email": email, "password": password})
    token = r.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    r = await async_client.post(
        "/api/v1/workspaces",
        headers=headers,
        json={"name": "Retention WS", "slug": f"retn-{uuid.uuid4().hex[:6]}"},
    )
    assert r.status_code in (200, 201)
    ws_id = r.json()["id"]
    headers["X-Workspace-Id"] = ws_id
    return {
        "headers": headers,
        "workspace_id": ws_id,
        "identity_id": identity_id,
    }


async def _make_session(db, ws_id: str, ident_id: str) -> uuid.UUID:
    sid = uuid.uuid4()
    await db.execute(
        text(
            "INSERT INTO sessions (id, workspace_id, kind, owner_identity_id, "
            "title, title_source, state, message_count, metadata_json) "
            "VALUES (:id, :ws, 'p2p', :uid, 'seed', 'auto_truncate', 'active', "
            "0, '{}'::jsonb)"
        ),
        {"id": sid, "ws": uuid.UUID(ws_id), "uid": uuid.UUID(ident_id)},
    )
    return sid


async def _seed_scoped_rows(ws_id: str, ident_id: str) -> dict[str, uuid.UUID]:
    """Seed one row per cascade target so we can assert the sweep
    actually touched them. Uses the global session factory because the
    sweep job opens its own session.
    """
    factory = get_session_factory()
    async with factory() as db:
        sid = await _make_session(db, ws_id, ident_id)

        goal = SessionGoal(
            workspace_id=uuid.UUID(ws_id),
            session_id=sid,
            goal_text="seed",
            success_criteria=[],
            locked_by=uuid.UUID(ident_id),
            alignment_threshold=0.6,
            metadata_json={},
        )
        db.add(goal)

        artifact = SessionArtifact(
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
        )
        db.add(artifact)

        token = EmailVerificationToken(
            identity_id=uuid.UUID(ident_id),
            token_hash=uuid.uuid4().hex + uuid.uuid4().hex,
            expires_at=utcnow_naive() + timedelta(hours=1),
        )
        db.add(token)

        await db.flush()
        ids = {
            "goal": goal.id,
            "artifact": artifact.id,
            "token": token.id,
        }
        await db.commit()
        return ids


async def _soft_delete_identity(ident_id: str) -> None:
    """Mark the identity as soft-deleted via raw SQL so no auth path
    (token blacklist, session cleanup, etc.) interferes with the test.
    """
    factory = get_session_factory()
    async with factory() as db:
        await db.execute(
            text("UPDATE identities SET deleted_at = now() WHERE id = :id"),
            {"id": uuid.UUID(ident_id)},
        )
        await db.commit()


async def _reset_watermarks_to_far_past() -> None:
    """Re-anchor watermarks so the test identity is visible to the sweep."""
    factory = get_session_factory()
    async with factory() as db:
        await db.execute(
            text("UPDATE retention_watermarks SET last_seen_deleted_at = now() - interval '1 hour'")
        )
        await db.commit()


async def test_sweep_cascades_and_advances_watermark(async_client):
    ctx = await _bootstrap(async_client)
    seeded = await _seed_scoped_rows(ctx["workspace_id"], ctx["identity_id"])

    await _soft_delete_identity(ctx["identity_id"])
    await _reset_watermarks_to_far_past()

    summary = await retention_sweep_cascade({})

    assert summary["identities_swept"] >= 1
    assert summary["rows_cascaded_total"] >= 1

    factory = get_session_factory()
    async with factory() as db:
        goal = (
            await db.execute(select(SessionGoal).where(SessionGoal.id == seeded["goal"]))
        ).scalar_one()
        assert goal.deleted_at is not None

        artifact = (
            await db.execute(
                select(SessionArtifact).where(SessionArtifact.id == seeded["artifact"])
            )
        ).scalar_one()
        assert artifact.deleted_at is not None

        # Hard-delete cascade target.
        token = (
            await db.execute(
                select(EmailVerificationToken).where(EmailVerificationToken.id == seeded["token"])
            )
        ).scalar_one_or_none()
        assert token is None

        wm = (
            await db.execute(
                select(RetentionWatermark).where(
                    RetentionWatermark.scope_kind == RetentionScopeKind.IDENTITY
                )
            )
        ).scalar_one()
        # Watermark must have advanced past our identity's deleted_at.
        ident = (
            await db.execute(select(Identity).where(Identity.id == uuid.UUID(ctx["identity_id"])))
        ).scalar_one()
        assert wm.last_seen_deleted_at >= ident.deleted_at
        assert wm.last_run_at is not None
        assert wm.last_error is None

        # Audit row was written for the cascade.
        audits = (
            (
                await db.execute(
                    select(AuditEvent).where(AuditEvent.action == "data.cascade_soft_delete")
                )
            )
            .scalars()
            .all()
        )
        assert len(audits) >= 1


async def test_sweep_idempotent_second_run_zero(async_client):
    ctx = await _bootstrap(async_client)
    await _seed_scoped_rows(ctx["workspace_id"], ctx["identity_id"])

    await _soft_delete_identity(ctx["identity_id"])
    await _reset_watermarks_to_far_past()

    first = await retention_sweep_cascade({})
    assert first["identities_swept"] >= 1
    assert first["rows_cascaded_total"] >= 1

    second = await retention_sweep_cascade({})
    # Watermark advanced → no pending identities; both counters at zero.
    assert second["identities_swept"] == 0
    assert second["rows_cascaded_total"] == 0
    assert second["permanent_failures"] == 0


async def test_sweep_records_permanent_failure_after_three_attempts(
    async_client,
):
    """Force ``cascade_for_identity`` to raise three times and prove
    the sweep audits ``job.failed_permanent`` and still advances past
    the bad row so the head-of-line stays clear.
    """
    ctx = await _bootstrap(async_client)
    await _soft_delete_identity(ctx["identity_id"])
    await _reset_watermarks_to_far_past()

    from app.jobs import retention as retention_job_mod

    boom_calls = {"n": 0}

    async def boom(_db, *, identity_id):
        boom_calls["n"] += 1
        raise RuntimeError("synthetic failure")

    with patch.object(retention_job_mod.retention_svc, "cascade_for_identity", boom):
        summary = await retention_sweep_cascade({})

    assert boom_calls["n"] >= 3
    assert summary["permanent_failures"] >= 1

    factory = get_session_factory()
    async with factory() as db:
        audits = (
            (
                await db.execute(
                    select(AuditEvent).where(
                        AuditEvent.action == "job.failed_permanent",
                        AuditEvent.resource_type == "retention_sweep_cascade",
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(audits) >= 1

        wm = (
            await db.execute(
                select(RetentionWatermark).where(
                    RetentionWatermark.scope_kind == RetentionScopeKind.IDENTITY
                )
            )
        ).scalar_one()
        # Even though every cascade attempt failed, the watermark must
        # advance so the next tick doesn't immediately re-enter the
        # broken row.
        ident = (
            await db.execute(select(Identity).where(Identity.id == uuid.UUID(ctx["identity_id"])))
        ).scalar_one()
        assert wm.last_seen_deleted_at >= ident.deleted_at
        assert wm.last_error == "job.failed_permanent"
