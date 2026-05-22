"""Unit: ``skill_verifier.verify_skill_version`` (M2.4)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest

from app.db.models.skill_pack_version import SkillPackVersionState
from app.repositories.session_artifact import SessionArtifactRepository
from app.repositories.skill_pack_version import SkillPackVersionRepository
from app.repositories.skills import SkillPackRepository
from app.services import session as session_svc
from app.services import skill_verifier as verifier_svc
from app.services import skill_version as skill_version_svc

pytestmark = pytest.mark.asyncio


async def _make_pack(db, *, workspace_id, slug=None):
    pack = await SkillPackRepository(db).create(
        workspace_id=workspace_id,
        slug=slug or f"sk-{uuid.uuid4().hex[:6]}",
        name="Test pack",
        description="x",
        version="0.1.0",
        publisher=None,
        signature=None,
        manifest_json={},
        enabled=True,
        metadata_json={},
        created_by=None,
    )
    await db.flush()
    return pack


async def _seed_active_v1(db, *, workspace_id, pack, identity, body="V1"):
    v1 = await skill_version_svc.create_version(
        db,
        workspace_id=workspace_id,
        pack_id=pack.id,
        content_md=body,
        files=None,
        created_by="user",
        creator_identity_id=identity.id,
    )
    await skill_version_svc.activate_version(
        db,
        workspace_id=workspace_id,
        version_id=v1.id,
        actor_identity_id=identity.id,
        reason="seed",
    )
    return v1


async def _propose_v2(db, *, workspace_id, pack, identity, body="V2"):
    return await skill_version_svc.create_version(
        db,
        workspace_id=workspace_id,
        pack_id=pack.id,
        content_md=body,
        files=None,
        created_by="evolver",
        creator_identity_id=identity.id,
    )


async def _seed_artifacts(db, *, workspace, identity, pack, count: int) -> list[uuid.UUID]:
    sess = await session_svc.create_session(
        db,
        workspace_id=workspace.id,
        owner_identity_id=identity.id,
    )
    repo = SessionArtifactRepository(db)
    ids: list[uuid.UUID] = []
    for i in range(count):
        finished = datetime.now(UTC).replace(tzinfo=None) - timedelta(minutes=i * 5 + 1)
        row = await repo.create(
            workspace_id=workspace.id,
            run_id=uuid.uuid4(),
            session_id=sess.id,
            agent_id=None,
            identity_id=identity.id,
            user_text_hash="0" * 64,
            turns_json=[
                {"role": "user", "iteration": 0, "text": "do thing"},
                {"role": "assistant", "iteration": 1, "text": "done"},
            ],
            injected_skill_pack_ids=[str(pack.id)],
            invoked_tools=[],
            iteration_count=1,
            final_outcome="success",
            error_kind=None,
            goal_alignment_avg=None,
            finished_at=finished,
        )
        ids.append(row.id)
    return ids


def _replay_returning(*, old: int, new: int, failed: bool = False):
    """Build a stub coroutine for ``replay_judge_with_skill_swap``."""

    async def _stub(_db, *, workspace_id, artifact, pack_slug, old_content, new_content):
        _ = (workspace_id, pack_slug, old_content, new_content)
        return verifier_svc.ArtifactReplayPair(
            artifact_id=artifact.id, old_score=old, new_score=new, failed=failed
        )

    return _stub


async def test_verify_accepts_when_delta_meets_threshold(db_session, workspace, identity):
    pack = await _make_pack(db_session, workspace_id=workspace.id)
    await _seed_active_v1(db_session, workspace_id=workspace.id, pack=pack, identity=identity)
    v2 = await _propose_v2(db_session, workspace_id=workspace.id, pack=pack, identity=identity)
    await _seed_artifacts(db_session, workspace=workspace, identity=identity, pack=pack, count=4)

    with patch.object(
        verifier_svc,
        "replay_judge_with_skill_swap",
        _replay_returning(old=0, new=1),
    ):
        result = await verifier_svc.verify_skill_version(
            db_session,
            workspace_id=workspace.id,
            version_id=v2.id,
        )

    assert result.status == "accepted"
    assert result.score_delta == pytest.approx(1.0)
    assert result.replayed_artifacts == 4

    refreshed = await SkillPackVersionRepository(db_session).get(v2.id)
    assert refreshed is not None
    assert refreshed.state == SkillPackVersionState.ACCEPTED
    assert refreshed.validation_results.get("status") == "accepted"
    assert refreshed.judge_score == pytest.approx(1.0)


async def test_verify_rejects_when_delta_below_threshold(db_session, workspace, identity):
    pack = await _make_pack(db_session, workspace_id=workspace.id)
    await _seed_active_v1(db_session, workspace_id=workspace.id, pack=pack, identity=identity)
    v2 = await _propose_v2(db_session, workspace_id=workspace.id, pack=pack, identity=identity)
    await _seed_artifacts(db_session, workspace=workspace, identity=identity, pack=pack, count=3)

    with patch.object(
        verifier_svc,
        "replay_judge_with_skill_swap",
        _replay_returning(old=1, new=0),
    ):
        result = await verifier_svc.verify_skill_version(
            db_session,
            workspace_id=workspace.id,
            version_id=v2.id,
        )

    assert result.status == "rejected"
    assert result.score_delta == pytest.approx(-1.0)
    refreshed = await SkillPackVersionRepository(db_session).get(v2.id)
    assert refreshed is not None
    assert refreshed.state == SkillPackVersionState.REJECTED
    assert refreshed.validation_results.get("status") == "rejected"


async def test_verify_skipped_when_artifacts_below_min(db_session, workspace, identity):
    pack = await _make_pack(db_session, workspace_id=workspace.id)
    await _seed_active_v1(db_session, workspace_id=workspace.id, pack=pack, identity=identity)
    v2 = await _propose_v2(db_session, workspace_id=workspace.id, pack=pack, identity=identity)
    # Only 1 artifact — default min_replay_artifacts is 3.
    await _seed_artifacts(db_session, workspace=workspace, identity=identity, pack=pack, count=1)

    # No replay should be invoked when the threshold gate fails — make the
    # stub blow up so we'd notice if the gate didn't fire.
    async def _explode(*args, **kwargs):
        raise AssertionError("replay should not be called when below min")

    with patch.object(verifier_svc, "replay_judge_with_skill_swap", _explode):
        result = await verifier_svc.verify_skill_version(
            db_session,
            workspace_id=workspace.id,
            version_id=v2.id,
        )

    assert result.status == "skipped_insufficient"
    assert result.replayed_artifacts == 0
    refreshed = await SkillPackVersionRepository(db_session).get(v2.id)
    assert refreshed is not None
    assert refreshed.state == SkillPackVersionState.ACCEPTED
    assert refreshed.validation_results.get("skipped") is True


async def test_verify_errors_become_rejected_with_status_errored(db_session, workspace, identity):
    pack = await _make_pack(db_session, workspace_id=workspace.id)
    await _seed_active_v1(db_session, workspace_id=workspace.id, pack=pack, identity=identity)
    v2 = await _propose_v2(db_session, workspace_id=workspace.id, pack=pack, identity=identity)
    await _seed_artifacts(db_session, workspace=workspace, identity=identity, pack=pack, count=3)

    async def _crash(*args, **kwargs):
        raise RuntimeError("aux tier blew up")

    with patch.object(verifier_svc, "replay_judge_with_skill_swap", _crash):
        result = await verifier_svc.verify_skill_version(
            db_session,
            workspace_id=workspace.id,
            version_id=v2.id,
        )

    assert result.status == "errored"
    refreshed = await SkillPackVersionRepository(db_session).get(v2.id)
    assert refreshed is not None
    assert refreshed.state == SkillPackVersionState.REJECTED
    assert refreshed.validation_results.get("status") == "errored"
    assert "aux tier blew up" in (refreshed.validation_results.get("error") or "")


async def test_verify_refuses_already_active_version(db_session, workspace, identity):
    pack = await _make_pack(db_session, workspace_id=workspace.id)
    v1 = await _seed_active_v1(db_session, workspace_id=workspace.id, pack=pack, identity=identity)
    with pytest.raises(verifier_svc.VerifierAlreadyTerminal):
        await verifier_svc.verify_skill_version(
            db_session,
            workspace_id=workspace.id,
            version_id=v1.id,
        )
