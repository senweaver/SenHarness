"""Integration test for ``GET /api/v1/agents/runtimes``.

The endpoint is the single source of truth for the "which runtimes are
registered in this deployment" view — the Agent-create form and the
workspace runtime picker both read it. Break its shape and multiple UI
surfaces break in lockstep, so this test pins the contract.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.asyncio


async def test_runtimes_endpoint_lists_bundled(async_client):
    resp = await async_client.get("/api/v1/agents/runtimes")
    assert resp.status_code == 200

    payload = resp.json()
    assert "runtimes" in payload
    assert "count" in payload
    assert payload["count"] == len(payload["runtimes"])

    # The two adapters that ship with the box must always show up.
    kinds = {r["kind"] for r in payload["runtimes"]}
    assert "native" in kinds, "native runtime must be registered"
    assert "openclaw" in kinds, "openclaw runtime must be registered"


async def test_runtimes_shape_is_ui_stable(async_client):
    """Every row carries the fields the frontend needs to render
    without defensive null-checks. Breaking a field name here means
    fixing the frontend in the same PR.
    """
    resp = await async_client.get("/api/v1/agents/runtimes")
    resp.raise_for_status()
    rows = resp.json()["runtimes"]
    assert rows, "at least one runtime must be registered"

    for row in rows:
        assert isinstance(row["kind"], str)
        assert isinstance(row["display_name"], str)
        assert isinstance(row["description"], str)
        assert isinstance(row["docs_url"], str)
        assert isinstance(row["requires_adapter"], bool)
        caps = row["capabilities"]
        assert isinstance(caps["supports_streaming"], bool)
        assert isinstance(caps["supports_parallel_tools"], bool)
        assert isinstance(caps["supports_thinking"], bool)
        assert isinstance(caps["supports_native_mcp"], bool)
        assert isinstance(caps["supports_vision"], bool)
        # max_context_tokens is either None or an int
        mct = caps["max_context_tokens"]
        assert mct is None or isinstance(mct, int)


async def test_runtimes_native_is_in_process(async_client):
    resp = await async_client.get("/api/v1/agents/runtimes")
    rows = resp.json()["runtimes"]
    native = next(r for r in rows if r["kind"] == "native")
    assert native["requires_adapter"] is False
    # Native backend supports the full capability set.
    assert native["capabilities"]["supports_streaming"] is True
    assert native["capabilities"]["supports_vision"] is True


async def test_runtimes_openclaw_needs_adapter(async_client):
    resp = await async_client.get("/api/v1/agents/runtimes")
    rows = resp.json()["runtimes"]
    oc = next(r for r in rows if r["kind"] == "openclaw")
    assert oc["requires_adapter"] is True
