"""Unit tests for the discover-models internals.

Covers the parts of ``app.services.provider`` that don't touch the database:

  - ``_fetch_remote_models`` with both the OpenAI-shaped ``{data: [...]}``
    response and the bare-list shape some self-hosted gateways use.
  - ``_static_models_for`` falls back to the catalog list (and finds
    aliases like ``moonshotai`` → ``moonshot``).
  - ``_merge_with_catalog`` enriches discover results with ``family``,
    ``recommended``, and ``in_db`` metadata.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from app.services import provider as svc


class _StubResponse:
    """Minimal subset of ``httpx.Response`` we exercise."""

    def __init__(self, *, status_code: int = 200, body: Any) -> None:
        self.status_code = status_code
        self._body = body

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                "stub", request=httpx.Request("GET", "http://example"),
                response=httpx.Response(self.status_code),
            )

    def json(self) -> Any:
        return self._body


class _StubClient:
    """Drop-in for ``httpx.AsyncClient`` used as ``async with``."""

    def __init__(self, response: _StubResponse) -> None:
        self._response = response
        self.last_url: str | None = None
        self.last_headers: dict[str, str] | None = None

    async def __aenter__(self) -> _StubClient:
        return self

    async def __aexit__(self, *_: Any) -> None:
        pass

    async def get(
        self, url: str, *, headers: dict[str, str] | None = None
    ) -> _StubResponse:
        self.last_url = url
        self.last_headers = headers
        return self._response


@pytest.mark.asyncio
async def test_fetch_remote_models_openai_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    body = {
        "data": [
            {"id": "gpt-5", "context_length": 200000},
            {"id": "gpt-5-mini"},
            {"id": "", "should_be_skipped": True},  # empty id, dropped
            {"unrelated": "no id no name no model"},  # nothing extractable, dropped
        ]
    }
    stub = _StubClient(_StubResponse(body=body))
    monkeypatch.setattr(httpx, "AsyncClient", lambda *_a, **_kw: stub)

    rows = await svc._fetch_remote_models(
        "https://api.example.com/v1/models", api_key="sk-test"
    )

    assert [r["model"] for r in rows] == ["gpt-5", "gpt-5-mini"]
    assert rows[0]["context_window"] == 200000
    assert stub.last_headers is not None
    assert stub.last_headers["Authorization"] == "Bearer sk-test"


@pytest.mark.asyncio
async def test_fetch_remote_models_bare_list_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    """Some self-hosted gateways return a bare list instead of ``{data: [...]}``."""
    body = [
        {"id": "qwen-72b", "context_window": 32768},
        {"id": "llama-3-70b"},
    ]
    stub = _StubClient(_StubResponse(body=body))
    monkeypatch.setattr(httpx, "AsyncClient", lambda *_a, **_kw: stub)

    rows = await svc._fetch_remote_models(
        "http://localhost:8000/v1/models", api_key="anything"
    )
    assert [r["model"] for r in rows] == ["qwen-72b", "llama-3-70b"]
    assert rows[0]["context_window"] == 32768


@pytest.mark.asyncio
async def test_fetch_remote_models_empty_or_garbage(monkeypatch: pytest.MonkeyPatch) -> None:
    stub = _StubClient(_StubResponse(body={"unrelated": "shape"}))
    monkeypatch.setattr(httpx, "AsyncClient", lambda *_a, **_kw: stub)

    rows = await svc._fetch_remote_models(
        "https://example.com/v1/models", api_key="x"
    )
    assert rows == []


def test_static_models_for_known_provider() -> None:
    rows = svc._static_models_for("deepseek")
    models = {r["model"] for r in rows}
    assert "deepseek-chat" in models
    assert any(r["recommended"] for r in rows), "expect at least one recommended row"


def test_static_models_for_alias_falls_back() -> None:
    """``moonshotai`` is a pydantic-ai alias; the catalog stores rows under ``moonshot``."""
    rows = svc._static_models_for("moonshotai")
    assert rows, "alias should resolve via pydantic_ai_kind"
    assert any("kimi" in r["model"].lower() or "moonshot" in r["model"].lower() for r in rows)


def test_static_models_for_unknown_kind() -> None:
    assert svc._static_models_for("does-not-exist-x") == []


def test_merge_with_catalog_marks_existing_rows() -> None:
    discovered = [
        {"model": "gpt-5.5", "label": None, "context_window": None},
        {"model": "gpt-5.4-mini", "label": None, "context_window": None},
        {"model": "totally-new-2099", "label": None, "context_window": None},
    ]
    merged = svc._merge_with_catalog("openai", discovered, existing_ids=["gpt-5.4-mini"])
    by_model = {r["model"]: r for r in merged}

    # ``gpt-5.4-mini`` is recommended in CATALOG → preserved.
    assert by_model["gpt-5.4-mini"]["recommended"] is True
    assert by_model["gpt-5.4-mini"]["in_db"] is True
    assert by_model["gpt-5.4-mini"]["family"] == "balanced"

    # ``gpt-5.5`` is in catalog but not yet in DB.
    assert by_model["gpt-5.5"]["in_db"] is False
    assert by_model["gpt-5.5"]["family"] == "frontier"

    # Unknown future model: enriched with empty metadata, not crashed.
    assert by_model["totally-new-2099"]["family"] is None
    assert by_model["totally-new-2099"]["recommended"] is False


def test_merge_orders_recommended_first() -> None:
    discovered = [
        {"model": "z-non-recommended"},
        {"model": "gpt-5.4-mini"},  # recommended in catalog
        {"model": "a-non-recommended"},
    ]
    merged = svc._merge_with_catalog("openai", discovered, existing_ids=[])
    assert merged[0]["model"] == "gpt-5.4-mini", "recommended must surface first"
