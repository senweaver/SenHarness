"""Provider-side prompt-cache breakpoint annotation (M2.5.9).

Pure helpers that turn a normalized message list into the same list
with provider-specific cache markers attached. The actual provider
SDK call still happens through the pydantic-ai backend; the runner
wires this module's output into the model's ``model_settings`` (for
Anthropic, which exposes first-class ``anthropic_cache_*`` flags) or
into a per-message content-block annotation (for OpenRouter, whose
upstream relays the Anthropic-shaped ``cache_control`` field through
unchanged).

Design constraints driven by the M2.5 wave roadmap:

* **Cache-aware mutation**: a marker only lands on a *stable* boundary
  (system prompt, tool schema, retrieved context). The runner's
  message_history rehydrate path stays byte-stable across turns so
  the prefix the upstream caches is the same on every attempt.
* **Provider transparency**: failover (M2.5.3) re-tries the same
  payload across providers; this module emits *per-provider* markers
  that the next attempt simply re-annotates, never carrying a stale
  one across kinds.
* **Safe NoOp**: an unsupported provider, an under-threshold prompt,
  or an annotation crash returns the input unchanged. The runner's
  cache disable window (see :mod:`app.services.cache_adaptive`)
  short-circuits this module entirely.

The character-based token estimate (``len // 4``) is intentional —
calling a real tokenizer would couple this module to a vendor SDK
just to decide whether annotating is worth it. The 1024-token floor
is conservative; even a 4× error keeps us above the 256-token line
where prompt caching is no longer a meaningful win.
"""

from __future__ import annotations

import copy
import logging
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

log = logging.getLogger(__name__)

__all__ = [
    "CacheBreakpointConfig",
    "CacheTtl",
    "PROVIDER_CACHE_PROFILES",
    "annotate_cache_breakpoints",
    "build_anthropic_cache_settings",
    "estimate_prompt_tokens",
    "extract_cache_hit_tokens",
    "is_provider_supported",
    "normalize_provider_kind",
]


# ─── Public enums / config dataclass ────────────────────────
class CacheTtl(StrEnum):
    """Cache TTL hint mapped to the upstream's vocabulary.

    ``DEFAULT`` is Anthropic's standard ephemeral entry (5 minutes);
    ``EXTENDED_1H`` activates the ``extended-cache-ttl-2025-04-11``
    beta to keep the entry warm for an hour. OpenRouter passes the
    same string through to its Anthropic provider unchanged.
    """

    DEFAULT = "5m"
    EXTENDED_1H = "1h"


@dataclass(slots=True, frozen=True)
class CacheBreakpointConfig:
    """Per-provider profile read by :func:`annotate_cache_breakpoints`.

    ``max_breakpoints`` is the hard limit imposed by the provider —
    Anthropic and OpenRouter both cap at 4 ``cache_control`` markers
    per request. ``min_prompt_tokens`` is our own floor; below it we
    skip annotation because the marker overhead outweighs the cache
    win.
    """

    provider_kind: str
    supports_cache_control: bool
    max_breakpoints: int = 4
    min_prompt_tokens: int = 1024
    notes: str = ""


# Anthropic-format ``cache_control`` markers ride through OpenRouter's
# Anthropic upstream unchanged; for other providers we explicitly
# declare them as unsupported so a future extension lands cleanly
# rather than silently misrouting markers.
PROVIDER_CACHE_PROFILES: dict[str, CacheBreakpointConfig] = {
    "anthropic": CacheBreakpointConfig(
        provider_kind="anthropic",
        supports_cache_control=True,
        max_breakpoints=4,
        min_prompt_tokens=1024,
        notes="native cache_control on system / tools / messages",
    ),
    "openrouter": CacheBreakpointConfig(
        provider_kind="openrouter",
        supports_cache_control=True,
        max_breakpoints=4,
        min_prompt_tokens=1024,
        notes="anthropic-shaped marker passes through to upstream",
    ),
    "openai": CacheBreakpointConfig(
        provider_kind="openai",
        supports_cache_control=False,
    ),
    "azure_openai": CacheBreakpointConfig(
        provider_kind="azure_openai",
        supports_cache_control=False,
    ),
    "deepseek": CacheBreakpointConfig(
        provider_kind="deepseek",
        supports_cache_control=False,
    ),
    "google": CacheBreakpointConfig(
        provider_kind="google",
        supports_cache_control=False,
    ),
    "xai": CacheBreakpointConfig(
        provider_kind="xai",
        supports_cache_control=False,
    ),
    "moonshot": CacheBreakpointConfig(
        provider_kind="moonshot",
        supports_cache_control=False,
    ),
    "moonshotai": CacheBreakpointConfig(
        provider_kind="moonshotai",
        supports_cache_control=False,
    ),
}


# Marker tag the runner uses on the in-memory dict copy. Anthropic /
# OpenRouter both consume the same shape (``{"type": "ephemeral"}``
# for the default TTL, ``{"type": "ephemeral", "ttl": "1h"}`` for the
# extended one).
_MARKER_FIELD = "cache_control"


# ─── Public helpers ─────────────────────────────────────────
def normalize_provider_kind(provider_kind: str | None) -> str:
    """Lowercase + trim. Defensive — callers pass through raw
    ``ResolvedModel.provider_kind`` which may carry whitespace.
    """
    return str(provider_kind or "").strip().lower()


def is_provider_supported(provider_kind: str | None) -> bool:
    """Whether the given provider supports cache markers at all.

    Unknown kinds default to ``False`` so the runner takes the safe
    NoOp path. To opt a new provider in, register it in
    :data:`PROVIDER_CACHE_PROFILES`.
    """
    profile = PROVIDER_CACHE_PROFILES.get(normalize_provider_kind(provider_kind))
    return bool(profile and profile.supports_cache_control)


def estimate_prompt_tokens(messages: list[dict[str, Any]]) -> int:
    """Rough char-count / 4 estimate.

    No tokenizer dependency: a 25 % overestimate of token count is a
    fine threshold for "is this prompt big enough to bother caching".
    Counts text inside ``content`` plus any nested string parts so
    multimodal blocks still register. Returns 0 on a malformed
    payload rather than raising — callers should fall through to the
    NoOp branch.
    """
    if not messages:
        return 0

    total_chars = 0
    for msg in messages:
        if not isinstance(msg, dict):
            continue
        total_chars += _count_chars(msg.get("content"))
    return total_chars // 4


def _count_chars(value: Any) -> int:
    if value is None:
        return 0
    if isinstance(value, str):
        return len(value)
    if isinstance(value, list):
        total = 0
        for item in value:
            if isinstance(item, dict):
                total += _count_chars(item.get("text") or item.get("content"))
            elif isinstance(item, str):
                total += len(item)
        return total
    if isinstance(value, dict):
        return _count_chars(value.get("text") or value.get("content"))
    return 0


def annotate_cache_breakpoints(
    messages: list[dict[str, Any]],
    *,
    provider_kind: str,
    min_prompt_tokens: int = 1024,
    max_breakpoints: int = 4,
    ttl: CacheTtl | str = CacheTtl.DEFAULT,
) -> list[dict[str, Any]]:
    """Return a copy of ``messages`` with provider cache markers attached.

    Strategy:

    1. Drop straight back to the input list when the provider doesn't
       support cache_control, the list is empty / non-list, or the
       prompt is below ``min_prompt_tokens``.
    2. Pick stable boundaries — system prompts and the last user
       message are the predictable cache prefix anchors. We stop after
       ``max_breakpoints`` markers so a long history doesn't burn the
       Anthropic/OpenRouter 4-marker budget.
    3. Annotate the **last content block** of each chosen message
       (Anthropic's docs require the marker on a content block, not on
       the message envelope; OpenRouter expects the same shape).

    The returned list is a deep copy when at least one annotation
    landed; otherwise the original reference is returned untouched —
    the runner can identify "annotation actually happened" by an
    identity check.
    """
    if not isinstance(messages, list) or not messages:
        return messages

    profile = PROVIDER_CACHE_PROFILES.get(normalize_provider_kind(provider_kind))
    if profile is None or not profile.supports_cache_control:
        return messages

    floor = max(0, int(min_prompt_tokens))
    if floor and estimate_prompt_tokens(messages) < floor:
        return messages

    cap = max(1, min(int(max_breakpoints), profile.max_breakpoints))
    targets = _pick_breakpoint_indices(messages, cap=cap)
    if not targets:
        return messages

    ttl_value = _ttl_value(ttl)
    annotated = copy.deepcopy(messages)
    landed = 0
    for idx in targets:
        if landed >= cap:
            break
        if _annotate_message(annotated[idx], ttl_value=ttl_value):
            landed += 1
    if landed == 0:
        return messages
    return annotated


def _ttl_value(ttl: CacheTtl | str) -> str:
    """Coerce a CacheTtl/string into the wire string ('5m' / '1h').

    Unknown strings collapse to the default ``5m`` to stay compatible
    with the upstream's accepted enum — the validator on the workspace
    setting already enforces the closed set, so this only handles
    legacy in-flight payloads.
    """
    if isinstance(ttl, CacheTtl):
        return ttl.value
    raw = str(ttl or "").strip().lower()
    if raw in {"1h", "extended_1h", "extended-1h"}:
        return CacheTtl.EXTENDED_1H.value
    return CacheTtl.DEFAULT.value


def _pick_breakpoint_indices(
    messages: list[dict[str, Any]], *, cap: int
) -> list[int]:
    """Pick up to ``cap`` stable indices to mark.

    Order of preference (most stable first):

    1. The first system message (system prompt is byte-stable across
       turns once the persona is rendered).
    2. Any subsequent system message that follows immediately (multi-
       part system prompts emitted by harness composers).
    3. The earliest *long* user message — typically a retrieved
       context block. We treat ``len >= 800`` chars as long enough to
       benefit from caching.
    4. The last user message — every chat-turn rotates here, so this
       gets us a fresh marker per turn for the prefix that just
       solidified.

    Duplicates are collapsed; indices are returned in ascending order
    because downstream provider parsers expect markers in document
    order.
    """
    indices: list[int] = []
    seen: set[int] = set()

    def _add(idx: int) -> None:
        if idx in seen or idx < 0 or idx >= len(messages):
            return
        seen.add(idx)
        indices.append(idx)

    last_system_idx = -1
    for idx, msg in enumerate(messages):
        if not isinstance(msg, dict):
            continue
        role = str(msg.get("role") or "").lower()
        if role == "system" and (last_system_idx == -1 or idx == last_system_idx + 1):
            _add(idx)
            last_system_idx = idx
        if len(indices) >= cap:
            return sorted(indices)

    long_user_idx = _first_long_user_index(messages)
    if long_user_idx is not None:
        _add(long_user_idx)

    last_user_idx = _last_user_index(messages)
    if last_user_idx is not None:
        _add(last_user_idx)

    return sorted(indices[:cap])


def _first_long_user_index(messages: list[dict[str, Any]]) -> int | None:
    for idx, msg in enumerate(messages):
        if not isinstance(msg, dict):
            continue
        if str(msg.get("role") or "").lower() != "user":
            continue
        if _count_chars(msg.get("content")) >= 800:
            return idx
    return None


def _last_user_index(messages: list[dict[str, Any]]) -> int | None:
    for idx in range(len(messages) - 1, -1, -1):
        msg = messages[idx]
        if isinstance(msg, dict) and str(msg.get("role") or "").lower() == "user":
            return idx
    return None


def _annotate_message(
    message: dict[str, Any], *, ttl_value: str
) -> bool:
    """Attach a marker to the last content block of ``message``.

    Returns True iff a marker was attached. The function widens
    plain string ``content`` into the content-block list shape on
    first annotation so downstream serialisers see a uniform input.
    Idempotent — re-annotating an already-marked message refreshes
    the TTL but does not duplicate the field.
    """
    if not isinstance(message, dict):
        return False

    content = message.get("content")
    marker = _build_marker(ttl_value)

    if isinstance(content, str):
        if not content.strip():
            return False
        message["content"] = [
            {"type": "text", "text": content, _MARKER_FIELD: marker}
        ]
        return True

    if isinstance(content, list) and content:
        for idx in range(len(content) - 1, -1, -1):
            block = content[idx]
            if isinstance(block, dict):
                block[_MARKER_FIELD] = marker
                return True
        return False

    if isinstance(content, dict):
        content[_MARKER_FIELD] = marker
        return True

    return False


def _build_marker(ttl_value: str) -> dict[str, Any]:
    marker: dict[str, Any] = {"type": "ephemeral"}
    if ttl_value and ttl_value != CacheTtl.DEFAULT.value:
        marker["ttl"] = ttl_value
    return marker


# ─── Anthropic-native settings translation ──────────────────
_PYDANTIC_AI_TTL: dict[str, str] = {
    CacheTtl.DEFAULT.value: "5m",
    CacheTtl.EXTENDED_1H.value: "1h",
}


@dataclass(slots=True)
class AnthropicCacheSettings:
    """Bundle the four ``anthropic_cache_*`` knobs pydantic-ai exposes
    as a single dataclass so the runner can apply them uniformly to
    ``Agent.model_settings`` without duplicating the literal strings.
    """

    cache_tool_definitions: str | None = None
    cache_instructions: str | None = None
    cache_messages: str | None = None
    cache: str | None = None
    betas: tuple[str, ...] = field(default_factory=tuple)


def build_anthropic_cache_settings(
    *, ttl: CacheTtl | str = CacheTtl.DEFAULT
) -> AnthropicCacheSettings:
    """Translate :class:`CacheTtl` into the ``anthropic_cache_*``
    knobs pydantic-ai accepts on its ``AnthropicModelSettings``.

    Returns the same TTL on every cache lever because Anthropic's
    cache pricing splits cleanly between ``5m`` and ``1h`` only — we
    never want a session prompt to cache shorter than the tool
    schema, for example.
    """
    ttl_value = _ttl_value(ttl)
    knob = _PYDANTIC_AI_TTL.get(ttl_value, "5m")
    betas: tuple[str, ...] = ()
    if ttl_value == CacheTtl.EXTENDED_1H.value:
        betas = ("extended-cache-ttl-2025-04-11",)
    return AnthropicCacheSettings(
        cache_tool_definitions=knob,
        cache_instructions=knob,
        cache_messages=knob,
        cache=knob,
        betas=betas,
    )


# ─── Provider response → cache-hit token extraction ─────────
_CACHE_TOKEN_FIELDS = (
    "cache_read_input_tokens",
    "cache_read_tokens",
    "prompt_tokens_cached",
    "cached_prompt_tokens",
    "cached_tokens",
    "input_tokens_cached",
)


def extract_cache_hit_tokens(usage: Any, provider_kind: str | None = None) -> int:
    """Read provider-side cache-hit token count from a usage object.

    Different SDKs spell the field differently:

    * Anthropic — ``cache_read_input_tokens``
    * OpenAI / Azure — ``prompt_tokens_cached`` (rare; not all SKUs)
    * OpenRouter — ``cache_read_input_tokens`` for Anthropic upstreams,
      ``cached_tokens`` for OpenAI upstreams.

    We try each known spelling on the surface object **and** on a
    nested ``prompt_tokens_details`` / ``input_token_details`` block
    that some adapters use. Returns 0 when no cache field is found —
    callers treat that as "miss".
    """
    if usage is None:
        return 0
    _ = provider_kind

    candidates: list[Any] = [usage]
    for nested_attr in ("prompt_tokens_details", "input_token_details", "details"):
        nested = getattr(usage, nested_attr, None)
        if nested is None and isinstance(usage, dict):
            nested = usage.get(nested_attr)
        if nested is not None:
            candidates.append(nested)

    extras_holder = getattr(usage, "details", None)
    if isinstance(extras_holder, dict):
        candidates.append(extras_holder)

    for source in candidates:
        for field_name in _CACHE_TOKEN_FIELDS:
            value = _read_field(source, field_name)
            if value is None:
                continue
            try:
                ivalue = int(value)
            except (TypeError, ValueError):
                continue
            if ivalue > 0:
                return ivalue
    return 0


def _read_field(source: Any, name: str) -> Any:
    if source is None:
        return None
    if isinstance(source, dict):
        return source.get(name)
    return getattr(source, name, None)
