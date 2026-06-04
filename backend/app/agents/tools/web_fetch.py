"""`web_fetch` — fetch a URL and return clean markdown.

Uses trafilatura for HTML→markdown extraction (drops nav/footer/ads).
Falls back to raw text for non-HTML content. Enforces:
  - scheme must be http / https
  - redirect follow with cap = 5
  - response body capped at 2 MiB
  - per-call timeout 20s
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx
from pydantic import BaseModel, Field, field_validator

from app.core.url_safety import UnsafeURLError, assert_safe_url

log = logging.getLogger(__name__)

MAX_BYTES = 2 * 1024 * 1024  # 2 MiB
MAX_OUTPUT_CHARS = 40_000  # clamp markdown length so it doesn't blow context
DEFAULT_TIMEOUT = 20.0


class WebFetchArgs(BaseModel):
    url: str = Field(..., description="Absolute http(s) URL to fetch.")
    format: str = Field(
        default="markdown",
        description="Output format: 'markdown' (default, cleaned) | 'text' | 'html_raw'.",
    )
    max_chars: int = Field(
        default=MAX_OUTPUT_CHARS,
        ge=500,
        le=MAX_OUTPUT_CHARS,
        description="Clamp output to at most this many characters.",
    )

    @field_validator("url")
    @classmethod
    def _validate_url(cls, v: str) -> str:
        # Structural + SSRF validation happens here so a bad URL never even
        # reaches ``run_web_fetch``. DNS resolution runs inside
        # ``assert_safe_url`` to catch CNAMEs that point at loopback.
        try:
            return assert_safe_url(v)
        except UnsafeURLError as e:
            raise ValueError(str(e)) from e


async def run_web_fetch(args: WebFetchArgs) -> dict:
    # Re-check right before the request — the URL may have been
    # constructed elsewhere (e.g. bypassing the Pydantic model) and
    # redirects need to be validated one hop at a time below.
    try:
        assert_safe_url(args.url)
    except UnsafeURLError as e:
        log.info("web_fetch blocked unsafe URL %r: %s", args.url, e.code)
        return {"url": args.url, "ok": False, "error": e.code, "message": str(e)}

    try:
        async with httpx.AsyncClient(
            timeout=DEFAULT_TIMEOUT,
            # follow_redirects=False — we do it manually so we can re-validate
            # every hop (a redirect to http://169.254.169.254 must be blocked
            # even if the initial URL was public).
            follow_redirects=False,
            headers={
                "User-Agent": ("SenHarnessBot/0.1 (+https://senharness.app; web_fetch tool)"),
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            },
        ) as cli:
            resp = await _follow_with_ssrf_check(cli, args.url, max_redirects=5)
    except UnsafeURLError as e:
        log.info("web_fetch blocked redirect to unsafe URL: %s", e.code)
        return {"url": args.url, "ok": False, "error": e.code, "message": str(e)}
    except httpx.HTTPError as e:
        fallback = await _jina_reader_fetch(args.url, args.max_chars)
        if fallback is not None:
            return fallback
        return {"url": args.url, "ok": False, "error": f"http_error: {e!s}"}

    if resp.status_code >= 400:
        fallback = await _jina_reader_fetch(args.url, args.max_chars)
        if fallback is not None:
            return fallback
        return {
            "url": args.url,
            "ok": False,
            "status": resp.status_code,
            "error": f"http_status_{resp.status_code}",
        }

    content_type = (resp.headers.get("content-type") or "").lower()
    raw_bytes = resp.content
    if len(raw_bytes) > MAX_BYTES:
        raw_bytes = raw_bytes[:MAX_BYTES]
        truncated_body = True
    else:
        truncated_body = False

    is_html = "html" in content_type or "xml" in content_type
    decoded = _decode_bytes(raw_bytes, resp.encoding)

    if args.format == "html_raw" or not is_html:
        body = decoded[: args.max_chars]
        return {
            "url": str(resp.url),
            "ok": True,
            "status": resp.status_code,
            "content_type": content_type,
            "format": "text" if not is_html else "html_raw",
            "title": None,
            "body": body,
            "truncated_body": truncated_body or len(decoded) > args.max_chars,
        }

    # trafilatura does clean HTML→markdown extraction.
    markdown, title = await asyncio.to_thread(_extract_markdown, decoded, args.format)
    output = (markdown or "").strip()
    # Empty extraction on a 200 page usually means an anti-bot / JS-only
    # shell. The Jina reader renders such pages server-side, so try it
    # before returning a blank body.
    if not output:
        fallback = await _jina_reader_fetch(args.url, args.max_chars)
        if fallback is not None:
            return fallback
    if len(output) > args.max_chars:
        output = output[: args.max_chars]
        truncated_body = True

    return {
        "url": str(resp.url),
        "ok": True,
        "status": resp.status_code,
        "content_type": content_type,
        "format": args.format,
        "title": title,
        "body": output,
        "truncated_body": truncated_body,
    }


async def _jina_reader_fetch(url: str, max_chars: int) -> dict | None:
    """Fallback reader proxy via ``r.jina.ai`` (no API key, free tier).

    Reaches pages the direct fetch can't — slow / blocked origins (common
    from mainland China) and anti-bot or JS-only shells — by letting Jina
    render and clean them server-side into markdown. ``url`` has already
    passed ``assert_safe_url`` in the caller, so there is no SSRF window:
    we only ever hand a public URL to the public proxy. Returns ``None`` on
    any failure so the caller falls through to its normal error path.
    """
    try:
        async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT, follow_redirects=True) as cli:
            resp = await cli.get(
                f"https://r.jina.ai/{url}",
                headers={"X-Return-Format": "markdown", "Accept": "text/plain"},
            )
    except httpx.HTTPError as e:
        log.debug("jina reader fallback failed for %s: %s", url, e)
        return None
    if resp.status_code >= 400:
        return None
    body = (resp.text or "").strip()
    if not body:
        return None
    return {
        "url": url,
        "ok": True,
        "status": 200,
        "content_type": "text/markdown",
        "format": "markdown",
        "title": None,
        "body": body[:max_chars],
        "truncated_body": len(body) > max_chars,
        "via": "jina_reader",
    }


async def _follow_with_ssrf_check(
    client: httpx.AsyncClient, url: str, *, max_redirects: int
) -> httpx.Response:
    """Walk the redirect chain manually, re-checking each hop against SSRF.

    httpx's own ``follow_redirects`` would quietly load a chain of
    ``302 → internal IP`` responses, which is exactly the escape hatch
    we're trying to close.
    """
    current = url
    for _ in range(max_redirects + 1):
        assert_safe_url(current)
        resp = await client.get(current)
        if resp.is_redirect:
            loc = resp.headers.get("location")
            if not loc:
                return resp
            # httpx can resolve relative redirects; we fold them through
            # ``urljoin``-equivalent via the response helper.
            try:
                current = str(resp.next_request.url) if resp.next_request else loc
            except Exception:  # pragma: no cover - defensive
                current = loc
            continue
        return resp
    # Too many redirects; let httpx raise its standard error so the caller
    # sees a normal http_error branch.
    raise httpx.TooManyRedirects("Exceeded SSRF-safe redirect cap", request=resp.request)


def _decode_bytes(raw: bytes, encoding: str | None) -> str:
    for enc in filter(None, (encoding, "utf-8", "gb18030", "latin-1")):
        try:
            return raw.decode(enc, errors="strict")
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def _extract_markdown(html: str, fmt: str) -> tuple[str, str | None]:
    try:
        import trafilatura
    except ImportError:  # pragma: no cover
        return html, None

    out_format = "markdown" if fmt == "markdown" else "txt"
    extracted = trafilatura.extract(
        html,
        output_format=out_format,
        include_comments=False,
        include_tables=True,
        with_metadata=False,
        favor_precision=True,
    )

    title: str | None = None
    try:
        meta = trafilatura.extract_metadata(html)  # type: ignore[attr-defined]
        if meta is not None:
            title = getattr(meta, "title", None)
    except Exception:  # pragma: no cover
        pass

    return extracted or _fallback_text(html), title


def _fallback_text(html: str) -> str:
    try:
        from lxml import html as lxml_html
        from lxml.html.clean import Cleaner

        cleaner = Cleaner(style=True, scripts=True, javascript=True)
        tree = lxml_html.fromstring(html)
        cleaned = cleaner.clean_html(tree)
        return cleaned.text_content() or ""
    except Exception:  # pragma: no cover
        return html


_ = Any  # keep typing import for future extension
