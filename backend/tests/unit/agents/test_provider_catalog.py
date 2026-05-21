"""Sanity checks for the SenHarness provider catalog."""

from __future__ import annotations

import pytest

from app.agents.kernels import provider_catalog as pc
from app.agents.kernels.model_catalog import CATALOG


def test_iter_catalog_returns_curated_entry_count() -> None:
    rows = pc.iter_catalog()
    assert len(rows) == 19, f"expected 19 catalog entries, got {len(rows)}"


def test_required_kinds_present() -> None:
    kinds = {r.kind for r in pc.iter_catalog()}
    must_have = {
        "openai",
        "anthropic",
        "google",
        "xai",
        "openrouter",
        "azure_openai",
        "huggingface",
        "deepseek",
        "dashscope",
        "bailian_token",
        "bailian_coding",
        "moonshot",
        "kimi_code",
        "zhipu",
        "siliconflow",
        "minimax",
        "ollama",
        "vllm",
        "custom",
    }
    missing = must_have - kinds
    assert not missing, f"catalog missing kinds: {sorted(missing)}"


def test_removed_kinds_absent() -> None:
    kinds = {r.kind for r in pc.iter_catalog()}
    for removed in (
        "alibaba_cn",
        "aliyun_coding_intl",
        "kimi_intl",
        "zhipu_coding_bigmodel",
        "openai_chatgpt",
    ):
        assert removed not in kinds, removed


def test_dashscope_cn_default_and_aliases() -> None:
    entry = pc.get_entry("dashscope")
    assert entry is not None
    assert pc.get_entry("alibaba_cn") is entry
    url = pc.default_base_url_for("alibaba") or ""
    assert "dashscope.aliyuncs.com" in url
    assert "dashscope-intl" not in url


def test_bailian_token_default_url() -> None:
    url = pc.default_base_url_for("bailian_token") or ""
    assert "token-plan.cn-beijing.maas.aliyuncs.com" in url


def test_bailian_coding_default_catalog() -> None:
    rows = {row.model: row for row in CATALOG.get("bailian_coding", [])}
    expected = {
        "qwen3.6-plus",
        "qwen3-coder-plus",
        "glm-5",
        "kimi-k2.5",
        "MiniMax-M2.5",
    }
    assert expected.issubset(rows.keys())


def test_each_entry_has_zh_display_name() -> None:
    for row in pc.iter_catalog():
        assert row.display_name, f"{row.kind}: missing display_name"
        assert row.display_name_zh, f"{row.kind}: missing display_name_zh"


def test_family_classification_consistent() -> None:
    families = {pc.family_of(r.kind) for r in pc.iter_catalog()}
    expected_super = {
        "openai-compatible",
        "anthropic",
        "google",
        "huggingface",
    }
    assert families.issubset(expected_super), (
        f"unknown families in catalog: {families - expected_super}"
    )


@pytest.mark.parametrize(
    "kind",
    ["openai", "deepseek", "moonshot", "dashscope", "anthropic", "xai"],
)
def test_pydantic_ai_provider_class_resolves(kind: str) -> None:
    from pydantic_ai.providers import infer_provider_class

    entry = pc.get_entry(kind)
    assert entry is not None, kind
    pkind = entry.pydantic_ai_kind or entry.kind
    cls = infer_provider_class(pkind)
    assert cls is not None
    assert cls.__name__.endswith("Provider")


def test_aliases_resolve_to_same_entry() -> None:
    a = pc.get_entry("moonshot")
    b = pc.get_entry("moonshotai")
    assert a is not None and b is not None
    assert a.kind == b.kind == "moonshot"


def test_canonical_kind_maps_legacy_aliases() -> None:
    assert pc.canonical_kind("alibaba_cn") == "dashscope"
    assert pc.canonical_kind("aliyun_coding_intl") == "bailian_coding"


def test_supports_discover_only_for_openai_family() -> None:
    for entry in pc.iter_catalog():
        expected = entry.family == "openai-compatible"
        assert pc.supports_discover(entry.kind) is expected, entry.kind


def test_default_base_urls_have_scheme() -> None:
    for entry in pc.iter_catalog():
        if not entry.default_base_url:
            continue
        assert entry.default_base_url.startswith(("http://", "https://")), (
            f"{entry.kind}: bad base_url {entry.default_base_url!r}"
        )


def test_unknown_kind_returns_none() -> None:
    assert pc.get_entry("totally-not-a-real-kind") is None
    assert not pc.is_known_kind("totally-not-a-real-kind")
