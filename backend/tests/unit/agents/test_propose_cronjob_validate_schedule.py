"""Pure-function coverage for ``parse_schedule`` (M2.8).

The runner trusts ``parse_schedule`` for schedule discrimination; if
this falls through then a malformed cron / interval / timestamp would
land an Approval that the M2.5 dispatch handler can't translate. The
tests exhaustively cover the three accepted shapes plus the standard
rejection branches (negative interval, past ISO timestamp, garbage).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.agents.tools.cronjob_propose import (
    ScheduleParseError,
    parse_schedule,
)


# ─── cron ────────────────────────────────────────────────────
def test_cron_daily_at_nine_parses() -> None:
    kind, meta = parse_schedule("0 9 * * *")
    assert kind == "cron"
    assert meta["expr"] == "0 9 * * *"
    assert meta["tz"] == "UTC"
    assert meta["expression"] == "0 9 * * *"


def test_cron_complex_expression_parses() -> None:
    kind, meta = parse_schedule("*/15 8-18 * * 1-5")
    assert kind == "cron"
    assert meta["expr"] == "*/15 8-18 * * 1-5"


def test_cron_with_outer_whitespace_strips() -> None:
    kind, _ = parse_schedule("   0 9 * * *   ")
    assert kind == "cron"


def test_cron_invalid_minute_field_rejected() -> None:
    with pytest.raises(ScheduleParseError):
        parse_schedule("99 9 * * *")


def test_cron_invalid_hour_field_rejected() -> None:
    with pytest.raises(ScheduleParseError):
        parse_schedule("0 99 * * *")


def test_cron_six_fields_falls_through_to_iso_then_rejects() -> None:
    # Six fields is not a 5-field cron and not an ISO timestamp → reject.
    with pytest.raises(ScheduleParseError):
        parse_schedule("0 9 * * * extra")


# ─── interval ────────────────────────────────────────────────
def test_interval_two_hours_parses() -> None:
    kind, meta = parse_schedule("every 2h")
    assert kind == "interval"
    assert meta["amount"] == 2
    assert meta["unit"] == "h"
    assert meta["seconds"] == 7200


def test_interval_thirty_minutes_parses() -> None:
    kind, meta = parse_schedule("every 30m")
    assert kind == "interval"
    assert meta["amount"] == 30
    assert meta["unit"] == "m"
    assert meta["seconds"] == 1800


def test_interval_seconds_and_days_parse() -> None:
    assert parse_schedule("every 45s")[1]["seconds"] == 45
    assert parse_schedule("every 7d")[1]["seconds"] == 7 * 86400


def test_interval_zero_amount_rejected() -> None:
    with pytest.raises(ScheduleParseError, match="positive"):
        parse_schedule("every 0s")


def test_interval_unsupported_unit_rejected() -> None:
    # ``y`` (years) is not in the supported set; falls through to ISO
    # timestamp parser which also rejects it.
    with pytest.raises(ScheduleParseError):
        parse_schedule("every 1y")


def test_interval_extra_whitespace_rejected() -> None:
    # The regex is exact (single space); enforces the wire format the
    # docs publish so the model is held to a single shape.
    with pytest.raises(ScheduleParseError):
        parse_schedule("every  2h")


# ─── one_shot ────────────────────────────────────────────────
def test_one_shot_future_iso_with_tz_parses() -> None:
    future = (datetime.now(UTC) + timedelta(days=1)).isoformat()
    kind, meta = parse_schedule(future)
    assert kind == "one_shot"
    # ``run_at`` is normalised to naive UTC for storage.
    assert "+" not in meta["run_at"]
    assert "Z" not in meta["run_at"]


def test_one_shot_future_iso_naive_parses() -> None:
    future = (datetime.utcnow() + timedelta(hours=1)).isoformat()
    kind, _ = parse_schedule(future)
    assert kind == "one_shot"


def test_one_shot_past_iso_rejected() -> None:
    past = (datetime.now(UTC) - timedelta(days=1)).isoformat()
    with pytest.raises(ScheduleParseError, match="future"):
        parse_schedule(past)


def test_one_shot_now_rejected() -> None:
    # Equal-to-now must still be rejected (strict >); covers the
    # boundary the runner depends on.
    now_iso = datetime.utcnow().isoformat()
    with pytest.raises(ScheduleParseError):
        parse_schedule(now_iso)


# ─── garbage ─────────────────────────────────────────────────
def test_empty_schedule_rejected() -> None:
    with pytest.raises(ScheduleParseError):
        parse_schedule("")


def test_whitespace_only_schedule_rejected() -> None:
    with pytest.raises(ScheduleParseError):
        parse_schedule("   \t  ")


def test_random_string_rejected() -> None:
    with pytest.raises(ScheduleParseError):
        parse_schedule("not a schedule at all")
