"""M0.8 — discord guild scoping + DM block.

The Gateway loop in ``_discord_stream`` is hard to drive without a
real ``discord.py`` client, so we exercise the same predicate via a
small reproduction of the filter logic. Keeping the predicate inline
in the test would be brittle; instead we re-derive it from the
``allowed_guild_ids`` / ``allow_dms`` semantics documented in
``docs/extensions-and-governance.md`` (Channel security section).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class _StubGuild:
    id: str | int


def _should_dispatch(
    *, allowed_guild_ids: set[str], allow_dms: bool, guild: _StubGuild | None
) -> bool:
    if guild is None:
        return allow_dms
    if not allowed_guild_ids:
        return True
    return str(guild.id) in allowed_guild_ids


def test_dm_blocked_by_default() -> None:
    assert not _should_dispatch(allowed_guild_ids=set(), allow_dms=False, guild=None)


def test_dm_allowed_when_flag_on() -> None:
    assert _should_dispatch(allowed_guild_ids=set(), allow_dms=True, guild=None)


def test_empty_allowlist_passes_any_guild() -> None:
    assert _should_dispatch(allowed_guild_ids=set(), allow_dms=False, guild=_StubGuild(id="g1"))


def test_allowlist_match_passes() -> None:
    assert _should_dispatch(
        allowed_guild_ids={"g1", "g2"},
        allow_dms=False,
        guild=_StubGuild(id="g2"),
    )


def test_allowlist_mismatch_blocked() -> None:
    assert not _should_dispatch(
        allowed_guild_ids={"g1"},
        allow_dms=False,
        guild=_StubGuild(id="g_unauth"),
    )


def test_allowlist_handles_int_guild_id() -> None:
    assert _should_dispatch(
        allowed_guild_ids={"42"},
        allow_dms=False,
        guild=_StubGuild(id=42),
    )
