"""Unit: ``aggregate_pack_stats`` math + edges."""

from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
from sqlalchemy import text

from app.core.security import utcnow_naive
from app.db.models.skill_usage import SkillUsage, SkillUsageEventKind
from app.db.models.skills import SkillPack, SkillPackSource
from app.services import skill_usage as svc

pytestmark = pytest.mark.asyncio


async def _make_pack(db_session, workspace) -> SkillPack:
    pack = SkillPack(
        workspace_id=workspace.id,
        slug=f"sk-{uuid.uuid4().hex[:8]}",
        name="P",
        version="0.1.0",
        manifest_json={},
        metadata_json={},
        source=SkillPackSource.WORKSPACE,
    )
    db_session.add(pack)
    await db_session.flush([pack])
    return pack


async def _make_session(db_session, workspace_id, identity_id) -> uuid.UUID:
    sid = uuid.uuid4()
    await db_session.execute(
        text(
            "INSERT INTO sessions (id, workspace_id, kind, owner_identity_id, "
            "title, title_source, state, message_count, metadata_json) "
            "VALUES (:id, :ws, 'p2p', :uid, 'seed', 'auto_truncate', 'active', "
            "0, '{}'::jsonb)"
        ),
        {"id": sid, "ws": workspace_id, "uid": identity_id},
    )
    return sid


def _make_row(*, ws_id, pack_id, sid, identity_id, kind, score=None, created_at=None) -> SkillUsage:
    row = SkillUsage(
        workspace_id=ws_id,
        pack_id=pack_id,
        run_id=uuid.uuid4(),
        session_id=sid,
        agent_id=None,
        identity_id=identity_id,
        event_kind=kind,
        contribution_score=score,
    )
    if created_at is not None:
        row.created_at = created_at
    return row


async def test_aggregate_returns_count_kinds_avg(db_session, workspace, identity):
    pack = await _make_pack(db_session, workspace)
    sid = await _make_session(db_session, workspace.id, identity.id)
    db_session.add_all(
        [
            _make_row(
                ws_id=workspace.id,
                pack_id=pack.id,
                sid=sid,
                identity_id=identity.id,
                kind=SkillUsageEventKind.INJECTED,
            ),
            _make_row(
                ws_id=workspace.id,
                pack_id=pack.id,
                sid=sid,
                identity_id=identity.id,
                kind=SkillUsageEventKind.INJECTED,
            ),
            _make_row(
                ws_id=workspace.id,
                pack_id=pack.id,
                sid=sid,
                identity_id=identity.id,
                kind=SkillUsageEventKind.READ_FULL,
                score=0.8,
            ),
            _make_row(
                ws_id=workspace.id,
                pack_id=pack.id,
                sid=sid,
                identity_id=identity.id,
                kind=SkillUsageEventKind.USED_IN_TOOL,
                score=0.4,
            ),
        ]
    )
    await db_session.flush()

    since = utcnow_naive() - timedelta(days=30)
    stats = await svc.aggregate_pack_stats(
        db_session, workspace_id=workspace.id, pack_id=pack.id, since=since
    )

    assert stats["use_count"] == 4
    assert stats["last_used_at"] is not None
    assert stats["contribution_avg"] == pytest.approx(0.6)
    assert stats["by_kind"] == {
        "injected": 2,
        "read_full": 1,
        "used_in_tool": 1,
    }


async def test_since_boundary_excludes_old_rows(db_session, workspace, identity):
    pack = await _make_pack(db_session, workspace)
    sid = await _make_session(db_session, workspace.id, identity.id)
    now = utcnow_naive()
    db_session.add(
        _make_row(
            ws_id=workspace.id,
            pack_id=pack.id,
            sid=sid,
            identity_id=identity.id,
            kind=SkillUsageEventKind.INJECTED,
            created_at=now - timedelta(days=40),
        )
    )
    db_session.add(
        _make_row(
            ws_id=workspace.id,
            pack_id=pack.id,
            sid=sid,
            identity_id=identity.id,
            kind=SkillUsageEventKind.READ_FULL,
            created_at=now - timedelta(days=2),
        )
    )
    await db_session.flush()

    since = now - timedelta(days=30)
    stats = await svc.aggregate_pack_stats(
        db_session, workspace_id=workspace.id, pack_id=pack.id, since=since
    )
    assert stats["use_count"] == 1
    assert stats["by_kind"] == {"read_full": 1}


async def test_aggregate_empty_returns_none_avg(db_session, workspace):
    pack = await _make_pack(db_session, workspace)
    since = utcnow_naive() - timedelta(days=30)
    stats = await svc.aggregate_pack_stats(
        db_session, workspace_id=workspace.id, pack_id=pack.id, since=since
    )
    assert stats["use_count"] == 0
    assert stats["last_used_at"] is None
    assert stats["contribution_avg"] is None
    assert stats["by_kind"] == {}
