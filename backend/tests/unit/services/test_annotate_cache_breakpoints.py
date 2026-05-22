"""Pure unit tests for :mod:`app.services.cache_control` (M2.5.9).

The tests stay free of any DB / Redis touch — they exercise the
pure annotation algorithm, the provider profile lookup, the prompt
size estimator, and the Anthropic-settings translator.
"""

from __future__ import annotations

import pytest

from app.services import cache_control as cache_ctl


# ─── is_provider_supported ──────────────────────────────────
@pytest.mark.parametrize(
    "kind,expected",
    [
        ("anthropic", True),
        ("openrouter", True),
        ("OpenRouter", True),
        ("openai", False),
        ("deepseek", False),
        ("google", False),
        ("totally-unknown", False),
        ("", False),
        (None, False),
    ],
)
def test_is_provider_supported_partition(kind, expected):
    assert cache_ctl.is_provider_supported(kind) is expected


# ─── estimate_prompt_tokens ─────────────────────────────────
def test_estimate_prompt_tokens_chars_div_4():
    msgs = [
        {"role": "system", "content": "x" * 4000},
        {"role": "user", "content": "x" * 200},
    ]
    assert cache_ctl.estimate_prompt_tokens(msgs) == (4200 // 4)


def test_estimate_prompt_tokens_handles_blocks():
    msgs = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "hello"},
                {"type": "text", "text": "world"},
            ],
        }
    ]
    assert cache_ctl.estimate_prompt_tokens(msgs) == (10 // 4)


def test_estimate_prompt_tokens_zero_on_garbage():
    assert cache_ctl.estimate_prompt_tokens([]) == 0
    assert cache_ctl.estimate_prompt_tokens([None, 42, "string"]) == 0  # type: ignore[list-item]


# ─── Anthropic happy path ───────────────────────────────────
def test_anthropic_happy_marks_system_and_last_user():
    msgs = [
        {"role": "system", "content": "x" * 5000},
        {"role": "user", "content": "what is the answer?"},
    ]
    out = cache_ctl.annotate_cache_breakpoints(
        msgs,
        provider_kind="anthropic",
        min_prompt_tokens=128,
        max_breakpoints=4,
    )
    assert out is not msgs
    sys_block = out[0]["content"][0]
    user_block = out[1]["content"][0]
    assert sys_block["cache_control"] == {"type": "ephemeral"}
    assert user_block["cache_control"] == {"type": "ephemeral"}
    assert sys_block["text"].startswith("xxx")


def test_extended_ttl_is_serialized():
    msgs = [
        {"role": "system", "content": "x" * 5000},
        {"role": "user", "content": "y" * 1000},
    ]
    out = cache_ctl.annotate_cache_breakpoints(
        msgs,
        provider_kind="anthropic",
        min_prompt_tokens=128,
        max_breakpoints=4,
        ttl=cache_ctl.CacheTtl.EXTENDED_1H,
    )
    sys_block = out[0]["content"][0]
    assert sys_block["cache_control"] == {"type": "ephemeral", "ttl": "1h"}


# ─── OpenRouter happy path (same shape as anthropic) ────────
def test_openrouter_uses_same_marker_shape():
    msgs = [
        {"role": "system", "content": "x" * 5000},
        {"role": "user", "content": "hello"},
    ]
    out = cache_ctl.annotate_cache_breakpoints(
        msgs,
        provider_kind="openrouter",
        min_prompt_tokens=128,
    )
    sys_block = out[0]["content"][0]
    assert sys_block["cache_control"] == {"type": "ephemeral"}


# ─── Unsupported provider NoOp ──────────────────────────────
def test_openai_noop_returns_input_identity():
    msgs = [
        {"role": "system", "content": "x" * 8000},
        {"role": "user", "content": "y" * 4000},
    ]
    out = cache_ctl.annotate_cache_breakpoints(msgs, provider_kind="openai", min_prompt_tokens=128)
    assert out is msgs


def test_unknown_provider_noop():
    msgs = [{"role": "user", "content": "x" * 4000}]
    out = cache_ctl.annotate_cache_breakpoints(
        msgs, provider_kind="totally-unknown", min_prompt_tokens=128
    )
    assert out is msgs


# ─── Below threshold NoOp ───────────────────────────────────
def test_below_min_prompt_tokens_noop():
    msgs = [{"role": "user", "content": "short"}]
    out = cache_ctl.annotate_cache_breakpoints(
        msgs, provider_kind="anthropic", min_prompt_tokens=1024
    )
    assert out is msgs


# ─── Max breakpoints respected ──────────────────────────────
def test_max_breakpoints_enforced():
    # 6 system messages — only 4 markers should be placed (default
    # max_breakpoints, with the cap also bounded by the profile's
    # provider-side limit of 4).
    msgs = [{"role": "system", "content": "x" * 1000} for _ in range(6)]
    msgs.append({"role": "user", "content": "fin"})
    out = cache_ctl.annotate_cache_breakpoints(
        msgs,
        provider_kind="anthropic",
        min_prompt_tokens=128,
        max_breakpoints=4,
    )

    marked = sum(
        1
        for m in out
        if isinstance(m.get("content"), list)
        and any("cache_control" in (b or {}) for b in m["content"])
    )
    assert marked == 4


def test_max_breakpoints_below_provider_limit():
    msgs = [{"role": "system", "content": "x" * 1000} for _ in range(5)]
    msgs.append({"role": "user", "content": "fin"})
    out = cache_ctl.annotate_cache_breakpoints(
        msgs,
        provider_kind="anthropic",
        min_prompt_tokens=128,
        max_breakpoints=2,
    )
    marked = sum(
        1
        for m in out
        if isinstance(m.get("content"), list)
        and any("cache_control" in (b or {}) for b in m["content"])
    )
    assert marked == 2


# ─── Anthropic settings translation ─────────────────────────
def test_build_anthropic_cache_settings_default_5m():
    s = cache_ctl.build_anthropic_cache_settings()
    assert s.cache_tool_definitions == "5m"
    assert s.cache_instructions == "5m"
    assert s.cache_messages == "5m"
    assert s.cache == "5m"
    assert s.betas == ()


def test_build_anthropic_cache_settings_extended_1h():
    s = cache_ctl.build_anthropic_cache_settings(ttl=cache_ctl.CacheTtl.EXTENDED_1H)
    assert s.cache_tool_definitions == "1h"
    assert "extended-cache-ttl-2025-04-11" in s.betas


# ─── extract_cache_hit_tokens ───────────────────────────────
def test_extract_anthropic_field():
    class _U:
        cache_read_input_tokens = 256

    assert cache_ctl.extract_cache_hit_tokens(_U()) == 256


def test_extract_openai_nested_field():
    class _Details:
        cached_tokens = 128

    class _U:
        prompt_tokens_details = _Details()

    assert cache_ctl.extract_cache_hit_tokens(_U()) == 128


def test_extract_returns_zero_when_absent():
    assert cache_ctl.extract_cache_hit_tokens(None) == 0
    assert cache_ctl.extract_cache_hit_tokens(object()) == 0


def test_extract_handles_dict_usage():
    usage = {"cache_read_input_tokens": 64}
    assert cache_ctl.extract_cache_hit_tokens(usage) == 64
