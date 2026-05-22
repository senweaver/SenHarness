"""Unit: ``record_usage`` / ``record_usage_batch`` happy + defensive paths."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from app.db.models.audit import AuditEvent
from app.db.models.skill_usage import SkillUsage, SkillUsageEventKind
from app.db.models.skills import SkillPack, SkillPackSource
from app.repositories.skill_usage import SkillUsageRepository
from app.services import skill_usage as svc

pytestmark = pytest.mark.asyncio


async def _make_pack(db_session, workspace) -> SkillPack:
    pack = SkillPack(
        workspace_id=workspace.id,
        slug=f"sk-{uuid.uuid4().hex[:8]}",
        name="Test pack",
        version="0.1.0",
        manifest_json={},
        metadata_json={},
        source=SkillPackSource.WORKSPACE,
    )
    db_session.add(pack)
    await db_session.flush([pack])
    return pack


async def test_record_usage_happy_path(db_session, workspace, identity):
    pack = await _make_pack(db_session, workspace)
    run_id = uuid.uuid4()
    session_id = uuid.uuid4()

    # session_id has FK to sessions; insert a minimal session row.
    from sqlalchemy import text

    await db_session.execute(
        text(
            "INSERT INTO sessions (id, workspace_id, kind, owner_identity_id, "
            "title, title_source, state, message_count, metadata_json) "
            "VALUES (:id, :ws, 'p2p', :uid, 'seed', 'auto_truncate', 'active', "
            "0, '{}'::jsonb)"
        ),
        {"id": session_id, "ws": workspace.id, "uid": identity.id},
    )

    row = await svc.record_usage(
        db_session,
        workspace_id=workspace.id,
        pack_id=pack.id,
        run_id=run_id,
        session_id=session_id,
        agent_id=None,
        identity_id=identity.id,
        event_kind=SkillUsageEventKind.READ_FULL,
    )
    assert row is not None
    assert row.event_kind == SkillUsageEventKind.READ_FULL
    assert row.workspace_id == workspace.id

    rows = await SkillUsageRepository(db_session).list_for_run(
        workspace_id=workspace.id, run_id=run_id
    )
    assert len(rows) == 1

    audits = (
        (
            await db_session.execute(
                select(AuditEvent).where(
                    AuditEvent.action == "skill.usage_recorded",
                    AuditEvent.resource_id == pack.id,
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(audits) == 1
    assert audits[0].metadata_json["event_kind"] == "read_full"


async def test_record_usage_silent_when_pack_missing(db_session, workspace, identity):
    """Defensive path: a pack id from another workspace is not raised on."""
    bogus_id = uuid.uuid4()
    session_id = uuid.uuid4()
    from sqlalchemy import text

    await db_session.execute(
        text(
            "INSERT INTO sessions (id, workspace_id, kind, owner_identity_id, "
            "title, title_source, state, message_count, metadata_json) "
            "VALUES (:id, :ws, 'p2p', :uid, 'seed', 'auto_truncate', 'active', "
            "0, '{}'::jsonb)"
        ),
        {"id": session_id, "ws": workspace.id, "uid": identity.id},
    )

    row = await svc.record_usage(
        db_session,
        workspace_id=workspace.id,
        pack_id=bogus_id,
        run_id=uuid.uuid4(),
        session_id=session_id,
        agent_id=None,
        identity_id=identity.id,
        event_kind=SkillUsageEventKind.INJECTED,
    )
    assert row is None

    count = (await db_session.execute(select(SkillUsage))).scalars().all()
    assert len(count) == 0


async def test_record_usage_batch_writes_n_rows_one_audit(db_session, workspace, identity):
    p1 = await _make_pack(db_session, workspace)
    p2 = await _make_pack(db_session, workspace)
    bogus = uuid.uuid4()  # cross-workspace skip path
    run_id = uuid.uuid4()
    session_id = uuid.uuid4()
    from sqlalchemy import text

    await db_session.execute(
        text(
            "INSERT INTO sessions (id, workspace_id, kind, owner_identity_id, "
            "title, title_source, state, message_count, metadata_json) "
            "VALUES (:id, :ws, 'p2p', :uid, 'seed', 'auto_truncate', 'active', "
            "0, '{}'::jsonb)"
        ),
        {"id": session_id, "ws": workspace.id, "uid": identity.id},
    )

    rows = await svc.record_usage_batch(
        db_session,
        workspace_id=workspace.id,
        run_id=run_id,
        session_id=session_id,
        agent_id=None,
        identity_id=identity.id,
        event_kind=SkillUsageEventKind.INJECTED,
        pack_ids=[p1.id, p2.id, bogus],
    )
    assert len(rows) == 2

    audits = (
        (
            await db_session.execute(
                select(AuditEvent).where(
                    AuditEvent.action == "skill.usage_batch_recorded",
                    AuditEvent.resource_id == run_id,
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(audits) == 1
    assert audits[0].metadata_json["batch_size"] == 2
    assert audits[0].metadata_json["skipped_count"] == 1
