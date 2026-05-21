"""Physical-purge unit tests (M0.11).

Walks ``physically_purge_expired`` against the DB through a series of
seeded ``session_artifacts`` rows. The tests intentionally manipulate
``deleted_at`` directly (instead of going through the cascade) so the
window math is the only thing under test.
"""

from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
from sqlalchemy import select, text

from app.core.security import utcnow_naive
from app.db.models.email_verification import EmailVerificationToken
from app.db.models.session_artifact import SessionArtifact
from app.services import retention as retention_svc
from app.services.system_settings import (
    RetentionSettings,
    SystemSettingKey,
    set_system_setting,
)

pytestmark = pytest.mark.asyncio


async def _make_session(db_session, workspace, identity) -> uuid.UUID:
    sid = uuid.uuid4()
    await db_session.execute(
        text(
            "INSERT INTO sessions (id, workspace_id, kind, owner_identity_id, "
            "title, title_source, state, message_count, metadata_json) "
            "VALUES (:id, :ws, 'p2p', :uid, 'seed', 'auto_truncate', 'active', "
            "0, '{}'::jsonb)"
        ),
        {"id": sid, "ws": workspace.id, "uid": identity.id},
    )
    return sid


async def _seed_old_artifact(
    db_session, workspace, identity, *, deleted_days_ago: int
) -> SessionArtifact:
    sid = await _make_session(db_session, workspace, identity)
    artifact = SessionArtifact(
        workspace_id=workspace.id,
        run_id=uuid.uuid4(),
        session_id=sid,
        identity_id=identity.id,
        user_text_hash="0" * 64,
        turns_json=[],
        injected_skill_pack_ids=[],
        invoked_tools=[],
        iteration_count=0,
        final_outcome="success",
        finished_at=utcnow_naive(),
        deleted_at=utcnow_naive() - timedelta(days=deleted_days_ago),
    )
    db_session.add(artifact)
    await db_session.flush()
    return artifact


async def test_dry_run_does_not_delete(db_session, workspace, identity):
    artifact = await _seed_old_artifact(
        db_session, workspace, identity, deleted_days_ago=60
    )

    report = await retention_svc.physically_purge_expired(
        db_session, dry_run=True
    )
    sess_artifact_rep = report["session_artifacts"]
    assert sess_artifact_rep.candidates >= 1
    assert sess_artifact_rep.deleted == 0

    surviving = (
        await db_session.execute(
            select(SessionArtifact).where(SessionArtifact.id == artifact.id)
        )
    ).scalar_one_or_none()
    assert surviving is not None


async def test_enabled_purge_actually_deletes(db_session, workspace, identity):
    artifact = await _seed_old_artifact(
        db_session, workspace, identity, deleted_days_ago=60
    )
    fresh = await _seed_old_artifact(
        db_session, workspace, identity, deleted_days_ago=1
    )

    report = await retention_svc.physically_purge_expired(
        db_session, dry_run=False
    )
    rep = report["session_artifacts"]
    assert rep.candidates >= 1
    assert rep.deleted >= 1

    expired = (
        await db_session.execute(
            select(SessionArtifact).where(SessionArtifact.id == artifact.id)
        )
    ).scalar_one_or_none()
    assert expired is None

    # Within retention → must not be touched.
    survivor = (
        await db_session.execute(
            select(SessionArtifact).where(SessionArtifact.id == fresh.id)
        )
    ).scalar_one()
    assert survivor.deleted_at is not None


async def test_per_table_days_override_takes_effect(
    db_session, workspace, identity
):
    """Override ``session_artifacts`` to 90 days and prove a 60-day-old
    soft-deleted row no longer purges.
    """
    await set_system_setting(
        db_session,
        SystemSettingKey.RETENTION,
        RetentionSettings(
            default_days=30,
            per_table_days={"session_artifacts": 90},
            physical_purge_enabled=True,
        ).model_dump(),
    )
    await db_session.flush()

    artifact = await _seed_old_artifact(
        db_session, workspace, identity, deleted_days_ago=60
    )
    report = await retention_svc.physically_purge_expired(
        db_session, dry_run=False
    )
    assert report["session_artifacts"].candidates == 0
    surviving = (
        await db_session.execute(
            select(SessionArtifact).where(SessionArtifact.id == artifact.id)
        )
    ).scalar_one_or_none()
    assert surviving is not None


async def test_default_days_apply_when_not_overridden(
    db_session, workspace, identity
):
    """Custom override leaves other tables on ``default_days = 30``."""
    await set_system_setting(
        db_session,
        SystemSettingKey.RETENTION,
        RetentionSettings(
            default_days=30,
            per_table_days={"workspace_creation_logs": 365},
            physical_purge_enabled=True,
        ).model_dump(),
    )
    await db_session.flush()

    artifact = await _seed_old_artifact(
        db_session, workspace, identity, deleted_days_ago=45
    )
    report = await retention_svc.physically_purge_expired(
        db_session, dry_run=False
    )
    assert report["session_artifacts"].candidates >= 1
    assert report["session_artifacts"].deleted >= 1
    expired = (
        await db_session.execute(
            select(SessionArtifact).where(SessionArtifact.id == artifact.id)
        )
    ).scalar_one_or_none()
    assert expired is None


async def test_purge_skips_tables_without_soft_delete(db_session, identity):
    """``email_verification_tokens`` has no ``deleted_at`` column;
    physical purge must label it ``no_soft_delete_column`` and not
    touch the row.
    """
    tok = EmailVerificationToken(
        identity_id=identity.id,
        token_hash=uuid.uuid4().hex + uuid.uuid4().hex,
        expires_at=utcnow_naive() + timedelta(hours=1),
    )
    db_session.add(tok)
    await db_session.flush()

    report = await retention_svc.physically_purge_expired(
        db_session, dry_run=False
    )
    rep = report["email_verification_tokens"]
    assert rep.skipped_reason == "no_soft_delete_column"
    assert rep.candidates == 0
    assert rep.deleted == 0

    surviving = (
        await db_session.execute(
            select(EmailVerificationToken).where(
                EmailVerificationToken.id == tok.id
            )
        )
    ).scalar_one_or_none()
    assert surviving is not None
