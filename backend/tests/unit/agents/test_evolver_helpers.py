"""Unit tests for the M2.2 evolver helper tools.

* ``list_session_artifacts`` returns only judged + low-scoring rows
  scoped to the active workspace.
* ``read_skill_pack`` returns metadata + ACTIVE content_md, truncated
  at the documented limit.
* ``mark_skip`` writes the audit row and signals ``stop=True`` so the
  evolver loop exits cleanly.
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from datetime import timedelta
from pathlib import Path

import pytest
from sqlalchemy import select

from app.agents.tools._context import ToolRunContext, set_context
from app.agents.tools.evolver_helpers import (
    AUDIT_MARKED_SKIP,
    READ_SKILL_PACK_TRUNCATE_CHARS,
    ListSessionArtifactsArgs,
    MarkSkipArgs,
    ReadSkillPackArgs,
    run_list_session_artifacts,
    run_mark_skip,
    run_read_skill_pack,
)
from app.core.security import utcnow_naive
from app.db.models.audit import AuditEvent
from app.db.models.session_artifact import SessionArtifact
from app.db.models.skills import SkillPackState
from app.repositories.skills import SkillPackRepository
from app.services import skill_version as version_svc

pytestmark = pytest.mark.asyncio


def _set_ctx(workspace, identity):
    set_context(
        ToolRunContext(
            run_id=uuid.uuid4(),
            workspace_id=workspace.id,
            session_id=uuid.uuid4(),
            identity_id=identity.id,
            agent_id=uuid.uuid4(),
            scratch_base=Path("/tmp"),
        )
    )


def _patched_factory(db_session):
    @asynccontextmanager
    async def _factory():
        yield db_session

    return _factory


async def _make_session(db_session, *, workspace, identity):
    """Insert a parent ``sessions`` row so ``session_artifacts`` FK is happy."""
    from app.db.models.session import Session as SessionModel

    sess = SessionModel(
        id=uuid.uuid4(),
        workspace_id=workspace.id,
        owner_identity_id=identity.id,
        title="evolver-test-session",
    )
    db_session.add(sess)
    await db_session.flush()
    return sess


async def _make_artifact(
    db_session,
    *,
    workspace,
    identity,
    session,
    judge_score: float | None,
    finished_at,
    error_kind: str | None = None,
    final_outcome: str = "error",
):
    artifact = SessionArtifact(
        workspace_id=workspace.id,
        run_id=uuid.uuid4(),
        session_id=session.id,
        agent_id=None,
        identity_id=identity.id,
        user_text_hash="0" * 64,
        turns_json=[],
        injected_skill_pack_ids=[],
        invoked_tools=["calculator"],
        iteration_count=2,
        final_outcome=final_outcome,
        error_kind=error_kind,
        judge_score=judge_score,
        finished_at=finished_at,
    )
    db_session.add(artifact)
    await db_session.flush()
    return artifact


# ─── list_session_artifacts ──────────────────────────────────
async def test_list_session_artifacts_returns_only_low_scoring(
    db_session, workspace, identity, monkeypatch
):
    _set_ctx(workspace, identity)
    monkeypatch.setattr(
        "app.agents.tools.evolver_helpers.get_session_factory",
        lambda: _patched_factory(db_session),
    )

    sess = await _make_session(db_session, workspace=workspace, identity=identity)
    now = utcnow_naive()
    a_failed = await _make_artifact(
        db_session,
        workspace=workspace,
        identity=identity,
        session=sess,
        judge_score=-1.0,
        finished_at=now - timedelta(hours=1),
        error_kind="tool_loop",
    )
    a_partial = await _make_artifact(
        db_session,
        workspace=workspace,
        identity=identity,
        session=sess,
        judge_score=0.0,
        finished_at=now - timedelta(hours=2),
        error_kind=None,
        final_outcome="partial",
    )
    a_success = await _make_artifact(
        db_session,
        workspace=workspace,
        identity=identity,
        session=sess,
        judge_score=1.0,
        finished_at=now - timedelta(hours=3),
        error_kind=None,
        final_outcome="success",
    )
    a_unjudged = await _make_artifact(
        db_session,
        workspace=workspace,
        identity=identity,
        session=sess,
        judge_score=None,
        finished_at=now - timedelta(hours=4),
        error_kind=None,
    )

    result = await run_list_session_artifacts(ListSessionArtifactsArgs(limit=20))
    assert result["status"] == "ok"
    ids = {item["artifact_id"] for item in result["items"]}
    assert str(a_failed.id) in ids
    assert str(a_partial.id) in ids
    assert str(a_success.id) not in ids, "1.0 score must not appear when score_max=0"
    assert str(a_unjudged.id) not in ids, "unjudged rows must not appear"

    failed_item = next(i for i in result["items"] if i["artifact_id"] == str(a_failed.id))
    assert failed_item["error_kind_hint"] == "tool_loop"
    assert "user_text" not in failed_item
    assert "turns_json" not in failed_item


async def test_list_session_artifacts_respects_since_days(
    db_session, workspace, identity, monkeypatch
):
    _set_ctx(workspace, identity)
    monkeypatch.setattr(
        "app.agents.tools.evolver_helpers.get_session_factory",
        lambda: _patched_factory(db_session),
    )

    sess = await _make_session(db_session, workspace=workspace, identity=identity)
    now = utcnow_naive()
    recent = await _make_artifact(
        db_session,
        workspace=workspace,
        identity=identity,
        session=sess,
        judge_score=-1.0,
        finished_at=now - timedelta(days=2),
    )
    ancient = await _make_artifact(
        db_session,
        workspace=workspace,
        identity=identity,
        session=sess,
        judge_score=-1.0,
        finished_at=now - timedelta(days=30),
    )

    result = await run_list_session_artifacts(
        ListSessionArtifactsArgs(limit=10, since_days=7)
    )
    ids = {item["artifact_id"] for item in result["items"]}
    assert str(recent.id) in ids
    assert str(ancient.id) not in ids


# ─── read_skill_pack ─────────────────────────────────────────
async def test_read_skill_pack_returns_active_content(
    db_session, workspace, identity, monkeypatch
):
    _set_ctx(workspace, identity)
    monkeypatch.setattr(
        "app.agents.tools.evolver_helpers.get_session_factory",
        lambda: _patched_factory(db_session),
    )

    pack = await SkillPackRepository(db_session).create(
        workspace_id=workspace.id,
        slug="readable",
        name="Readable",
        description="d",
        version="1.0.0",
        publisher=None,
        signature=None,
        manifest_json={},
        enabled=True,
        metadata_json={},
        created_by=identity.id,
        state=SkillPackState.ACTIVE,
    )
    await db_session.flush()
    version = await version_svc.create_version(
        db_session,
        workspace_id=workspace.id,
        pack_id=pack.id,
        content_md="# Hello\nbody",
        files={},
        created_by="user",
        creator_identity_id=identity.id,
    )
    await version_svc.activate_version(
        db_session,
        workspace_id=workspace.id,
        version_id=version.id,
        actor_identity_id=identity.id,
    )

    result = await run_read_skill_pack(ReadSkillPackArgs(pack_id=pack.id))
    assert result["status"] == "ok"
    assert result["slug"] == "readable"
    assert result["state"] == SkillPackState.ACTIVE.value
    assert result["content_md"].startswith("# Hello")
    assert result["content_truncated"] is False
    assert result["active_version_no"] == version.version_no


async def test_read_skill_pack_truncates_long_body(
    db_session, workspace, identity, monkeypatch
):
    _set_ctx(workspace, identity)
    monkeypatch.setattr(
        "app.agents.tools.evolver_helpers.get_session_factory",
        lambda: _patched_factory(db_session),
    )

    pack = await SkillPackRepository(db_session).create(
        workspace_id=workspace.id,
        slug="big-pack",
        name="Big",
        description=None,
        version="1.0.0",
        publisher=None,
        signature=None,
        manifest_json={},
        enabled=True,
        metadata_json={},
        created_by=identity.id,
        state=SkillPackState.ACTIVE,
    )
    await db_session.flush()
    long_body = "x" * (READ_SKILL_PACK_TRUNCATE_CHARS + 500)
    version = await version_svc.create_version(
        db_session,
        workspace_id=workspace.id,
        pack_id=pack.id,
        content_md=long_body,
        files={},
        created_by="user",
        creator_identity_id=identity.id,
    )
    await version_svc.activate_version(
        db_session,
        workspace_id=workspace.id,
        version_id=version.id,
        actor_identity_id=identity.id,
    )

    result = await run_read_skill_pack(ReadSkillPackArgs(pack_id=pack.id))
    assert result["content_truncated"] is True
    assert len(result["content_md"]) == READ_SKILL_PACK_TRUNCATE_CHARS


async def test_read_skill_pack_unknown_pack_rejects(
    db_session, workspace, identity, monkeypatch
):
    _set_ctx(workspace, identity)
    monkeypatch.setattr(
        "app.agents.tools.evolver_helpers.get_session_factory",
        lambda: _patched_factory(db_session),
    )
    result = await run_read_skill_pack(ReadSkillPackArgs(pack_id=uuid.uuid4()))
    assert result["status"] == "rejected"
    assert result["code"] == "evolver.pack_not_found"


# ─── mark_skip ───────────────────────────────────────────────
async def test_mark_skip_records_audit_and_returns_stop(
    db_session, workspace, identity, monkeypatch
):
    _set_ctx(workspace, identity)
    monkeypatch.setattr(
        "app.agents.tools.evolver_helpers.get_session_factory",
        lambda: _patched_factory(db_session),
    )

    result = await run_mark_skip(
        MarkSkipArgs(rationale="batch is healthy; no proposals worth filing.")
    )
    assert result["status"] == "skipped"
    assert result["stop"] is True
    assert "no SkillPack proposals" in result["final_message"].lower()

    audits = list(
        (
            await db_session.execute(
                select(AuditEvent).where(
                    AuditEvent.action == AUDIT_MARKED_SKIP,
                    AuditEvent.workspace_id == workspace.id,
                )
            )
        ).scalars()
    )
    assert len(audits) == 1
    assert audits[0].metadata_json["rationale"].startswith("batch is healthy")
