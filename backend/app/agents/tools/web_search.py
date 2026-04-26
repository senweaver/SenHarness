"""`web_search` — multi-provider web search.

Resolution order (first non-empty wins):
  1. Tavily      — TAVILY_API_KEY           (best quality, generous free tier)
  2. SerpAPI     — SERPAPI_KEY              (Google SERP)
  3. Brave       — BRAVE_SEARCH_API_KEY     (independent index)
  4. DuckDuckGo  — no key                   (fallback via `ddgs`)

Returns a compact list of `{title, url, snippet, source}` items the LLM can
chain into `web_fetch` for full-page extraction.
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

import httpx
from pydantic import BaseModel, Field

log = logging.getLogger(__name__)

MAX_RESULTS_CAP = 10


class WebSearchArgs(BaseModel):
    query: str = Field(..., description="Search query, 3-200 chars.")
    max_results: int = Field(
        default=5, ge=1, le=MAX_RESULTS_CAP, description="How many results to return."
    )
    site: str | None = Field(
        default=None, description="Restrict to a site (e.g. 'github.com')."
    )
    time_range: str | None = Field(
        default=None, description="One of 'day'|'week'|'month'|'year' (best-effort)."
    )


async def run_web_search(args: WebSearchArgs) -> dict:
    q = args.query.strip()
    if args.site:
        q = f"site:{args.site} {q}"

    for provider_fn in (_tavily, _serpapi, _brave, _ddgs):
        try:
            result = await provider_fn(q, args.max_results, args.time_range)
        except Exception as e:  # pragma: no cover
            log.debug("web_search provider %s failed: %s", provider_fn.__name__, e)
            continue
        if result is not None:
            return result

    return {
        "query": args.query,
        "provider": "none",
        "results": [],
        "note": (
            "All search providers failed. Set TAVILY_API_KEY / SERPAPI_KEY / "
            "BRAVE_SEARCH_API_KEY in .env, or ensure outbound DDG is reachable."
        ),
    }


# ─── Tavily ────────────────────────────────────────────────
async def _tavily(query: str, limit: int, time_range: str | None) -> dict | None:
    key = os.environ.get("TAVILY_API_KEY")
    if not key:
        return None
    payload: dict[str, Any] = {
        "query": query,
        "max_results": limit,
        "include_answer": False,
    }
    if time_range:
        payload["time_range"] = time_range
    async with httpx.AsyncClient(timeout=20.0) as cli:
        r = await cli.post(
            "https://api.tavily.com/search",
            json=payload,
            headers={"Authorization": f"Bearer {key}"},
        )
    r.raise_for_status()
    data = r.json()
    return {
        "query": query,
        "provider": "tavily",
        "results": [
            {
                "title": item.get("title"),
                "url": item.get("url"),
                "snippet": item.get("content"),
                "source": _domain(item.get("url", "")),
            }
            for item in (data.get("results") or [])[:limit]
        ],
    }


# ─── SerpAPI ───────────────────────────────────────────────
async def _serpapi(query: str, limit: int, time_range: str | None) -> dict | None:
    key = os.environ.get("SERPAPI_KEY")
    if not key:
        return None
    params: dict[str, Any] = {
        "engine": "google",
        "q": query,
        "num": limit,
        "api_key": key,
    }
    if time_range:
        params["tbs"] = {"day": "qdr:d", "week": "qdr:w", "month": "qdr:m", "year": "qdr:y"}.get(
            time_range, ""
        )
    async with httpx.AsyncClient(timeout=20.0) as cli:
        r = await cli.get("https://serpapi.com/search.json", params=params)
    r.raise_for_status()
    data = r.json()
    return {
        "query": query,
        "provider": "serpapi",
        "results": [
            {
                "title": item.get("title"),
                "url": item.get("link"),
                "snippet": item.get("snippet"),
                "source": _domain(item.get("link", "")),
            }
            for item in (data.get("organic_results") or [])[:limit]
        ],
    }


# ─── Brave ─────────────────────────────────────────────────
async def _brave(query: str, limit: int, time_range: str | None) -> dict | None:
    key = os.environ.get("BRAVE_SEARCH_API_KEY")
    if not key:
        return None
    params = {"q": query, "count": limit}
    if time_range:
        params["freshness"] = {"day": "pd", "week": "pw", "month": "pm", "year": "py"}.get(
            time_range, ""
        )
    async with httpx.AsyncClient(timeout=20.0) as cli:
        r = await cli.get(
            "https://api.search.brave.com/res/v1/web/search",
            params=params,
            headers={"X-Subscription-Token": key, "Accept": "application/json"},
        )
    r.raise_for_status()
    data = r.json()
    web = (data.get("web") or {}).get("results") or []
    return {
        "query": query,
        "provider": "brave",
        "results": [
            {
                "title": item.get("title"),
                "url": item.get("url"),
                "snippet": item.get("description"),
                "source": _domain(item.get("url", "")),
            }
            for item in web[:limit]
        ],
    }


# ─── DuckDuckGo (fallback, no key) ─────────────────────────
async def _ddgs(query: str, limit: int, time_range: str | None) -> dict | None:
    try:
        from ddgs import DDGS
    except ImportError:  # pragma: no cover
        return None

    def _sync() -> list[dict]:
        time_map = {"day": "d", "week": "w", "month": "m", "year": "y"}
        kwargs: dict[str, Any] = {"max_results": limit}
        if time_range and time_range in time_map:
            kwargs["timelimit"] = time_map[time_range]
        with DDGS() as ddgs:
            return list(ddgs.text(query, **kwargs))

    raw = await asyncio.to_thread(_sync)
    results = []
    for item in raw[:limit]:
        results.append(
            {
                "title": item.get("title"),
                "url": item.get("href") or item.get("link") or item.get("url"),
                "snippet": item.get("body") or item.get("description"),
                "source": _domain(item.get("href") or item.get("link") or item.get("url") or ""),
            }
        )
    return {"query": query, "provider": "duckduckgo", "results": results}


# ─── Helpers ───────────────────────────────────────────────
def _domain(url: str) -> str:
    if not url:
        return ""
    try:
        from urllib.parse import urlparse

        return urlparse(url).hostname or ""
    except Exception:
        return ""
