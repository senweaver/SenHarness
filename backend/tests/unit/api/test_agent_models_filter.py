"""Composer model list respects per-provider ``provider_models.enabled``."""

from __future__ import annotations

from types import SimpleNamespace

from app.agents.kernels.model_catalog import CatalogModel
from app.api.v1.agents import _composer_entries_for_provider


def _row(model: str, *, enabled: bool) -> SimpleNamespace:
    return SimpleNamespace(model=model, enabled=enabled)


def test_enabled_rows_only_when_configured() -> None:
    catalog = [
        CatalogModel("deepseek", "deepseek-chat", "DeepSeek Chat", "balanced"),
        CatalogModel("deepseek", "deepseek-reasoner", "DeepSeek Reasoner", "reasoning"),
    ]
    persisted = [
        _row("deepseek-chat", enabled=True),
        _row("deepseek-reasoner", enabled=False),
    ]
    entries = _composer_entries_for_provider(persisted, catalog)
    assert [e.model for e in entries] == ["deepseek-chat"]


def test_no_enabled_rows_when_all_disabled() -> None:
    catalog = [CatalogModel("deepseek", "deepseek-chat", "DeepSeek Chat", "balanced")]
    persisted = [_row("deepseek-chat", enabled=False)]
    assert _composer_entries_for_provider(persisted, catalog) == []


def test_static_catalog_when_never_configured() -> None:
    catalog = [
        CatalogModel("deepseek", "deepseek-chat", "DeepSeek Chat", "balanced"),
        CatalogModel("deepseek", "deepseek-reasoner", "DeepSeek Reasoner", "reasoning"),
    ]
    entries = _composer_entries_for_provider([], catalog)
    assert len(entries) == 2
