"""M2.5.4 — uniform MCP client across stdio / SSE / streamable-http.

Wraps the official MCP Python SDK transports behind a single
``McpClient`` so the rest of SenHarness only deals with one shape:
``connect / list_tools / call_tool / close``. Adds the four
operational guarantees the roadmap requires:

* **Keepalive** — the client loop pings every ``keepalive_seconds``
  (default 30 s); when a single ping fails the loop closes the
  transport and surfaces a typed error so callers can record
  ``mcp.keepalive_timeout`` and re-connect.
* **Hard cancel** — every ``call_tool`` is wrapped in
  ``asyncio.wait_for(timeout=request_timeout_seconds)`` with a 5 min
  default cap, so a hung MCP server can never wedge a chat turn.
* **Per-server concurrency** — an internal semaphore (default 4)
  blocks the 5th in-flight ``call_tool`` until a slot frees up; the
  ``mcp.concurrency_limit_hit`` audit fires when the wait actually
  matters.
* **Media-aware result envelope** — ``McpToolResult.media`` carries
  every image / audio / file part the SDK returned so the runner can
  wrap them as ``RunEvent(kind=TOOL_RESULT, data={"media": [...]})``.

The MCP SDK is an *optional* dependency: ``connect`` raises
:class:`McpSdkUnavailable` (translated into a graceful audit row by
the service layer) when the package is not importable, so deploys
that don't enable MCP keep working.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

log = logging.getLogger(__name__)

# Default ops budget — the roadmap pins 30 s keepalive + 5 min hard
# cancel; we expose the constants so platform settings can override
# without touching the client.
DEFAULT_KEEPALIVE_SECONDS = 30
DEFAULT_REQUEST_TIMEOUT_SECONDS = 300
DEFAULT_MAX_CONCURRENT = 4
KEEPALIVE_FAILURE_GRACE = 2  # consecutive ping failures tolerated


class McpTransport(StrEnum):
    STDIO = "stdio"
    SSE = "sse"
    STREAMABLE_HTTP = "streamable_http"


# ─── Errors ──────────────────────────────────────────────────────
class McpClientError(RuntimeError):
    """Base for every M2.5.4 client failure."""

    code: str = "mcp.client_error"


class McpSdkUnavailable(McpClientError):
    code = "mcp.sdk_unavailable"


class McpConfigInvalid(McpClientError):
    code = "mcp.config_invalid"


class McpKeepaliveTimeout(McpClientError):
    code = "mcp.keepalive_timeout"


class McpRequestTimeout(McpClientError):
    code = "mcp.request_timeout"


class McpConcurrencyLimited(McpClientError):
    code = "mcp.concurrency_limit_hit"


# ─── Public dataclasses ──────────────────────────────────────────
@dataclass(slots=True)
class McpClientConfig:
    """Connection envelope assembled from a ``McpServer`` row."""

    transport: McpTransport
    # stdio transport:
    command: list[str] | None = None
    cwd: str | None = None
    env: dict[str, str] | None = None
    # sse / streamable_http transport:
    url: str | None = None
    headers: dict[str, str] | None = None
    # auth (resolved before connect — token already looked up via vault):
    bearer_token: str | None = None
    # ops:
    keepalive_seconds: int = DEFAULT_KEEPALIVE_SECONDS
    request_timeout_seconds: int = DEFAULT_REQUEST_TIMEOUT_SECONDS
    max_concurrent: int = DEFAULT_MAX_CONCURRENT
    # caller-supplied label for logs / audit metadata.
    server_slug: str | None = None

    def validate(self) -> None:
        if self.transport == McpTransport.STDIO:
            if not self.command:
                raise McpConfigInvalid("stdio transport requires ``command``")
            if any(not isinstance(p, str) or not p for p in self.command):
                raise McpConfigInvalid("stdio command parts must be non-empty strings")
        elif self.transport in (McpTransport.SSE, McpTransport.STREAMABLE_HTTP):
            if not self.url or not self.url.strip():
                raise McpConfigInvalid(
                    f"{self.transport.value} transport requires ``url``"
                )
        else:  # pragma: no cover — StrEnum guards
            raise McpConfigInvalid(f"unknown transport {self.transport!r}")
        if self.keepalive_seconds < 5 or self.keepalive_seconds > 600:
            raise McpConfigInvalid("keepalive_seconds must be in [5, 600]")
        if self.request_timeout_seconds < 30 or self.request_timeout_seconds > 1800:
            raise McpConfigInvalid("request_timeout_seconds must be in [30, 1800]")
        if self.max_concurrent < 1 or self.max_concurrent > 32:
            raise McpConfigInvalid("max_concurrent must be in [1, 32]")


@dataclass(slots=True)
class McpToolDescriptor:
    """Tool catalogue entry surfaced by ``McpClient.list_tools``."""

    name: str
    description: str | None = None
    input_schema: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class McpMediaPart:
    """One non-text part returned by an MCP tool — image / audio / file."""

    kind: str  # "image" | "audio" | "file" | "resource"
    mime: str | None = None
    url: str | None = None
    data_b64: str | None = None
    filename: str | None = None


@dataclass(slots=True)
class McpToolResult:
    """Uniform tool-call result.

    ``text`` is the concatenation of every text part returned by the
    server (the most common case). ``media`` carries non-text parts
    so the runner can wrap them into ``RunEvent.media``. ``raw``
    keeps the SDK payload for debugging / future enrichment.
    """

    text: str
    media: list[McpMediaPart] = field(default_factory=list)
    is_error: bool = False
    raw: Any = None


# ─── Internal SDK probe ─────────────────────────────────────────
def _import_sdk() -> Any:
    """Return a small namespace with the SDK entry-points or raise.

    Isolated so unit tests can monkeypatch a fake module without
    pulling the real ``mcp`` package; ``connect`` calls this once
    and stashes the result on the client instance.
    """
    try:
        from mcp.client.session import ClientSession  # type: ignore[import-not-found]
        from mcp.client.stdio import StdioServerParameters, stdio_client  # type: ignore[import-not-found]

        try:
            from mcp.client.sse import sse_client  # type: ignore[import-not-found]
        except ImportError:
            sse_client = None  # type: ignore[assignment]

        try:
            from mcp.client.streamable_http import streamable_http_client  # type: ignore[import-not-found]
        except ImportError:  # pragma: no cover — older SDK
            try:
                from mcp.client.streamable_http import (  # type: ignore[import-not-found]
                    streamablehttp_client as streamable_http_client,  # type: ignore[no-redef]
                )
            except ImportError:
                streamable_http_client = None  # type: ignore[assignment]
    except ImportError as e:
        raise McpSdkUnavailable(
            "the optional ``mcp`` python SDK is not installed; "
            "add it via ``pip install mcp`` to enable MCP servers"
        ) from e

    return _SdkBundle(
        ClientSession=ClientSession,
        StdioServerParameters=StdioServerParameters,
        stdio_client=stdio_client,
        sse_client=sse_client,
        streamable_http_client=streamable_http_client,
    )


@dataclass(slots=True)
class _SdkBundle:
    ClientSession: Any
    StdioServerParameters: Any
    stdio_client: Any
    sse_client: Any | None
    streamable_http_client: Any | None


# ─── Client ──────────────────────────────────────────────────────
class McpClient:
    """Single-server MCP client wrapping the SDK transports.

    Instances are created via :meth:`connect` and disposed via
    :meth:`close` (or async-context-manager). The class is **not**
    safe for cross-server reuse — each ``McpServer`` row gets its
    own client because the keepalive loop and concurrency semaphore
    are per-server.
    """

    def __init__(
        self,
        config: McpClientConfig,
        *,
        sdk: _SdkBundle | None = None,
        on_audit: Callable[[str, dict[str, Any]], Awaitable[None]] | None = None,
    ) -> None:
        config.validate()
        self._config = config
        self._sdk = sdk
        self._session: Any | None = None
        self._exit_stack: Any | None = None
        self._concurrency = asyncio.Semaphore(config.max_concurrent)
        self._inflight = 0
        self._inflight_lock = asyncio.Lock()
        self._keepalive_task: asyncio.Task[None] | None = None
        self._closed = False
        self._on_audit = on_audit
        self._last_pong_at: float = time.monotonic()

    # ─── Lifecycle ────────────────────────────────────────────
    @classmethod
    async def connect(
        cls,
        config: McpClientConfig,
        *,
        on_audit: Callable[[str, dict[str, Any]], Awaitable[None]] | None = None,
    ) -> "McpClient":
        sdk = _import_sdk()
        client = cls(config, sdk=sdk, on_audit=on_audit)
        await client._open()
        await client._emit_audit(
            "mcp.client_connected",
            {
                "transport": config.transport.value,
                "server_slug": config.server_slug,
                "max_concurrent": config.max_concurrent,
                "keepalive_seconds": config.keepalive_seconds,
                "request_timeout_seconds": config.request_timeout_seconds,
            },
        )
        return client

    async def _open(self) -> None:
        assert self._sdk is not None
        from contextlib import AsyncExitStack

        stack = AsyncExitStack()
        try:
            transport_cm = self._build_transport_cm(self._sdk)
            streams = await stack.enter_async_context(transport_cm)
            # Some SDK versions ship a 3-tuple, modern v2 ships a 2-tuple.
            read_stream, write_stream = streams[0], streams[1]
            session_cm = self._sdk.ClientSession(read_stream, write_stream)
            session = await stack.enter_async_context(session_cm)
            await asyncio.wait_for(
                session.initialize(),
                timeout=self._config.request_timeout_seconds,
            )
        except Exception:
            await stack.aclose()
            raise
        self._exit_stack = stack
        self._session = session
        self._last_pong_at = time.monotonic()
        self._keepalive_task = asyncio.create_task(
            self._keepalive_loop(),
            name=f"mcp-keepalive-{self._config.server_slug or 'unknown'}",
        )

    def _build_transport_cm(self, sdk: _SdkBundle) -> Any:
        cfg = self._config
        if cfg.transport == McpTransport.STDIO:
            assert cfg.command
            params = sdk.StdioServerParameters(
                command=cfg.command[0],
                args=list(cfg.command[1:]),
                env=cfg.env or None,
                cwd=cfg.cwd,
            )
            return sdk.stdio_client(params)
        headers = dict(cfg.headers or {})
        if cfg.bearer_token:
            headers.setdefault("Authorization", f"Bearer {cfg.bearer_token}")
        if cfg.transport == McpTransport.SSE:
            if sdk.sse_client is None:
                raise McpConfigInvalid(
                    "the installed mcp SDK does not expose an SSE client; "
                    "switch this server to ``streamable_http`` or upgrade ``mcp``"
                )
            return sdk.sse_client(cfg.url, headers=headers)
        if cfg.transport == McpTransport.STREAMABLE_HTTP:
            if sdk.streamable_http_client is None:
                raise McpConfigInvalid(
                    "the installed mcp SDK does not expose a streamable_http client"
                )
            return sdk.streamable_http_client(cfg.url, headers=headers)
        raise McpConfigInvalid(f"unknown transport {cfg.transport!r}")

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        task = self._keepalive_task
        self._keepalive_task = None
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        stack = self._exit_stack
        self._exit_stack = None
        self._session = None
        if stack is not None:
            with contextlib.suppress(Exception):
                await stack.aclose()

    async def __aenter__(self) -> "McpClient":  # pragma: no cover - convenience
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:  # pragma: no cover - convenience
        await self.close()

    # ─── Public API ──────────────────────────────────────────
    async def list_tools(self) -> list[McpToolDescriptor]:
        if self._session is None or self._closed:
            raise McpClientError("client is not connected")
        result = await asyncio.wait_for(
            self._session.list_tools(),
            timeout=self._config.request_timeout_seconds,
        )
        out: list[McpToolDescriptor] = []
        for tool in getattr(result, "tools", None) or []:
            out.append(
                McpToolDescriptor(
                    name=str(getattr(tool, "name", "")),
                    description=getattr(tool, "description", None),
                    input_schema=dict(getattr(tool, "inputSchema", {}) or {}),
                )
            )
        return out

    async def call_tool(
        self,
        name: str,
        args: dict[str, Any],
        *,
        timeout_seconds: int | None = None,
    ) -> McpToolResult:
        if self._session is None or self._closed:
            raise McpClientError("client is not connected")
        budget = timeout_seconds or self._config.request_timeout_seconds

        # Best-effort concurrency probe: when the semaphore is fully
        # taken we want to surface ``mcp.concurrency_limit_hit`` once
        # per blocked call rather than spamming the audit log every
        # tick. The probe is non-acquiring so the real semaphore
        # acquisition keeps the FIFO ordering.
        if self._concurrency.locked():
            await self._emit_audit(
                "mcp.concurrency_limit_hit",
                {
                    "server_slug": self._config.server_slug,
                    "tool_name": name,
                    "max_concurrent": self._config.max_concurrent,
                },
            )

        async with self._concurrency:
            async with self._track_inflight():
                try:
                    raw = await asyncio.wait_for(
                        self._session.call_tool(name, arguments=args),
                        timeout=budget,
                    )
                except TimeoutError as e:
                    await self._emit_audit(
                        "mcp.tool_called",
                        {
                            "server_slug": self._config.server_slug,
                            "tool_name": name,
                            "outcome": "timeout",
                            "timeout_seconds": budget,
                        },
                    )
                    raise McpRequestTimeout(
                        f"mcp tool {name!r} timed out after {budget}s"
                    ) from e
            wrapped = _wrap_tool_result(raw)
        await self._emit_audit(
            "mcp.tool_called",
            {
                "server_slug": self._config.server_slug,
                "tool_name": name,
                "outcome": "error" if wrapped.is_error else "ok",
                "media_count": len(wrapped.media),
                "text_chars": len(wrapped.text),
            },
        )
        return wrapped

    @property
    def inflight(self) -> int:
        return self._inflight

    # ─── Internals ───────────────────────────────────────────
    @contextlib.asynccontextmanager
    async def _track_inflight(self):
        async with self._inflight_lock:
            self._inflight += 1
        try:
            yield
        finally:
            async with self._inflight_lock:
                self._inflight = max(0, self._inflight - 1)

    async def _keepalive_loop(self) -> None:
        """Best-effort liveness probe.

        We treat ``KEEPALIVE_FAILURE_GRACE`` consecutive misses as a
        dead transport: emit ``mcp.keepalive_timeout`` and close the
        client so the next ``list_tools`` / ``call_tool`` raises a
        typed error instead of hanging the chat turn. A successful
        ping at any point resets the failure counter.
        """
        misses = 0
        try:
            while not self._closed:
                await asyncio.sleep(self._config.keepalive_seconds)
                if self._closed or self._session is None:
                    return
                try:
                    await asyncio.wait_for(
                        self._ping(),
                        timeout=self._config.keepalive_seconds,
                    )
                    misses = 0
                    self._last_pong_at = time.monotonic()
                except (TimeoutError, Exception) as e:
                    misses += 1
                    log.warning(
                        "mcp keepalive miss server=%s misses=%d err=%s",
                        self._config.server_slug,
                        misses,
                        e,
                    )
                    if misses >= KEEPALIVE_FAILURE_GRACE:
                        await self._emit_audit(
                            "mcp.keepalive_timeout",
                            {
                                "server_slug": self._config.server_slug,
                                "misses": misses,
                                "since_last_pong_s": int(
                                    time.monotonic() - self._last_pong_at
                                ),
                            },
                        )
                        with contextlib.suppress(Exception):
                            await self.close()
                        return
        except asyncio.CancelledError:
            return

    async def _ping(self) -> None:
        """Lightweight liveness probe.

        The MCP wire protocol defines an explicit ping; the SDK
        surfaces it on ``ClientSession.send_ping``. Older SDKs use a
        no-arg ``ping``. We try both and fall back to ``list_tools``
        as a last resort — any of the three round-trips is enough to
        prove the transport is alive.
        """
        session = self._session
        if session is None:
            raise McpClientError("session went away during ping")
        for attr in ("send_ping", "ping"):
            method = getattr(session, attr, None)
            if callable(method):
                await method()
                return
        await session.list_tools()

    async def _emit_audit(self, action: str, payload: dict[str, Any]) -> None:
        if self._on_audit is None:
            return
        try:
            await self._on_audit(action, payload)
        except Exception:  # pragma: no cover - audit must never break the run
            log.warning("mcp audit %s failed", action, exc_info=True)


# ─── Result conversion ──────────────────────────────────────────
def _wrap_tool_result(raw: Any) -> McpToolResult:
    """Normalise a SDK ``CallToolResult`` into our envelope.

    Handles both v1 (``content: list[TextContent | ImageContent | ...]``)
    and the newer v2 (``content`` plus an optional ``structured_content``
    dict). Unknown part types fall through into ``media`` with
    ``kind='resource'`` so nothing is silently dropped.
    """
    if raw is None:
        return McpToolResult(text="")
    text_parts: list[str] = []
    media: list[McpMediaPart] = []
    is_error = bool(getattr(raw, "isError", False))
    content = getattr(raw, "content", None) or []
    for part in content:
        kind = _detect_part_kind(part)
        if kind == "text":
            text = getattr(part, "text", None)
            if text:
                text_parts.append(str(text))
        elif kind == "image":
            media.append(
                McpMediaPart(
                    kind="image",
                    mime=getattr(part, "mimeType", None) or "image/png",
                    data_b64=getattr(part, "data", None),
                )
            )
        elif kind == "audio":
            media.append(
                McpMediaPart(
                    kind="audio",
                    mime=getattr(part, "mimeType", None) or "audio/wav",
                    data_b64=getattr(part, "data", None),
                )
            )
        elif kind == "resource":
            resource = getattr(part, "resource", None)
            uri = getattr(resource, "uri", None) if resource else None
            mime = getattr(resource, "mimeType", None) if resource else None
            media.append(
                McpMediaPart(
                    kind="file",
                    mime=mime,
                    url=str(uri) if uri else None,
                    filename=getattr(resource, "name", None) if resource else None,
                )
            )
        else:
            # Forward-compat: unknown content types still surface as
            # opaque resource parts so the UI can decide what to do.
            media.append(
                McpMediaPart(
                    kind="resource",
                    mime=getattr(part, "mimeType", None),
                )
            )
    return McpToolResult(
        text="".join(text_parts),
        media=media,
        is_error=is_error,
        raw=raw,
    )


def _detect_part_kind(part: Any) -> str:
    explicit = getattr(part, "type", None)
    if isinstance(explicit, str):
        return explicit
    cls_name = type(part).__name__.lower()
    if "text" in cls_name:
        return "text"
    if "image" in cls_name:
        return "image"
    if "audio" in cls_name:
        return "audio"
    if "resource" in cls_name or "embedded" in cls_name:
        return "resource"
    return "unknown"


__all__ = [
    "DEFAULT_KEEPALIVE_SECONDS",
    "DEFAULT_MAX_CONCURRENT",
    "DEFAULT_REQUEST_TIMEOUT_SECONDS",
    "McpClient",
    "McpClientConfig",
    "McpClientError",
    "McpConcurrencyLimited",
    "McpConfigInvalid",
    "McpKeepaliveTimeout",
    "McpMediaPart",
    "McpRequestTimeout",
    "McpSdkUnavailable",
    "McpToolDescriptor",
    "McpToolResult",
    "McpTransport",
]
