"""DB-backed integration test for the M1.8 cap selection wiring.

Asserts the three observables the M1.8 contract surfaces when a
workspace binds far more SkillPacks than fit inside its cap:

1. ``injected_pack_ids`` length == cap (count cap fired).
2. The ``skill_usage`` table grows by one row per dropped pack with
   ``event_kind == DROPPED_AT_CAP``.
3. Exactly one ``skill.cap_applied`` audit row lands per resolution
   pass.

The pure cap math is exercised in
``tests/unit/services/test_skill_selection.py``; this file pins the
DB-side wiring and the workspace-config override path.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from sqlalchemy import func, select

from app.agents.harness import skills as skills_mod
from app.db.models.audit import AuditEvent
from app.db.models.skill_usage import SkillUsage, SkillUsageEventKind
from app.db.models.skills import SkillPackState
from app.repositories.skills import SkillFileRepository, SkillPackRepository

pytestmark = pytest.mark.asyncio


class _CapturedCapability:
    def __init__(self, *, skills: list[Any], **_: Any) -> None:
        self.skills = list(skills)


class _Skill:
    def __init__(self, *, name: str, description: str, content: str) -> None:
        self.name = name
        self.description = description
        self.content = content


@pytest.fixture(autouse=True)
def _patch_skill_lib(monkeypatch):
    monkeypatch.setattr(skills_mod, "_resolve_skill_dataclass", lambda: _Skill)
    monkeypatch.setattr(
        skills_mod,
        "_instantiate_skills_capability",
        lambda materialized: _CapturedCapability(skills=materialized),
    )
    yield


async def _seed_packs(db_session, *, workspace_id: uuid.UUID, n: int):
    """Seed ``n`` ACTIVE packs whose ``effectiveness_avg`` strictly
    decreases — guarantees a deterministic injection order so the
    surviving id list is predictable.
    """
    repo_packs = SkillPackRepository(db_session)
    repo_files = SkillFileRepository(db_session)
    out = []
    for i in range(n):
        pack = await repo_packs.create(
            workspace_id=workspace_id,
            slug=f"cap-pack-{i:03d}",
            name=f"cap-pack-{i:03d}",
            description=f"description {i}",
            version="0.1.0",
            manifest_json={},
            metadata_json={},
            enabled=True,
            state=SkillPackState.ACTIVE,
            effectiveness_avg=1.0 - (i / 1000.0),
        )
        await repo_files.create(
            workspace_id=workspace_id,
            skill_pack_id=pack.id,
            path="SKILL.md",
            content_md=f"# pack {i}\nbody",
        )
        out.append(pack)
    await db_session.flush()
    return out


async def test_50_packs_capped_to_workspace_limit_writes_drops_and_audit(
    db_session, workspace, identity, agent
):
    workspace.home_config_json = {"skills": {"max_active_injected": 10}}
    await db_session.flush()

    packs = await _seed_packs(db_session, workspace_id=workspace.id, n=50)
    bound_ids = [str(p.id) for p in packs]

    fake_run = uuid.uuid4()
    fake_session = uuid.uuid4()

    cap, injected_ids = await skills_mod.build_skills_capability(
        policy={"skills": bound_ids},
        workspace_id=workspace.id,
        db=db_session,
        run_id=fake_run,
        session_id=fake_session,
        agent_id=agent.id,
        identity_id=identity.id,
    )
    await db_session.flush()

    # 1. cap fired — only 10 packs survive.
    assert cap is not None
    assert len(injected_ids) == 10
    assert len(cap.skills) == 10

    # 2. DROPPED_AT_CAP rows landed for the 40 displaced packs.
    drop_count = (
        await db_session.execute(
            select(func.count())
            .select_from(SkillUsage)
            .where(
                SkillUsage.workspace_id == workspace.id,
                SkillUsage.event_kind == SkillUsageEventKind.DROPPED_AT_CAP,
                SkillUsage.run_id == fake_run,
            )
        )
    ).scalar_one()
    assert drop_count == 40

    # The dropped pack ids must be the 40 lowest-effectiveness packs
    # — packs[10:] in our seeding order.
    dropped_pack_ids = set(
        (
            await db_session.execute(
                select(SkillUsage.pack_id).where(
                    SkillUsage.workspace_id == workspace.id,
                    SkillUsage.event_kind == SkillUsageEventKind.DROPPED_AT_CAP,
                    SkillUsage.run_id == fake_run,
                )
            )
        ).scalars().all()
    )
    expected_dropped = {p.id for p in packs[10:]}
    assert dropped_pack_ids == expected_dropped

    # 3. Exactly one ``skill.cap_applied`` audit row landed for this pass.
    cap_audit_count = (
        await db_session.execute(
            select(func.count())
            .select_from(AuditEvent)
            .where(
                AuditEvent.workspace_id == workspace.id,
                AuditEvent.action == "skill.cap_applied",
            )
        )
    ).scalar_one()
    assert cap_audit_count == 1

    # The audit metadata records the right counts.
    audit_row = (
        await db_session.execute(
            select(AuditEvent).where(
                AuditEvent.workspace_id == workspace.id,
                AuditEvent.action == "skill.cap_applied",
            )
        )
    ).scalar_one()
    assert audit_row.metadata_json["selected_count"] == 10
    assert audit_row.metadata_json["dropped_count"] == 40
    assert audit_row.metadata_json["truncated_by_count"] is True
    assert audit_row.metadata_json["max_active_injected"] == 10


async def test_under_cap_writes_no_drops_and_no_cap_audit(
    db_session, workspace, identity, agent
):
    """When every bound pack fits, neither telemetry channel should fire."""
    packs = await _seed_packs(db_session, workspace_id=workspace.id, n=3)
    bound_ids = [str(p.id) for p in packs]

    cap, injected_ids = await skills_mod.build_skills_capability(
        policy={"skills": bound_ids},
        workspace_id=workspace.id,
        db=db_session,
        run_id=uuid.uuid4(),
        session_id=uuid.uuid4(),
        agent_id=agent.id,
        identity_id=identity.id,
    )
    await db_session.flush()

    assert cap is not None
    assert len(injected_ids) == 3

    drop_count = (
        await db_session.execute(
            select(func.count())
            .select_from(SkillUsage)
            .where(
                SkillUsage.workspace_id == workspace.id,
                SkillUsage.event_kind == SkillUsageEventKind.DROPPED_AT_CAP,
            )
        )
    ).scalar_one()
    assert drop_count == 0

    cap_audit_count = (
        await db_session.execute(
            select(func.count())
            .select_from(AuditEvent)
            .where(
                AuditEvent.workspace_id == workspace.id,
                AuditEvent.action == "skill.cap_applied",
            )
        )
    ).scalar_one()
    assert cap_audit_count == 0


async def test_record_drops_false_skips_audit_writes(
    db_session, workspace, identity, agent
):
    """The escape hatch lets unit tests opt out of telemetry side effects."""
    workspace.home_config_json = {"skills": {"max_active_injected": 2}}
    await db_session.flush()

    packs = await _seed_packs(db_session, workspace_id=workspace.id, n=5)
    bound_ids = [str(p.id) for p in packs]

    cap, injected_ids = await skills_mod.build_skills_capability(
        policy={"skills": bound_ids},
        workspace_id=workspace.id,
        db=db_session,
        record_drops=False,
        run_id=uuid.uuid4(),
        session_id=uuid.uuid4(),
        agent_id=agent.id,
        identity_id=identity.id,
    )
    await db_session.flush()

    assert cap is not None
    assert len(injected_ids) == 2

    drop_count = (
        await db_session.execute(
            select(func.count())
            .select_from(SkillUsage)
            .where(
                SkillUsage.workspace_id == workspace.id,
                SkillUsage.event_kind == SkillUsageEventKind.DROPPED_AT_CAP,
            )
        )
    ).scalar_one()
    assert drop_count == 0

    # Note: the cap_applied audit STILL fires because the truncation
    # itself is observable regardless of telemetry rows. ``record_drops``
    # only gates the per-pack ``skill_usage`` write, not the
    # workspace-level audit signal.
    cap_audit_count = (
        await db_session.execute(
            select(func.count())
            .select_from(AuditEvent)
            .where(
                AuditEvent.workspace_id == workspace.id,
                AuditEvent.action == "skill.cap_applied",
            )
        )
    ).scalar_one()
    assert cap_audit_count == 1
