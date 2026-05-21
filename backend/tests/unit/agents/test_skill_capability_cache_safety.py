"""Cache-prefix stability proof for ``build_skills_capability`` (M1.7).

Provider-side prompt caches key on the leading SystemPromptPart bytes
of every request. The skill capability injects deterministic
metadata + content into the system prompt at ``Agent.iter()`` time, so
two consecutive runs against the same agent + DB state must produce a
byte-identical pack id sequence — otherwise the cache prefix flips and
the model is silently re-charged for the same prompt.

These tests verify two angles:

1. Repeated invocations against the same DB return the *same* ordered
   ``injected_pack_ids`` list (stable across runs).
2. Repeated invocations swap the order in the user-supplied
   ``policy["skills"]`` list — the function must still order results
   by the canonical repository order (``updated_at DESC``), so that a
   trivial reorder of the agent's metadata blob does not flip the
   cache prefix.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from app.agents.harness import skills as skills_mod
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


async def _seed_pack(db_session, *, workspace_id: uuid.UUID, slug: str):
    pack = await SkillPackRepository(db_session).create(
        workspace_id=workspace_id,
        slug=slug,
        name=slug,
        description=f"{slug} description",
        version="0.1.0",
        manifest_json={},
        metadata_json={},
        enabled=True,
        state=SkillPackState.ACTIVE,
    )
    await SkillFileRepository(db_session).create(
        workspace_id=workspace_id,
        skill_pack_id=pack.id,
        path="SKILL.md",
        content_md=f"# {slug}\nbody",
    )
    await db_session.flush()
    return pack


async def test_repeated_invocations_yield_identical_ids(
    db_session, workspace
):
    p1 = await _seed_pack(db_session, workspace_id=workspace.id, slug="alpha")
    p2 = await _seed_pack(db_session, workspace_id=workspace.id, slug="bravo")
    p3 = await _seed_pack(db_session, workspace_id=workspace.id, slug="charlie")
    policy = {"skills": [str(p1.id), str(p2.id), str(p3.id)]}

    cap_a, ids_a = await skills_mod.build_skills_capability(
        policy=policy, workspace_id=workspace.id, db=db_session
    )
    cap_b, ids_b = await skills_mod.build_skills_capability(
        policy=policy, workspace_id=workspace.id, db=db_session
    )

    assert ids_a == ids_b
    assert cap_a is not None and cap_b is not None
    assert [s.name for s in cap_a.skills] == [s.name for s in cap_b.skills]
    assert [s.content for s in cap_a.skills] == [
        s.content for s in cap_b.skills
    ]


async def test_policy_order_does_not_change_injection_order(
    db_session, workspace
):
    p1 = await _seed_pack(db_session, workspace_id=workspace.id, slug="alpha-2")
    p2 = await _seed_pack(db_session, workspace_id=workspace.id, slug="bravo-2")
    p3 = await _seed_pack(db_session, workspace_id=workspace.id, slug="charlie-2")

    forward_policy = {"skills": [str(p1.id), str(p2.id), str(p3.id)]}
    reversed_policy = {"skills": [str(p3.id), str(p2.id), str(p1.id)]}

    _, ids_forward = await skills_mod.build_skills_capability(
        policy=forward_policy, workspace_id=workspace.id, db=db_session
    )
    _, ids_reversed = await skills_mod.build_skills_capability(
        policy=reversed_policy, workspace_id=workspace.id, db=db_session
    )

    assert ids_forward == ids_reversed
    assert set(ids_forward) == {p1.id, p2.id, p3.id}


async def test_unrelated_pack_does_not_leak_into_run(db_session, workspace):
    """A pack that exists in the workspace but is not bound by policy
    must never appear in the resolved id list — otherwise the cache
    prefix would shift the moment another agent gets a new pack added.
    """
    bound = await _seed_pack(
        db_session, workspace_id=workspace.id, slug="bound-pack"
    )
    await _seed_pack(
        db_session, workspace_id=workspace.id, slug="unrelated-pack"
    )

    cap, ids = await skills_mod.build_skills_capability(
        policy={"skills": [str(bound.id)]},
        workspace_id=workspace.id,
        db=db_session,
    )
    assert cap is not None
    assert ids == [bound.id]
