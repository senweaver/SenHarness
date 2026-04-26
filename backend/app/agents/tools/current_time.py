"""`current_time` tool — returns the current wall-clock time in ISO 8601."""

from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, Field


class CurrentTimeArgs(BaseModel):
    timezone: str = Field(
        default="UTC",
        description="IANA timezone name (e.g. 'Asia/Shanghai', 'America/Los_Angeles', 'UTC').",
    )


def run_current_time(args: CurrentTimeArgs) -> dict:
    try:
        zone = ZoneInfo(args.timezone)
    except ZoneInfoNotFoundError:
        zone = UTC
    now = datetime.now(zone)
    return {
        "iso": now.isoformat(),
        "timezone": str(zone),
        "unix": int(now.timestamp()),
        "weekday": now.strftime("%A"),
    }
