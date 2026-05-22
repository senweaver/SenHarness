"""Unit tests for the ``mark_message_as_compressed`` pure helper (M4.3).

The helper is the single choke point a future compaction layer goes
through to stamp ``original_turns_ref`` on a summary message — so the
schema invariants belong here, not deep inside the (yet-to-land)
sliding-window module.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from app.services import lineage_replay as lineage_svc


def _fake_message(message_id: uuid.UUID | None = None) -> SimpleNamespace:
    return SimpleNamespace(id=message_id or uuid.uuid4())


def test_helper_returns_full_schema():
    summary = _fake_message()
    originals = [_fake_message(), _fake_message(), _fake_message()]

    ref = lineage_svc.mark_message_as_compressed(summary, originals, strategy="sliding_window")

    assert set(ref.keys()) == {
        "turn_message_ids",
        "turn_count",
        "compressed_at",
        "compaction_strategy",
    }
    assert ref["turn_count"] == 3
    assert ref["compaction_strategy"] == "sliding_window"
    assert len(ref["turn_message_ids"]) == 3
    assert all(isinstance(s, str) for s in ref["turn_message_ids"])


def test_helper_preserves_original_order():
    originals = [_fake_message() for _ in range(5)]
    expected = [str(m.id) for m in originals]

    ref = lineage_svc.mark_message_as_compressed(_fake_message(), originals, strategy="manual")
    assert ref["turn_message_ids"] == expected


def test_helper_rejects_unknown_strategy():
    with pytest.raises(ValueError):
        lineage_svc.mark_message_as_compressed(
            _fake_message(), [_fake_message()], strategy="rotation"
        )


def test_helper_accepts_known_strategies():
    for s in ("sliding_window", "manual", "evolver"):
        ref = lineage_svc.mark_message_as_compressed(_fake_message(), [_fake_message()], strategy=s)
        assert ref["compaction_strategy"] == s


def test_helper_uses_explicit_compressed_at_when_provided():
    when = datetime(2026, 5, 10, 12, 0, 0, tzinfo=UTC)
    ref = lineage_svc.mark_message_as_compressed(
        _fake_message(),
        [_fake_message()],
        strategy="sliding_window",
        compressed_at=when,
    )
    assert ref["compressed_at"].startswith("2026-05-10T12:00:00")


def test_helper_handles_empty_original_list():
    ref = lineage_svc.mark_message_as_compressed(_fake_message(), [], strategy="manual")
    assert ref["turn_count"] == 0
    assert ref["turn_message_ids"] == []
