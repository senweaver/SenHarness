"""Model pricing catalog — USD per 1M tokens (input, output).

Used to compute real-dollar cost for every LLM turn. Numbers are approximate
public list prices as of 2025-Q2; override per-deployment via
``settings.PRICING_OVERRIDES_JSON`` (JSON blob of ``{"<model>": [in, out]}``).

Lookup is **fuzzy**:
    1. exact match on "<provider>/<model>"
    2. exact match on "<model>"
    3. prefix match (longest) against the catalog keys

So both ``gpt-4o-2024-11-20`` and ``openai/gpt-4o`` resolve to the gpt-4o row.

If nothing matches, returns ``(0.0, 0.0)`` and cost = 0 — never throws. This is
a cost **estimate**; the authoritative number comes from the provider invoice.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from functools import lru_cache

log = logging.getLogger(__name__)

# ─── Catalog: "model_key" -> (input_usd_per_mtok, output_usd_per_mtok) ───
# Keys are lowercased. Prefix matching is applied during lookup, so keys are
# intentionally short.
_CATALOG_BASE: dict[str, tuple[float, float]] = {
    # ── OpenAI ────────────────────────────────────────────────
    "gpt-5": (1.25, 10.00),
    "gpt-5-mini": (0.25, 2.00),
    "gpt-5-nano": (0.05, 0.40),
    "gpt-4.1": (2.00, 8.00),
    "gpt-4.1-mini": (0.40, 1.60),
    "gpt-4.1-nano": (0.10, 0.40),
    "gpt-4o": (2.50, 10.00),
    "gpt-4o-mini": (0.15, 0.60),
    "o3": (2.00, 8.00),
    "o3-mini": (1.10, 4.40),
    "o1": (15.00, 60.00),
    "o1-mini": (1.10, 4.40),
    "o4-mini": (1.10, 4.40),
    # ── Anthropic ─────────────────────────────────────────────
    "claude-opus-4.1": (15.00, 75.00),
    "claude-opus-4": (15.00, 75.00),
    "claude-sonnet-4.5": (3.00, 15.00),
    "claude-sonnet-4": (3.00, 15.00),
    "claude-3-7-sonnet": (3.00, 15.00),
    "claude-3-5-sonnet": (3.00, 15.00),
    "claude-3-5-haiku": (0.80, 4.00),
    "claude-3-opus": (15.00, 75.00),
    "claude-3-haiku": (0.25, 1.25),
    # ── Google ────────────────────────────────────────────────
    "gemini-2.5-pro": (1.25, 10.00),
    "gemini-2.5-flash": (0.30, 2.50),
    "gemini-2.0-flash": (0.10, 0.40),
    "gemini-1.5-pro": (1.25, 5.00),
    "gemini-1.5-flash": (0.075, 0.30),
    # ── DeepSeek ──────────────────────────────────────────────
    "deepseek-chat": (0.27, 1.10),
    "deepseek-v3": (0.27, 1.10),
    "deepseek-reasoner": (0.55, 2.19),
    "deepseek-r1": (0.55, 2.19),
    # ── Qwen / Alibaba ────────────────────────────────────────
    "qwen3-max": (1.60, 6.40),
    "qwen-max": (1.60, 6.40),
    "qwen-plus": (0.40, 1.20),
    "qwen-turbo": (0.05, 0.20),
    # ── Mistral ───────────────────────────────────────────────
    "mistral-large": (2.00, 6.00),
    "mistral-medium": (0.40, 2.00),
    "mistral-small": (0.20, 0.60),
    # ── xAI ───────────────────────────────────────────────────
    "grok-4": (3.00, 15.00),
    "grok-3": (3.00, 15.00),
    "grok-3-mini": (0.30, 0.50),
    # ── Moonshot ──────────────────────────────────────────────
    "kimi-k2": (0.60, 2.50),
    "moonshot-v1-8k": (1.70, 1.70),
    "moonshot-v1-32k": (3.40, 3.40),
    "moonshot-v1-128k": (8.50, 8.50),
    # ── Zhipu ─────────────────────────────────────────────────
    "glm-4.6": (0.60, 2.20),
    "glm-4.5": (0.60, 2.20),
    "glm-4": (0.70, 0.70),
    # ── Local / free / unknown ────────────────────────────────
    "ollama": (0.0, 0.0),
    "llama": (0.0, 0.0),
    "local": (0.0, 0.0),
}


@dataclass(frozen=True)
class PriceHit:
    """Result of a pricing lookup."""

    matched_key: str
    input_usd_per_mtok: float
    output_usd_per_mtok: float


def _load_overrides(raw: str) -> dict[str, tuple[float, float]]:
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except Exception as e:  # pragma: no cover
        log.warning("PRICING_OVERRIDES_JSON is not valid JSON: %s", e)
        return {}
    out: dict[str, tuple[float, float]] = {}
    for k, v in (parsed or {}).items():
        if not isinstance(k, str):
            continue
        if isinstance(v, list | tuple) and len(v) == 2:
            try:
                out[k.lower()] = (float(v[0]), float(v[1]))
            except (TypeError, ValueError):
                continue
    return out


@lru_cache(maxsize=1)
def _catalog() -> dict[str, tuple[float, float]]:
    # Lazy import to avoid circular dependency with settings at module init.
    from app.core.config import settings

    overrides = _load_overrides(getattr(settings, "PRICING_OVERRIDES_JSON", "") or "")
    return {**_CATALOG_BASE, **overrides}


def _normalize(model: str | None, provider: str | None = None) -> list[str]:
    """Produce ordered lookup candidates (most specific first)."""
    candidates: list[str] = []
    if model:
        m = model.strip().lower()
        if provider:
            candidates.append(f"{provider.strip().lower()}/{m}")
        candidates.append(m)
    return candidates


def lookup_price(model: str | None, provider: str | None = None) -> PriceHit | None:
    """Return the best pricing match or ``None``."""
    catalog = _catalog()
    for cand in _normalize(model, provider):
        if cand in catalog:
            hit = catalog[cand]
            return PriceHit(cand, hit[0], hit[1])
        # Prefix match (longest first)
        prefix_matches = sorted(
            (k for k in catalog if cand.startswith(k) or k.startswith(cand)),
            key=len,
            reverse=True,
        )
        for pm in prefix_matches:
            # Prefer keys that are actually a prefix of the candidate (model id
            # is usually longer than the catalog key, e.g. ``gpt-4o-2024-11-20``
            # vs ``gpt-4o``). ``k.startswith(cand)`` handles the reverse (alias).
            if cand.startswith(pm) or pm.startswith(cand):
                hit = catalog[pm]
                return PriceHit(pm, hit[0], hit[1])
    return None


def calc_cost_usd(
    model: str | None,
    provider: str | None,
    input_tokens: int | None,
    output_tokens: int | None,
) -> dict[str, float | str | None]:
    """Compute a cost estimate for one LLM turn.

    Returns a dict with ``cost`` (float USD), ``matched_model`` (catalog key or
    ``None``), and ``rates`` (``{"in": float, "out": float}``). Safe on any
    input — unknown model or zero tokens returns 0.
    """
    inp = max(int(input_tokens or 0), 0)
    out = max(int(output_tokens or 0), 0)
    if inp == 0 and out == 0:
        return {"cost": 0.0, "matched_model": None, "in_rate": 0.0, "out_rate": 0.0}

    hit = lookup_price(model, provider)
    if hit is None:
        return {"cost": 0.0, "matched_model": None, "in_rate": 0.0, "out_rate": 0.0}

    cost = (inp / 1_000_000.0) * hit.input_usd_per_mtok + (
        out / 1_000_000.0
    ) * hit.output_usd_per_mtok
    return {
        "cost": round(cost, 6),
        "matched_model": hit.matched_key,
        "in_rate": hit.input_usd_per_mtok,
        "out_rate": hit.output_usd_per_mtok,
    }
