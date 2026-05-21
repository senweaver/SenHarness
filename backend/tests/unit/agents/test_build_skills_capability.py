"""Unit tests for the DB-backed runtime SkillPack discovery (M1.7).

The function under test resolves bound pack ids straight from the
agent's policy slot, runs them through
:meth:`SkillPackRepository.list_active`, materialises the resolved rows
into runtime ``Skill`` objects, and returns ``(capability, ids)``. Each
test pins one branch of the contract: empty bind, malformed UUIDs,
state filtering, pin override, tombstone exclusion, repository
failure, capability instantiation failure.

DB-backed fixtures rely on the project-wide ``db_session`` fixture, so
the suite is skipped automatically on dev machines without Postgres.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

import pytest

from app.agents.harness import skills as skills_mod
from app.db.models.skills import SkillPackState
from app.repositories.skills import SkillFileRepository, SkillPackRepository

pytestmark = pytest.mark.asyncio


class _CapturedCapability:
    """Stand-in for ``SkillsCapability`` used in tests.

    We monkeypatch the import inside :mod:`app.agents.harness.skills` so
    the unit tests don't take a hard dependency on the runtime library
    being installed (it is, but tests that depend on third-party DSL
    behaviour are brittle). The captured ``skills`` list is the
    materialised pack content the runner would have shipped.
    """

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


async def _create_pack(
    db_session,
    *,
    workspace_id: uuid.UUID,
    slug: str,
    name: str | None = None,
    state: SkillPackState = SkillPackState.ACTIVE,
    pinned: bool = False,
    content_md: str = "# default content",
    description: str | None = "test pack",
):
    pack = await SkillPackRepository(db_session).create(
        workspace_id=workspace_id,
        slug=slug,
        name=name or slug,
        description=description,
        version="0.1.0",
        manifest_json={},
        metadata_json={},
        enabled=True,
        state=state,
        pinned=pinned,
    )
    await SkillFileRepository(db_session).create(
        workspace_id=workspace_id,
        skill_pack_id=pack.id,
        path="SKILL.md",
        content_md=content_md,
    )
    await db_session.flush()
    return pack


async def test_policy_none_returns_empty(db_session, workspace):
    cap, ids = await skills_mod.build_skills_capability(
        policy=None, workspace_id=workspace.id, db=db_session
    )
    assert cap is None
    assert ids == []


async def test_skills_missing_returns_empty(db_session, workspace):
    cap, ids = await skills_mod.build_skills_capability(
        policy={"autonomy_level": "l2"},
        workspace_id=workspace.id,
        db=db_session,
    )
    assert cap is None
    assert ids == []


async def test_empty_skills_list_returns_empty(db_session, workspace):
    cap, ids = await skills_mod.build_skills_capability(
        policy={"skills": []},
        workspace_id=workspace.id,
        db=db_session,
    )
    assert cap is None
    assert ids == []


async def test_malformed_uuid_logged_and_filtered(
    db_session, workspace, caplog
):
    pack = await _create_pack(
        db_session, workspace_id=workspace.id, slug="ok-pack"
    )
    with caplog.at_level(logging.WARNING):
        cap, ids = await skills_mod.build_skills_capability(
            policy={"skills": ["not-a-uuid", str(pack.id)]},
            workspace_id=workspace.id,
            db=db_session,
        )
    assert cap is not None
    assert ids == [pack.id]
    assert any(
        "skills.malformed_pack_id" in record.message for record in caplog.records
    )


async def test_all_malformed_returns_empty(db_session, workspace):
    cap, ids = await skills_mod.build_skills_capability(
        policy={"skills": ["nope", "", None]},
        workspace_id=workspace.id,
        db=db_session,
    )
    assert cap is None
    assert ids == []


async def test_happy_path_three_active(db_session, workspace):
    p1 = await _create_pack(
        db_session,
        workspace_id=workspace.id,
        slug="alpha",
        content_md="alpha body",
    )
    p2 = await _create_pack(
        db_session,
        workspace_id=workspace.id,
        slug="beta",
        content_md="beta body",
    )
    p3 = await _create_pack(
        db_session,
        workspace_id=workspace.id,
        slug="gamma",
        content_md="gamma body",
    )

    cap, ids = await skills_mod.build_skills_capability(
        policy={"skills": [str(p1.id), str(p2.id), str(p3.id)]},
        workspace_id=workspace.id,
        db=db_session,
    )
    assert isinstance(cap, _CapturedCapability)
    assert set(ids) == {p1.id, p2.id, p3.id}
    assert {s.name for s in cap.skills} == {"alpha", "beta", "gamma"}
    assert {s.content for s in cap.skills} == {
        "alpha body",
        "beta body",
        "gamma body",
    }


async def test_archived_pack_filtered_out(db_session, workspace):
    active = await _create_pack(
        db_session, workspace_id=workspace.id, slug="active-one"
    )
    archived = await _create_pack(
        db_session,
        workspace_id=workspace.id,
        slug="archived-one",
        state=SkillPackState.ARCHIVED,
    )

    cap, ids = await skills_mod.build_skills_capability(
        policy={"skills": [str(active.id), str(archived.id)]},
        workspace_id=workspace.id,
        db=db_session,
    )
    assert cap is not None
    assert ids == [active.id]


async def test_pinned_stale_pack_is_injected(db_session, workspace):
    pinned_stale = await _create_pack(
        db_session,
        workspace_id=workspace.id,
        slug="stale-pin",
        state=SkillPackState.STALE,
        pinned=True,
    )

    cap, ids = await skills_mod.build_skills_capability(
        policy={"skills": [str(pinned_stale.id)]},
        workspace_id=workspace.id,
        db=db_session,
    )
    assert cap is not None
    assert ids == [pinned_stale.id]


async def test_pinned_tombstone_pack_is_excluded(db_session, workspace):
    tomb = await _create_pack(
        db_session,
        workspace_id=workspace.id,
        slug="tomb-pin",
        state=SkillPackState.TOMBSTONE,
        pinned=True,
    )

    cap, ids = await skills_mod.build_skills_capability(
        policy={"skills": [str(tomb.id)]},
        workspace_id=workspace.id,
        db=db_session,
    )
    assert cap is None
    assert ids == []


async def test_db_failure_returns_empty(monkeypatch, db_session, workspace):
    pack = await _create_pack(
        db_session, workspace_id=workspace.id, slug="ok-but-broken-repo"
    )

    async def _boom(self, *, workspace_id, ids=None, limit=500):
        raise RuntimeError("simulated DB failure")

    monkeypatch.setattr(SkillPackRepository, "list_active", _boom)

    cap, ids = await skills_mod.build_skills_capability(
        policy={"skills": [str(pack.id)]},
        workspace_id=workspace.id,
        db=db_session,
    )
    assert cap is None
    assert ids == []


async def test_capability_init_failure_returns_empty(
    monkeypatch, db_session, workspace
):
    pack = await _create_pack(
        db_session, workspace_id=workspace.id, slug="cap-init-fails"
    )
    monkeypatch.setattr(
        skills_mod, "_instantiate_skills_capability", lambda _: None
    )

    cap, ids = await skills_mod.build_skills_capability(
        policy={"skills": [str(pack.id)]},
        workspace_id=workspace.id,
        db=db_session,
    )
    assert cap is None
    assert ids == []


async def test_pack_with_missing_skill_md_falls_back_to_description(
    db_session, workspace
):
    pack = await SkillPackRepository(db_session).create(
        workspace_id=workspace.id,
        slug="no-file",
        name="no-file",
        description="frontmatter description",
        version="0.1.0",
        manifest_json={},
        metadata_json={},
        enabled=True,
        state=SkillPackState.ACTIVE,
    )
    await db_session.flush()

    cap, ids = await skills_mod.build_skills_capability(
        policy={"skills": [str(pack.id)]},
        workspace_id=workspace.id,
        db=db_session,
    )
    assert cap is not None
    assert ids == [pack.id]
    assert cap.skills[0].content == "frontmatter description"
