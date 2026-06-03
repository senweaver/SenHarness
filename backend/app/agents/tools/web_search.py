"""`web_search` — multi-provider web search, vault-backed.

Resolution order (first non-empty wins; sorted by `search_providers.priority`):

  1. Workspace-configured Tavily / SerpAPI / Brave / Jina / Exa via vault key.
  2. DuckDuckGo (no key, fallback via `ddgs`).

No env-var reads — operators configure search providers via the
``Settings → Search providers`` UI.

Returns a compact list of `{title, url, snippet, source}` items the LLM can
chain into `web_fetch` for full-page extraction.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx
from pydantic import BaseModel, Field

from app.agents.tools._context import get_context
from app.db.session import get_session_factory

log = logging.getLogger(__name__)

MAX_RESULTS_CAP = 10


class WebSearchArgs(BaseModel):
    query: str = Field(..., description="Search query, 3-200 chars.")
    max_results: int = Field(
        default=5, ge=1, le=MAX_RESULTS_CAP, description="How many results to return."
    )
    site: str | None = Field(default=None, description="Restrict to a site (e.g. 'github.com').")
    time_range: str | None = Field(
        default=None, description="One of 'day'|'week'|'month'|'year' (best-effort)."
    )


async def run_web_search(args: WebSearchArgs) -> dict:
    q = args.query.strip()
    if args.site:
        q = f"site:{args.site} {q}"

    candidates = await _ordered_candidates()

    # No search backend configured at all: skip the implicit DuckDuckGo probe
    # (frequently blocked / slow in some regions, which only adds latency) and
    # steer the model to `web_fetch` for reading a known URL directly.
    if not candidates:
        return {
            "query": args.query,
            "provider": "none",
            "results": [],
            "note": (
                "No web search backend is configured, so web_search is "
                "unavailable. Configure one in Settings → Search providers, or "
                "use the web_fetch tool to read a specific URL directly."
            ),
        }

    for kind, api_key, base_url in candidates:
        fn = _PROVIDER_FNS.get(kind)
        if fn is None:
            continue
        try:
            result = await fn(q, args.max_results, args.time_range, api_key, base_url)
        except Exception as e:  # pragma: no cover
            log.debug("web_search provider %s failed: %s", kind, e)
            continue
        if result is not None:
            return result

    # Last-resort no-key fallback.
    try:
        ddg = await _ddgs(q, args.max_results, args.time_range, None, None)
        if ddg is not None:
            return ddg
    except Exception:  # pragma: no cover
        pass

    return {
        "query": args.query,
        "provider": "none",
        "results": [],
        "note": (
            "All search providers failed. Configure one in Settings → Search "
            "providers, or ensure DuckDuckGo (no-key fallback) is reachable."
        ),
    }


async def _ordered_candidates() -> list[tuple[str, str | None, str | None]]:
    """Return (kind, api_key, base_url) tuples in priority order.

    Pulls every enabled `search_providers` row for the workspace; rows with a
    vault key get unwrapped here so the per-call HTTP path stays clean. When
    the calling agent declared a preferred ``default_search_provider_kind``
    via ``policy``, candidates of that kind are surfaced ahead of the rest
    so the agent's choice wins as long as it has a working key — the
    workspace priority list still functions as a fallback chain.
    """
    try:
        ctx = get_context()
    except Exception:
        return []
    ws_id = ctx.workspace_id
    preferred_kind_raw = (ctx.policy or {}).get("default_search_provider_kind")
    preferred_kind = (
        preferred_kind_raw.strip().lower()
        if isinstance(preferred_kind_raw, str) and preferred_kind_raw.strip()
        else None
    )

    out: list[tuple[str, str | None, str | None]] = []
    factory = get_session_factory()
    async with factory() as session:
        from app.repositories.search_provider import SearchProviderRepository

        repo = SearchProviderRepository(session)
        rows = await repo.list(
            workspace_id=ws_id,
            enabled=True,
            order_by=None,
            limit=20,
        )
        # Sort by priority (asc), then created_at — repo helper doesn't accept
        # column references for ordering by attribute name, so do it in Python.
        # When the agent picked a default kind, that bucket wins ties at the
        # top regardless of priority — but unmatched rows stay in their
        # original priority order so they remain a viable fallback chain.
        rows = sorted(
            rows,
            key=lambda r: (
                0 if preferred_kind and r.kind == preferred_kind else 1,
                r.priority,
                r.created_at,
            ),
        )

        for row in rows:
            api_key: str | None = None
            if row.vault_item_id is not None:
                try:
                    from app.db.models.vault import VaultItem
                    from app.db.repository import AsyncRepository
                    from app.services.vault import reveal_secret

                    vault_repo: AsyncRepository[VaultItem] = AsyncRepository(session, VaultItem)
                    item = await vault_repo.get(row.vault_item_id)
                    if item is not None:
                        api_key = await reveal_secret(item)
                except Exception as e:  # pragma: no cover
                    log.warning("vault reveal failed for search_provider %s: %s", row.id, e)
            out.append((row.kind, api_key, row.base_url))

    return out


# ─── Tavily ────────────────────────────────────────────────
async def _tavily(
    query: str,
    limit: int,
    time_range: str | None,
    api_key: str | None,
    base_url: str | None,
) -> dict | None:
    if not api_key:
        return None
    payload: dict[str, Any] = {
        "query": query,
        "max_results": limit,
        "include_answer": False,
    }
    if time_range:
        payload["time_range"] = time_range
    base = (base_url or "https://api.tavily.com").rstrip("/")
    async with httpx.AsyncClient(timeout=20.0) as cli:
        r = await cli.post(
            f"{base}/search",
            json=payload,
            headers={"Authorization": f"Bearer {api_key}"},
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
async def _serpapi(
    query: str,
    limit: int,
    time_range: str | None,
    api_key: str | None,
    base_url: str | None,
) -> dict | None:
    if not api_key:
        return None
    params: dict[str, Any] = {
        "engine": "google",
        "q": query,
        "num": limit,
        "api_key": api_key,
    }
    if time_range:
        params["tbs"] = {"day": "qdr:d", "week": "qdr:w", "month": "qdr:m", "year": "qdr:y"}.get(
            time_range, ""
        )
    base = (base_url or "https://serpapi.com").rstrip("/")
    async with httpx.AsyncClient(timeout=20.0) as cli:
        r = await cli.get(f"{base}/search.json", params=params)
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
async def _brave(
    query: str,
    limit: int,
    time_range: str | None,
    api_key: str | None,
    base_url: str | None,
) -> dict | None:
    if not api_key:
        return None
    params = {"q": query, "count": limit}
    if time_range:
        params["freshness"] = {"day": "pd", "week": "pw", "month": "pm", "year": "py"}.get(
            time_range, ""
        )
    base = (base_url or "https://api.search.brave.com").rstrip("/")
    async with httpx.AsyncClient(timeout=20.0) as cli:
        r = await cli.get(
            f"{base}/res/v1/web/search",
            params=params,
            headers={"X-Subscription-Token": api_key, "Accept": "application/json"},
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


async def _jina(
    query: str,
    limit: int,
    _time_range: str | None,
    api_key: str | None,
    base_url: str | None,
) -> dict | None:
    if not api_key:
        return None
    base = (base_url or "https://s.jina.ai").rstrip("/")
    async with httpx.AsyncClient(timeout=20.0) as cli:
        r = await cli.get(
            f"{base}/{query}",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Accept": "application/json",
            },
        )
    r.raise_for_status()
    data = r.json()
    rows = data.get("data") or []
    return {
        "query": query,
        "provider": "jina",
        "results": [
            {
                "title": item.get("title"),
                "url": item.get("url"),
                "snippet": item.get("description") or item.get("content"),
                "source": _domain(item.get("url", "")),
            }
            for item in rows[:limit]
        ],
    }


async def _exa(
    query: str,
    limit: int,
    _time_range: str | None,
    api_key: str | None,
    base_url: str | None,
) -> dict | None:
    if not api_key:
        return None
    base = (base_url or "https://api.exa.ai").rstrip("/")
    payload = {"query": query, "numResults": limit}
    async with httpx.AsyncClient(timeout=20.0) as cli:
        r = await cli.post(
            f"{base}/search",
            headers={"x-api-key": api_key, "Content-Type": "application/json"},
            json=payload,
        )
    r.raise_for_status()
    data = r.json()
    return {
        "query": query,
        "provider": "exa",
        "results": [
            {
                "title": item.get("title"),
                "url": item.get("url"),
                "snippet": item.get("text"),
                "source": _domain(item.get("url", "")),
            }
            for item in (data.get("results") or [])[:limit]
        ],
    }


# ─── DuckDuckGo (fallback, no key) ─────────────────────────
# ``ddgs``'s default ``backend="auto"`` fans out to bing/brave/grokipedia/
# mojeek/wikipedia/yandex/yahoo concurrently and waits up to ``timeout=5s``
# for each. From mainland China the slow path dominates: Brave/Yahoo/
# Wikipedia/Mojeek consistently time out or return 403, leaving Yandex/Bing
# as the only sources that ever produce a row. Constraining the backend
# list to the engines that actually reach us — and tightening the per-engine
# wait — keeps the no-key fallback usable instead of imposing a 5-7 s tax
# on every ``web_search`` call. Operators who need different reachability
# should configure a keyed provider (Tavily / Brave API / Jina / Exa /
# SerpAPI) in ``Settings → Search providers``; this fallback is only meant
# to keep the agent functional when none of those are set.
_DDGS_BACKENDS = "yandex,bing"
_DDGS_TIMEOUT_SEC = 3  # see web_search.py docstring


async def _ddgs(
    query: str,
    limit: int,
    time_range: str | None,
    _api_key: str | None,
    _base_url: str | None,
) -> dict | None:
    try:
        from ddgs import DDGS
    except ImportError:  # pragma: no cover
        return None

    def _sync() -> list[dict]:
        time_map = {"day": "d", "week": "w", "month": "m", "year": "y"}
        kwargs: dict[str, Any] = {
            "max_results": limit,
            "backend": _DDGS_BACKENDS,
        }
        if time_range and time_range in time_map:
            kwargs["timelimit"] = time_map[time_range]
        with DDGS(timeout=_DDGS_TIMEOUT_SEC) as ddgs:
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


_PROVIDER_FNS: dict[str, Any] = {
    "tavily": _tavily,
    "serpapi": _serpapi,
    "brave": _brave,
    "jina": _jina,
    "exa": _exa,
    "duckduckgo": _ddgs,
}


# ─── Helpers ───────────────────────────────────────────────
def _domain(url: str) -> str:
    if not url:
        return ""
    try:
        from urllib.parse import urlparse

        return urlparse(url).hostname or ""
    except Exception:
        return ""
