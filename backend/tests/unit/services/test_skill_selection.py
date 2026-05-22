"""Pure-function tests for the M1.8 active-skill-set hard cap.

The seven cases pin the contract of :func:`select_active_set`:

1. empty input → empty result
2. all packs under cap → no drops, both flags false
3. unpinned overflow → count cap fires, dropped list populated
4. char cap fires before count cap when bodies are large
5. pinned packs above cap → all pinned selected, zero dropped, warn log
6. effectiveness vs recency tiebreak ordering is deterministic
7. ``selection_strategy="manual_only"`` preserves caller order

The tests use a lightweight ``_StubPack`` rather than the real ORM
class so they stay decoupled from the DB layer.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from app.services.skill_selection import (
    DEFAULT_MAX_ACTIVE_INJECTED,
    SkillSelectionConfig,
    select_active_set,
)


@dataclass
class _StubPack:
    slug: str
    pinned: bool = False
    effectiveness_avg: float | None = None
    last_used_at: datetime | None = None
    created_at: datetime = field(default_factory=lambda: datetime(2026, 1, 1))
    description: str | None = None
    content_md: str | None = None


_NOW = datetime(2026, 5, 1, 0, 0, 0)


def _pack(
    slug: str,
    *,
    pinned: bool = False,
    eff: float | None = None,
    last_used: datetime | None = None,
    created: datetime | None = None,
    body_chars: int = 100,
) -> _StubPack:
    return _StubPack(
        slug=slug,
        pinned=pinned,
        effectiveness_avg=eff,
        last_used_at=last_used,
        created_at=created or _NOW,
        content_md="x" * body_chars,
    )


def test_empty_packs_returns_empty_result():
    cfg = SkillSelectionConfig()
    result = select_active_set([], cap=cfg)

    assert result.selected == []
    assert result.dropped == []
    assert result.char_count == 0
    assert result.truncated_by_count is False
    assert result.truncated_by_chars is False


def test_all_under_cap_no_drops():
    cfg = SkillSelectionConfig(max_active_injected=10, max_injected_chars_total=10_000)
    packs = [_pack(f"p{i}", eff=0.5, body_chars=50) for i in range(5)]

    result = select_active_set(packs, cap=cfg)

    assert len(result.selected) == 5
    assert result.dropped == []
    assert result.char_count == 5 * 50
    assert result.truncated_by_count is False
    assert result.truncated_by_chars is False


def test_count_cap_drops_overflow_unpinned():
    cfg = SkillSelectionConfig(max_active_injected=30, max_injected_chars_total=10_000_000)
    # 50 packs ranked by descending effectiveness — top 30 must
    # survive, bottom 20 must drop. Body kept tiny so chars cap
    # cannot fire first.
    packs = [_pack(f"p{i:02d}", eff=1.0 - i / 100.0, body_chars=10) for i in range(50)]

    result = select_active_set(packs, cap=cfg)

    assert len(result.selected) == 30
    assert len(result.dropped) == 20
    assert result.truncated_by_count is True
    assert result.truncated_by_chars is False
    # Top-ranked (highest effectiveness) packs must be in the selected set.
    selected_slugs = {p.slug for p in result.selected}
    assert "p00" in selected_slugs
    assert "p29" in selected_slugs
    assert "p49" not in selected_slugs


def test_char_cap_fires_before_count_cap():
    cfg = SkillSelectionConfig(max_active_injected=100, max_injected_chars_total=300)
    # Each pack is 100 chars; 4 packs = 400 chars > 300 so the third
    # one fits and the fourth is dropped by char cap (not count).
    packs = [_pack(f"p{i}", eff=1.0 - i / 100.0, body_chars=100) for i in range(10)]

    result = select_active_set(packs, cap=cfg)

    assert len(result.selected) == 3
    assert len(result.dropped) == 7
    assert result.char_count == 300
    assert result.truncated_by_chars is True
    assert result.truncated_by_count is False


def test_pinned_packs_exempt_from_count_cap(caplog):
    cfg = SkillSelectionConfig(max_active_injected=3, max_injected_chars_total=10_000)
    packs = [_pack(f"pin{i}", pinned=True, body_chars=10) for i in range(5)]

    with caplog.at_level(logging.WARNING):
        result = select_active_set(packs, cap=cfg)

    assert len(result.selected) == 5  # all pinned win
    assert result.dropped == []
    # The truncation flags should NOT fire for pinned-only overflow —
    # the cap was breached but no pack was dropped, so nothing was
    # truncated; the warn log is the auditor's signal.
    assert result.truncated_by_count is False
    assert result.truncated_by_chars is False
    assert any("skill.cap_pinned_above_count_cap" in rec.message for rec in caplog.records)


def test_effectiveness_then_recency_tiebreak_is_stable():
    cfg = SkillSelectionConfig(max_active_injected=2, max_injected_chars_total=10_000)
    base = _NOW
    packs = [
        # Same effectiveness — recency wins; "newer" pack should be first.
        _pack(
            "older-tied",
            eff=0.7,
            last_used=base - timedelta(days=5),
            body_chars=10,
        ),
        _pack(
            "newer-tied",
            eff=0.7,
            last_used=base - timedelta(days=1),
            body_chars=10,
        ),
        # Lower effectiveness — should not make the cap.
        _pack("lower", eff=0.1, body_chars=10),
    ]

    result = select_active_set(packs, cap=cfg)

    assert [p.slug for p in result.selected] == ["newer-tied", "older-tied"]
    assert [p.slug for p in result.dropped] == ["lower"]
    assert result.truncated_by_count is True


def test_manual_only_preserves_caller_order():
    cfg = SkillSelectionConfig(
        max_active_injected=2,
        max_injected_chars_total=10_000,
        selection_strategy="manual_only",
    )
    # Caller order is intentionally NOT effectiveness-sorted; the
    # manual strategy must keep it.
    packs = [
        _pack("first", eff=0.1, body_chars=10),
        _pack("second", eff=0.9, body_chars=10),
        _pack("third", eff=0.5, body_chars=10),
    ]

    result = select_active_set(packs, cap=cfg)

    assert [p.slug for p in result.selected] == ["first", "second"]
    assert [p.slug for p in result.dropped] == ["third"]


def test_default_config_uses_30_count_cap():
    """Sanity check on the platform-default surface.

    Documents that the no-arg config matches the M1.8 platform
    default so a regression on either side surfaces here.
    """
    cfg = SkillSelectionConfig()
    assert cfg.max_active_injected == DEFAULT_MAX_ACTIVE_INJECTED == 30
    assert cfg.max_injected_chars_total == 12000
    assert cfg.selection_strategy == "effectiveness_then_recency"


def test_null_effectiveness_sorts_after_scored_packs():
    cfg = SkillSelectionConfig(max_active_injected=2, max_injected_chars_total=10_000)
    packs = [
        _pack("no-score", eff=None, body_chars=10),
        _pack("low-score", eff=0.1, body_chars=10),
        _pack("high-score", eff=0.9, body_chars=10),
    ]

    result = select_active_set(packs, cap=cfg)

    # NULLS LAST: scored packs win the two slots; the unscored one
    # is dropped.
    assert [p.slug for p in result.selected] == ["high-score", "low-score"]
    assert [p.slug for p in result.dropped] == ["no-score"]


def test_pinned_pack_preserves_position_relative_to_unpinned():
    """Pinned packs always lead the selected list regardless of cap order.

    Belt-and-suspenders for the M1.5 capture layer: the
    ``injected_pack_ids`` order it persists on the session_artifact
    must put pinned packs first so a downstream replay tool can
    always identify the operator-anchored anchors.
    """
    cfg = SkillSelectionConfig(max_active_injected=4, max_injected_chars_total=10_000)
    packs = [
        _pack("ranked-low", eff=0.1, body_chars=10),
        _pack("pinned-mid", pinned=True, eff=0.5, body_chars=10),
        _pack("ranked-high", eff=0.9, body_chars=10),
    ]

    result = select_active_set(packs, cap=cfg)

    selected_slugs = [p.slug for p in result.selected]
    assert selected_slugs[0] == "pinned-mid"
    # Order of the unpinned tail respects the strategy sort.
    assert selected_slugs[1:] == ["ranked-high", "ranked-low"]
