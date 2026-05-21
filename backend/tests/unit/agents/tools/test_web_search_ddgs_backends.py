"""Guard the ``ddgs`` no-key fallback against silently regressing to
``backend="auto"`` or the upstream default 5 s per-engine timeout.

The agent runs from mainland China where the upstream ``auto`` engine list
fans out to Brave / Yahoo / Wikipedia / Mojeek — all of which either time
out or 403 — and only ``yandex`` / ``bing`` consistently return rows. The
production fallback now pins both ``backend`` and ``timeout`` so a single
``web_search`` call costs ~2 s instead of 5-7 s. If a future contributor
drops those overrides, this test fails before the regression ships."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from app.agents.tools import web_search as web_search_mod


class _StubDDGS:
    """Records the constructor kwargs and the ``text()`` kwargs so the test
    can assert the production overrides made it through."""

    last_ctor_kwargs: dict[str, Any] = {}
    last_text_kwargs: dict[str, Any] = {}

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        _StubDDGS.last_ctor_kwargs = kwargs

    def __enter__(self) -> "_StubDDGS":
        return self

    def __exit__(self, *exc: Any) -> None:
        return None

    def text(self, query: str, **kwargs: Any) -> list[dict[str, Any]]:
        _StubDDGS.last_text_kwargs = {"query": query, **kwargs}
        return [
            {
                "title": "stub",
                "href": "https://example.invalid/",
                "body": "stub snippet",
            }
        ]


@pytest.fixture
def stub_ddgs(monkeypatch: pytest.MonkeyPatch) -> type[_StubDDGS]:
    _StubDDGS.last_ctor_kwargs = {}
    _StubDDGS.last_text_kwargs = {}
    import ddgs

    monkeypatch.setattr(ddgs, "DDGS", _StubDDGS, raising=True)
    return _StubDDGS


def test_ddgs_fallback_pins_backend_to_cn_reachable_engines(
    stub_ddgs: type[_StubDDGS],
) -> None:
    """``web_search``'s no-key fallback must restrict ``backend`` to engines
    that have been observed to actually return rows from mainland China.
    The current allowlist is ``yandex,bing``; widening it back to ``auto``
    re-introduces the multi-second tax that triggered this fix."""
    result = asyncio.run(web_search_mod._ddgs("BYD stock", 5, None, None, None))

    assert result is not None
    assert stub_ddgs.last_text_kwargs.get("backend") == "yandex,bing"
    assert stub_ddgs.last_text_kwargs.get("max_results") == 5


def test_ddgs_fallback_pins_per_engine_timeout(stub_ddgs: type[_StubDDGS]) -> None:
    """The DDGS constructor must carry a tighter ``timeout`` than the upstream
    5 s default; otherwise a single stalled engine still drags the entire
    ``web_search`` call to ~5 s."""
    asyncio.run(web_search_mod._ddgs("BYD stock", 5, None, None, None))

    timeout = stub_ddgs.last_ctor_kwargs.get("timeout")
    assert isinstance(timeout, int)
    assert 1 <= timeout <= 4, (
        f"expected a tight per-engine timeout (<=4s), got {timeout}s"
    )


def test_ddgs_fallback_propagates_time_range(stub_ddgs: type[_StubDDGS]) -> None:
    """Time-range filters round-trip into the ddgs ``timelimit`` kwarg without
    being clobbered by the backend/timeout overrides above."""
    asyncio.run(web_search_mod._ddgs("BYD stock", 5, "week", None, None))

    assert stub_ddgs.last_text_kwargs.get("timelimit") == "w"
    assert stub_ddgs.last_text_kwargs.get("backend") == "yandex,bing"
