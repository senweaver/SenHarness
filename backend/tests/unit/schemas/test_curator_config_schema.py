"""Unit: :class:`app.schemas.curator.CuratorConfigIn` field + cross-field
validation pinning (M1.9).

Pure-Python; no DB, no FastAPI test client. The integration suite hits
the same constraints through the PATCH endpoint, but we keep the
schema-level cases here so a regression in the Pydantic model is
caught without paying for the full test client startup.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.schemas.curator import CuratorConfigIn


def test_empty_payload_is_valid_no_op() -> None:
    config = CuratorConfigIn()
    dump = config.model_dump(exclude_none=True)
    assert dump == {}


def test_valid_full_payload_round_trips() -> None:
    config = CuratorConfigIn(
        enabled=True,
        stale_after_days=14,
        archive_after_days=60,
        min_idle_hours=12,
        active_skills_soft_cap=40,
    )
    assert config.stale_after_days == 14
    assert config.archive_after_days == 60


@pytest.mark.parametrize(
    "field, value",
    [
        ("stale_after_days", 0),
        ("stale_after_days", 366),
        ("stale_after_days", -1),
        ("archive_after_days", 0),
        ("archive_after_days", 366),
        ("min_idle_hours", -1),
        ("min_idle_hours", 721),
        ("active_skills_soft_cap", 0),
        ("active_skills_soft_cap", 1001),
    ],
)
def test_out_of_range_value_rejected(field: str, value: int) -> None:
    with pytest.raises(ValidationError):
        CuratorConfigIn(**{field: value})


@pytest.mark.parametrize(
    "field, value",
    [
        ("stale_after_days", "abc"),
        ("archive_after_days", 1.5),
        ("min_idle_hours", "x"),
    ],
)
def test_non_int_value_rejected(field: str, value: object) -> None:
    """Pydantic 2 coerces bool-like and numeric strings; non-integer
    values that cannot be parsed as ``int`` raise ``ValidationError``.
    """
    with pytest.raises(ValidationError):
        CuratorConfigIn(**{field: value})


def test_stale_greater_than_archive_rejected() -> None:
    with pytest.raises(ValidationError) as excinfo:
        CuratorConfigIn(stale_after_days=60, archive_after_days=30)
    msg = str(excinfo.value)
    assert "stale_after_days" in msg
    assert "archive_after_days" in msg


def test_stale_equal_to_archive_accepted() -> None:
    config = CuratorConfigIn(stale_after_days=30, archive_after_days=30)
    assert config.stale_after_days == 30
    assert config.archive_after_days == 30


def test_partial_only_stale_supplied_skips_cross_field_check() -> None:
    """Cross-field invariant only fires when both fields are supplied.
    A PATCH that bumps only ``stale_after_days`` must succeed at the
    schema layer; the route will re-evaluate against the effective
    merged state (where the platform default fills in
    ``archive_after_days``)."""
    config = CuratorConfigIn(stale_after_days=200)
    assert config.stale_after_days == 200
    assert config.archive_after_days is None


def test_enabled_only_payload_accepted() -> None:
    config = CuratorConfigIn(enabled=False)
    assert config.enabled is False
    assert config.stale_after_days is None
