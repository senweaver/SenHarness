"""Integration: M2.5.4 ``McpClient`` lifecycle against a fake SDK.

The real ``mcp`` SDK is an optional install — exercising the
keepalive loop, concurrency cap, and timeout-cancel guards against
the network would mean spinning up an MCP server in CI. We instead
substitute the SDK probe with a typed fake module, which keeps the
test fast and deterministic while still proving the
``McpClient.connect → list_tools → call_tool → close`` contract.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any

import pytest

from app.services import mcp_client as mcp_client_module
from app.services.mcp_client import (
    KEEPALIVE_FAILURE_GRACE,
    McpClient,
    McpClientConfig,
    McpRequestTimeout,
    McpTransport,
)

pytestmark = pytest.mark.asyncio


# ─── Fake SDK pieces ───────────────────────────────────────────
class _FakeStreams:
    """Stand-in for the (read, write) tuple yielded by transport CMs."""

    def __init__(self) -> None:
        self.events: list[str] = []


@dataclass(slots=True)
class _FakeTextPart:
    text: str
    type: str = "text"


@dataclass(slots=True)
class _FakeImagePart:
    data: str
    mimeType: str = "image/png"  # noqa: N815 - SDK shape
    type: str = "image"


@dataclass(slots=True)
class _FakeAudioPart:
    data: str
    mimeType: str = "audio/wav"  # noqa: N815 - SDK shape
    type: str = "audio"


@dataclass(slots=True)
class _FakeToolResult:
    content: list[Any]
    isError: bool = False  # noqa: N815 - SDK shape


@dataclass(slots=True)
class _FakeToolDescriptor:
    name: str
    description: str | None = None
    inputSchema: dict = field(default_factory=dict)  # noqa: N815 - SDK shape


@dataclass(slots=True)
class _FakeListToolsResult:
    tools: list[_FakeToolDescriptor]


class _FakeSession:
    def __init__(self, *, behaviour: dict[str, Any]) -> None:
        self._behaviour = behaviour
        self.initialize_calls = 0
        self.ping_calls = 0
        self.list_calls = 0
        self.call_calls: list[tuple[str, dict]] = []
        self.closed = False

    async def __aenter__(self) -> "_FakeSession":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        self.closed = True

    async def initialize(self) -> None:
        self.initialize_calls += 1

    async def send_ping(self) -> None:
        self.ping_calls += 1
        cb = self._behaviour.get("on_ping")
        if cb is not None:
            await cb(self.ping_calls)

    async def list_tools(self) -> _FakeListToolsResult:
        self.list_calls += 1
        return _FakeListToolsResult(
            tools=[
                _FakeToolDescriptor(
                    name="echo",
                    description="echoes back",
                    inputSchema={"type": "object"},
                ),
                _FakeToolDescriptor(name="image_capture"),
            ]
        )

    async def call_tool(self, name: str, *, arguments: dict) -> _FakeToolResult:
        self.call_calls.append((name, arguments))
        cb = self._behaviour.get("on_call_tool")
        if cb is not None:
            return await cb(name, arguments)
        return _FakeToolResult(
            content=[_FakeTextPart(text=f"called {name}")],
        )


@asynccontextmanager
async def _fake_sse_client(url: str, *, headers: dict | None = None):
    # The headers param is asserted on by callers below.
    streams = _FakeStreams()
    yield (streams, streams)


@asynccontextmanager
async def _fake_streamable_http_client(url: str, *, headers: dict | None = None):
    streams = _FakeStreams()
    yield (streams, streams)


@asynccontextmanager
async def _fake_stdio_client(_params):
    streams = _FakeStreams()
    yield (streams, streams)


def _build_fake_sdk(behaviour: dict[str, Any]):
    fake_session = _FakeSession(behaviour=behaviour)

    def _client_session_factory(read, write):
        return fake_session

    fake = mcp_client_module._SdkBundle(
        ClientSession=_client_session_factory,
        StdioServerParameters=lambda **kw: kw,
        stdio_client=_fake_stdio_client,
        sse_client=_fake_sse_client,
        streamable_http_client=_fake_streamable_http_client,
    )
    return fake, fake_session


@pytest.fixture
def fake_sdk(monkeypatch):
    state: dict[str, Any] = {"behaviour": {}, "session": None}

    def _patch(behaviour: dict[str, Any] | None = None) -> _FakeSession:
        state["behaviour"] = dict(behaviour or {})
        bundle, session = _build_fake_sdk(state["behaviour"])
        monkeypatch.setattr(
            mcp_client_module, "_import_sdk", lambda: bundle
        )
        state["session"] = session
        return session

    return _patch


# ─── Tests ─────────────────────────────────────────────────────
async def test_connect_lists_tools_and_call_tool_happy(fake_sdk):
    fake_session = fake_sdk()
    config = McpClientConfig(
        transport=McpTransport.SSE,
        url="https://mcp.example.com/sse",
        bearer_token="abc",
        keepalive_seconds=120,
        request_timeout_seconds=60,
        server_slug="happy",
    )
    audit_calls: list[tuple[str, dict]] = []

    async def _audit(action: str, payload: dict) -> None:
        audit_calls.append((action, payload))

    client = await McpClient.connect(config, on_audit=_audit)

    tools = await client.list_tools()
    assert [t.name for t in tools] == ["echo", "image_capture"]
    result = await client.call_tool("echo", {"x": 1})
    assert result.text == "called echo"
    assert result.media == []
    assert fake_session.initialize_calls == 1
    assert fake_session.call_calls == [("echo", {"x": 1})]

    actions = [a for a, _ in audit_calls]
    assert "mcp.client_connected" in actions
    assert "mcp.tool_called" in actions

    await client.close()


async def test_call_tool_request_timeout_translates(fake_sdk):
    async def _slow(name, args):
        await asyncio.sleep(5)
        raise AssertionError("should have timed out")

    fake_sdk({"on_call_tool": _slow})
    config = McpClientConfig(
        transport=McpTransport.STREAMABLE_HTTP,
        url="https://mcp.example.com/mcp",
        keepalive_seconds=120,
        request_timeout_seconds=60,
    )
    client = await McpClient.connect(config)
    try:
        with pytest.raises(McpRequestTimeout):
            await client.call_tool("slow", {}, timeout_seconds=1)
    finally:
        await client.close()


async def test_keepalive_closes_after_consecutive_failures(fake_sdk, monkeypatch):
    # Capture the real sleeper so the patched implementation can yield
    # back to the loop without re-entering itself.
    real_sleep = asyncio.sleep
    sleeps: list[float] = []

    async def _instant_sleep(seconds: float) -> None:
        sleeps.append(seconds)
        await real_sleep(0)

    monkeypatch.setattr(mcp_client_module.asyncio, "sleep", _instant_sleep)

    async def _ping_always_raises(_count: int) -> None:
        raise RuntimeError("transport dead")

    fake_sdk({"on_ping": _ping_always_raises})
    audit_calls: list[tuple[str, dict]] = []

    async def _audit(action: str, payload: dict) -> None:
        audit_calls.append((action, payload))

    config = McpClientConfig(
        transport=McpTransport.SSE,
        url="https://mcp.example.com/sse",
        keepalive_seconds=30,
        request_timeout_seconds=60,
        server_slug="dead-canary",
    )
    client = await McpClient.connect(config, on_audit=_audit)
    # Wait for the keepalive loop to flag the timeout and close.
    for _ in range(200):
        if client._closed:
            break
        await real_sleep(0)
    assert client._closed
    actions = [a for a, _ in audit_calls]
    assert "mcp.keepalive_timeout" in actions
    timeout_event = [
        payload for action, payload in audit_calls if action == "mcp.keepalive_timeout"
    ][0]
    assert timeout_event["misses"] >= KEEPALIVE_FAILURE_GRACE


async def test_concurrency_audit_emitted_when_semaphore_full(fake_sdk):
    barrier = asyncio.Event()
    in_flight: list[str] = []

    async def _hold(name, args):
        in_flight.append(name)
        await barrier.wait()
        return _FakeToolResult(
            content=[_FakeTextPart(text=f"done {name}")],
        )

    fake_sdk({"on_call_tool": _hold})
    audit_calls: list[tuple[str, dict]] = []

    async def _audit(action, payload) -> None:
        audit_calls.append((action, payload))

    config = McpClientConfig(
        transport=McpTransport.SSE,
        url="https://mcp.example.com/sse",
        keepalive_seconds=120,
        request_timeout_seconds=60,
        max_concurrent=1,
    )
    client = await McpClient.connect(config, on_audit=_audit)

    async def _call(name: str):
        return await client.call_tool(name, {}, timeout_seconds=30)

    first = asyncio.create_task(_call("a"))
    # Wait for the first to actually be in-flight.
    while not in_flight:
        await asyncio.sleep(0)
    second = asyncio.create_task(_call("b"))
    # Give the second call a tick to attempt acquisition.
    await asyncio.sleep(0)
    barrier.set()

    await asyncio.gather(first, second)
    actions = [a for a, _ in audit_calls]
    assert "mcp.concurrency_limit_hit" in actions

    await client.close()


async def test_streamable_http_attaches_bearer_header(fake_sdk):
    captured: dict[str, Any] = {}

    @asynccontextmanager
    async def _capture(url, *, headers=None):
        captured["url"] = url
        captured["headers"] = headers
        streams = _FakeStreams()
        yield (streams, streams)

    behaviour: dict[str, Any] = {}
    bundle, _ = _build_fake_sdk(behaviour)
    bundle.streamable_http_client = _capture
    import app.services.mcp_client as mod

    original = mod._import_sdk
    mod._import_sdk = lambda: bundle  # type: ignore[assignment]
    try:
        config = McpClientConfig(
            transport=McpTransport.STREAMABLE_HTTP,
            url="https://mcp.example.com/mcp",
            bearer_token="tok-abc",
            keepalive_seconds=120,
            request_timeout_seconds=60,
        )
        client = await McpClient.connect(config)
        await client.close()
    finally:
        mod._import_sdk = original  # type: ignore[assignment]

    assert captured["url"] == "https://mcp.example.com/mcp"
    assert captured["headers"]["Authorization"] == "Bearer tok-abc"
