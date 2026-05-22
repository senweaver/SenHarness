"""Pure parser tests for the ``/insights`` slash command (M4.5).

The parser is sync-callable from a service-level async wrapper so we
can hit it without spinning up a DB. The matching shape mirrors the
M0.1 ``/goal`` parser tests: bare command → status, ``--days N`` →
custom window, junk → ``None``.
"""

from __future__ import annotations

import pytest

from app.services.cross_session_insights import parse_insights_command


@pytest.mark.asyncio
async def test_bare_insights_returns_default_window():
    assert await parse_insights_command("/insights") == {"days": None}
    assert await parse_insights_command("  /insights  ") == {"days": None}
    assert await parse_insights_command("/INSIGHTS") == {"days": None}


@pytest.mark.asyncio
async def test_explicit_days_window():
    assert await parse_insights_command("/insights --days 7") == {"days": 7}
    assert await parse_insights_command("/insights --days=14") == {"days": 14}
    assert await parse_insights_command("  /insights  --days  90  ") == {"days": 90}


@pytest.mark.asyncio
async def test_negative_days_returned_for_validation_layer():
    """Negative / zero days are returned as-is so the queue layer can
    produce a structured ``insights.days_out_of_range`` rejection."""
    assert await parse_insights_command("/insights --days 0") == {"days": 0}
    assert await parse_insights_command("/insights --days -3") == {"days": -3}


@pytest.mark.asyncio
async def test_non_insights_returns_none():
    assert await parse_insights_command("Hello") is None
    assert await parse_insights_command("") is None
    # ``/goal`` lives in a parallel parser; the insights parser must
    # not hijack it.
    assert await parse_insights_command("/goal ship M4.5") is None
    # Bare slash should not hijack the command palette either.
    assert await parse_insights_command("/") is None


@pytest.mark.asyncio
async def test_garbled_insights_still_recognised_as_insights_attempt():
    """The parser is permissive: anything starting with ``/insights``
    is returned as an insights command (with default window) so the
    caller writes one audit row instead of silently passing the meta
    text to the LLM."""
    assert await parse_insights_command("/insights last 30 days") == {"days": None}
    assert await parse_insights_command("/insights --days abc") == {"days": None}
