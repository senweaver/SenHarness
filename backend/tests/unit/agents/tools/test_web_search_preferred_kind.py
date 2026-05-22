"""Verify the agent-preferred search kind wins ties (M2.5.8).

When ``policy.default_search_provider_kind`` is set on the run, the
tool surfaces matching rows ahead of the workspace priority order.
Non-matching rows must keep their priority position so they still
serve as a fallback chain.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

from app.agents.tools import web_search as web_search_mod
from app.agents.tools._context import ToolRunContext, set_context


@dataclass
class _Row:
    id: uuid.UUID
    kind: str
    name: str
    priority: int
    created_at: datetime
    enabled: bool = True
    vault_item_id: uuid.UUID | None = None
    base_url: str | None = None


class _FakeRepo:
    def __init__(self, rows: list[_Row]):
        self._rows = rows

    async def list(self, **_: Any) -> list[_Row]:
        return list(self._rows)


class _FakeSession:
    def __init__(self, rows: list[_Row]):
        self._rows = rows

    async def __aenter__(self) -> _FakeSession:
        return self

    async def __aexit__(self, *exc: Any) -> None:
        return None


def _ctx(*, preferred: str | None) -> ToolRunContext:
    policy: dict[str, Any] = {}
    if preferred is not None:
        policy["default_search_provider_kind"] = preferred
    return ToolRunContext(
        run_id=uuid.uuid4(),
        workspace_id=uuid.uuid4(),
        session_id=uuid.uuid4(),
        identity_id=uuid.uuid4(),
        agent_id=uuid.uuid4(),
        scratch_base=Path("."),
        policy=policy,
    )


@pytest.fixture
def _patched_repo(monkeypatch: pytest.MonkeyPatch):
    rows = [
        _Row(
            id=uuid.uuid4(),
            kind="tavily",
            name="Tavily",
            priority=1,
            created_at=datetime(2026, 1, 1),
        ),
        _Row(
            id=uuid.uuid4(),
            kind="brave",
            name="Brave",
            priority=2,
            created_at=datetime(2026, 1, 2),
        ),
        _Row(
            id=uuid.uuid4(),
            kind="jina",
            name="Jina",
            priority=3,
            created_at=datetime(2026, 1, 3),
        ),
    ]

    def _factory() -> _FakeSession:
        return _FakeSession(rows)

    def _get_factory():
        return _factory

    monkeypatch.setattr(web_search_mod, "get_session_factory", _get_factory)

    import app.repositories.search_provider as sp_mod

    monkeypatch.setattr(sp_mod, "SearchProviderRepository", lambda _s: _FakeRepo(rows))
    yield rows


@pytest.mark.asyncio
async def test_preferred_kind_floats_to_top(_patched_repo):
    set_context(_ctx(preferred="jina"))
    try:
        result = await web_search_mod._ordered_candidates()
    finally:
        set_context(None)
    assert [k for (k, _, _) in result] == ["jina", "tavily", "brave"]


@pytest.mark.asyncio
async def test_no_preference_keeps_priority_order(_patched_repo):
    set_context(_ctx(preferred=None))
    try:
        result = await web_search_mod._ordered_candidates()
    finally:
        set_context(None)
    assert [k for (k, _, _) in result] == ["tavily", "brave", "jina"]


@pytest.mark.asyncio
async def test_unknown_preference_falls_back(_patched_repo):
    set_context(_ctx(preferred="not_configured"))
    try:
        result = await web_search_mod._ordered_candidates()
    finally:
        set_context(None)
    assert [k for (k, _, _) in result] == ["tavily", "brave", "jina"]
