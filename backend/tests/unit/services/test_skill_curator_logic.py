"""Unit: ``skill_curator.find_*_candidates`` selection logic (M1.4).

Pure read-side checks — no audit, no approvals. The two finders return
the eligible-pack rows for the curator to act on; pinned packs are
intentionally still returned because the pin-exemption gate sits at
:func:`skill_lifecycle.transition`, not at the candidate query.
"""

from __future__ import annotations

import uuid
from datetime import timedelta

import pytest

from app.core.security import utcnow_naive
from app.db.models.skills import SkillPackState
from app.repositories.skills import SkillPackRepository
from app.services import skill_curator as svc

pytestmark = pytest.mark.asyncio


async def _make_pack(
    db,
    *,
    workspace_id,
    state: SkillPackState = SkillPackState.ACTIVE,
    last_used_at=None,
    state_changed_at=None,
    pinned: bool = False,
    effectiveness_avg: float | None = None,
    use_count: int | None = None,
):
    pack = await SkillPackRepository(db).create(
        workspace_id=workspace_id,
        slug=f"sk-{uuid.uuid4().hex[:6]}",
        name="Curator pack",
        description="x",
        version="0.1.0",
        publisher=None,
        signature=None,
        manifest_json={},
        enabled=True,
        metadata_json={},
        created_by=None,
        state=state,
    )
    pack.pinned = pinned
    pack.last_used_at = last_used_at
    pack.state_changed_at = state_changed_at
    pack.effectiveness_avg = effectiveness_avg
    await db.flush([pack])
    _ = use_count  # accepted for symmetry with the test brief; not stored.
    return pack


# ── find_stale_candidates ─────────────────────────────────────
async def test_stale_candidate_qualifies_when_idle_for_31_days(db_session, workspace):
    now = utcnow_naive()
    eligible = await _make_pack(
        db_session,
        workspace_id=workspace.id,
        last_used_at=now - timedelta(days=31),
    )
    fresh = await _make_pack(
        db_session,
        workspace_id=workspace.id,
        last_used_at=now - timedelta(hours=23),  # touched within min_idle_hours
    )

    found = await svc.find_stale_candidates(
        db_session,
        workspace_id=workspace.id,
        stale_after_days=30,
        min_idle_hours=24,
        now=now,
    )
    found_ids = {p.id for p in found}
    assert eligible.id in found_ids
    assert fresh.id not in found_ids


async def test_stale_candidate_skips_pack_used_within_min_idle_hours(db_session, workspace):
    """Recently-touched pack stays out even when it crosses ``stale_after_days``.

    The ``min_idle_hours`` knob exists to protect against a race with
    the M1.3 rollup that hasn't materialised the latest use yet.
    """
    now = utcnow_naive()
    pack = await _make_pack(
        db_session,
        workspace_id=workspace.id,
        # ACTIVE for >30 days (qualifies as "stale" by age) but the
        # last invocation lands inside the 24-hour idle window —
        # ``min_idle_hours`` must filter it out.
        state_changed_at=now - timedelta(days=40),
        last_used_at=now - timedelta(hours=23),
    )
    found = await svc.find_stale_candidates(
        db_session,
        workspace_id=workspace.id,
        stale_after_days=30,
        min_idle_hours=24,
        now=now,
    )
    assert pack.id not in {p.id for p in found}


async def test_stale_candidate_includes_pinned_pack(db_session, workspace):
    """Pinned filter sits at transition, not at the read step."""
    now = utcnow_naive()
    pinned_pack = await _make_pack(
        db_session,
        workspace_id=workspace.id,
        last_used_at=now - timedelta(days=60),
        pinned=True,
    )
    found = await svc.find_stale_candidates(
        db_session,
        workspace_id=workspace.id,
        stale_after_days=30,
        min_idle_hours=24,
        now=now,
    )
    assert pinned_pack.id in {p.id for p in found}


async def test_stale_candidate_never_used_pack_uses_state_changed_age(db_session, workspace):
    now = utcnow_naive()
    old_unused = await _make_pack(
        db_session,
        workspace_id=workspace.id,
        last_used_at=None,
        state_changed_at=now - timedelta(days=45),
    )
    young_unused = await _make_pack(
        db_session,
        workspace_id=workspace.id,
        last_used_at=None,
        state_changed_at=now - timedelta(days=5),
    )
    found = await svc.find_stale_candidates(
        db_session,
        workspace_id=workspace.id,
        stale_after_days=30,
        min_idle_hours=24,
        now=now,
    )
    found_ids = {p.id for p in found}
    assert old_unused.id in found_ids
    assert young_unused.id not in found_ids


async def test_stale_candidate_skips_non_active_packs(db_session, workspace):
    now = utcnow_naive()
    stale_pack = await _make_pack(
        db_session,
        workspace_id=workspace.id,
        state=SkillPackState.STALE,
        last_used_at=now - timedelta(days=60),
    )
    archived = await _make_pack(
        db_session,
        workspace_id=workspace.id,
        state=SkillPackState.ARCHIVED,
        last_used_at=now - timedelta(days=60),
    )
    found = await svc.find_stale_candidates(
        db_session,
        workspace_id=workspace.id,
        stale_after_days=30,
        min_idle_hours=24,
        now=now,
    )
    found_ids = {p.id for p in found}
    assert stale_pack.id not in found_ids
    assert archived.id not in found_ids


# ── find_archive_candidates ──────────────────────────────────
async def test_archive_candidate_qualifies_after_archive_threshold(db_session, workspace):
    now = utcnow_naive()
    eligible = await _make_pack(
        db_session,
        workspace_id=workspace.id,
        state=SkillPackState.STALE,
        state_changed_at=now - timedelta(days=95),
    )
    too_young = await _make_pack(
        db_session,
        workspace_id=workspace.id,
        state=SkillPackState.STALE,
        state_changed_at=now - timedelta(days=10),
    )
    found = await svc.find_archive_candidates(
        db_session,
        workspace_id=workspace.id,
        archive_after_days=90,
        now=now,
    )
    found_ids = {p.id for p in found}
    assert eligible.id in found_ids
    assert too_young.id not in found_ids


async def test_archive_candidate_skips_active_packs(db_session, workspace):
    now = utcnow_naive()
    active_pack = await _make_pack(
        db_session,
        workspace_id=workspace.id,
        state=SkillPackState.ACTIVE,
        state_changed_at=now - timedelta(days=180),
    )
    found = await svc.find_archive_candidates(
        db_session,
        workspace_id=workspace.id,
        archive_after_days=90,
        now=now,
    )
    assert active_pack.id not in {p.id for p in found}


async def test_archive_candidate_metrics_do_not_affect_selection(db_session, workspace):
    """``effectiveness_avg`` and use_count_30d don't gate selection.

    The candidate query is age-based only; the M1.9 admin "soft cap"
    knob will be the place metrics gate selection later.
    """
    now = utcnow_naive()
    high_perf = await _make_pack(
        db_session,
        workspace_id=workspace.id,
        state=SkillPackState.STALE,
        state_changed_at=now - timedelta(days=120),
        effectiveness_avg=0.95,
    )
    low_perf = await _make_pack(
        db_session,
        workspace_id=workspace.id,
        state=SkillPackState.STALE,
        state_changed_at=now - timedelta(days=120),
        effectiveness_avg=0.10,
    )
    found = await svc.find_archive_candidates(
        db_session,
        workspace_id=workspace.id,
        archive_after_days=90,
        now=now,
    )
    found_ids = {p.id for p in found}
    assert high_perf.id in found_ids
    assert low_perf.id in found_ids


# ── CuratorConfig defaults ───────────────────────────────────
async def test_curator_config_defaults_match_locked_decision():
    cfg = svc.CuratorConfig()
    assert cfg.enabled is True  # design point Q4 — default enabled
    assert cfg.stale_after_days == 30
    assert cfg.archive_after_days == 90
    assert cfg.min_idle_hours == 24
    assert cfg.active_skills_soft_cap == 50


async def test_curator_config_clamps_out_of_range_values():
    cfg = svc.CuratorConfig.from_dict(
        {
            "enabled": False,
            "stale_after_days": 0,  # below lo=1 → clamped to 1
            "archive_after_days": 99999,  # above hi=3650 → clamped to 3650
            "min_idle_hours": -5,  # below lo=0 → clamped to 0
            "active_skills_soft_cap": "garbage",
        }
    )
    assert cfg.enabled is False
    assert cfg.stale_after_days == 1
    assert cfg.archive_after_days == 3650
    assert cfg.min_idle_hours == 0
    # garbage falls back to default 50
    assert cfg.active_skills_soft_cap == 50
