"""``http_request`` node — call an external HTTP endpoint.

``data`` shape::

    {
        "method":   "POST",                      # GET / POST / PUT / PATCH / DELETE
        "url":      "https://hooks.slack.com/..",  # templated
        "headers":  {"Authorization": "Bearer {{start.token}}"},
        "body":     "{\"text\": \"{{n2.text}}\"}",  # templated, JSON or raw
        "timeout":  10                             # seconds, 1-60
    }

Output::

    {
        "status":  int,
        "headers": {...},
        "body":    "<up to 40 KB of text>",
        "json":    <parsed json or null>
    }
"""

from __future__ import annotations

import json as _json
from typing import Any

import httpx

from app.core.url_safety import UnsafeURLError, assert_safe_url
from app.services.flow_nodes import NodeContext

_ALLOWED_METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE"}
_MAX_BODY_BYTES = 40 * 1024


async def run_http_request(ctx: NodeContext) -> dict:
    method = str(ctx.data.get("method") or "GET").upper()
    if method not in _ALLOWED_METHODS:
        raise ValueError(f"unsupported method: {method!r}")

    url = ctx.render_str(ctx.data.get("url"))
    if not url or not (url.startswith("http://") or url.startswith("https://")):
        raise ValueError(f"invalid url: {url!r}")
    # SSRF guard — Flow nodes are user-authored and would otherwise let a
    # workspace member dial cloud metadata (169.254.169.254), the host's
    # docker daemon, or other private RFC1918 endpoints. ``assert_safe_url``
    # resolves the host and refuses if any A/AAAA record lands in a
    # blocked range.
    try:
        url = assert_safe_url(url)
    except UnsafeURLError as e:
        raise ValueError(f"unsafe url ({e.code}): {e}") from e

    timeout = float(ctx.data.get("timeout") or 10.0)
    timeout = max(1.0, min(timeout, 60.0))

    raw_headers = ctx.data.get("headers") or {}
    headers: dict[str, str] = {}
    if isinstance(raw_headers, dict):
        for k, v in raw_headers.items():
            headers[str(k)] = ctx.render_str(str(v))

    body_template = ctx.data.get("body")
    body_rendered = ctx.render_str(body_template) if body_template else None

    # If Content-Type is absent and the body parses as JSON, add it — matches
    # n8n / Zapier ergonomics where users paste JSON and expect the right
    # header automatically.
    if body_rendered and "content-type" not in {k.lower() for k in headers}:
        stripped = body_rendered.strip()
        if stripped.startswith("{") or stripped.startswith("["):
            headers["Content-Type"] = "application/json"

    # Manual redirect handling so each hop is re-validated against the
    # SSRF block-list (a public 302→169.254.169.254 would otherwise sneak
    # through). Cap matches web_fetch tool.
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as c:
        resp = await _follow_with_ssrf_check(
            c, method, url, headers=headers, content=body_rendered, max_redirects=5
        )

    # Truncate huge bodies.
    text = resp.text or ""
    truncated = False
    if len(text) > _MAX_BODY_BYTES:
        text = text[:_MAX_BODY_BYTES] + "\n…[truncated]"
        truncated = True

    parsed: Any = None
    try:
        parsed = resp.json()
    except (ValueError, _json.JSONDecodeError):
        parsed = None

    return {
        "status": resp.status_code,
        "headers": dict(resp.headers),
        "body": text,
        "json": parsed,
        "truncated": truncated,
    }


async def _follow_with_ssrf_check(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    *,
    headers: dict[str, str],
    content: str | None,
    max_redirects: int,
) -> httpx.Response:
    """Walk the redirect chain manually, re-validating each hop."""
    current = url
    for _ in range(max_redirects + 1):
        assert_safe_url(current)
        resp = await client.request(method, current, headers=headers, content=content)
        if not resp.is_redirect:
            return resp
        loc = resp.headers.get("location")
        if not loc:
            return resp
        try:
            current = str(resp.next_request.url) if resp.next_request else loc
        except Exception:  # pragma: no cover - defensive
            current = loc
        # Per RFC, after a 30x most clients drop the body and switch to GET.
        # For 307/308 the method/body must be preserved — keep them as-is.
        if resp.status_code in (301, 302, 303):
            method = "GET"
            content = None
    raise httpx.TooManyRedirects(
        "Exceeded SSRF-safe redirect cap", request=resp.request
    )
