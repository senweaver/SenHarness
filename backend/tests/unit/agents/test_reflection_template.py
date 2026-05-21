"""Tests for the whitelisted reflection-template loader."""

from __future__ import annotations

import pytest

from app.agents.templates.reflection import (
    REFLECTION_TEMPLATE_NAMES,
    TemplateNotFoundError,
    list_templates,
    load_reflection_template,
)
from app.agents.templates.reflection.loader import _clear_cache_for_tests


def setup_function() -> None:
    _clear_cache_for_tests()


def test_known_templates_load() -> None:
    body = load_reflection_template("periodic")
    assert "iteration" in body.lower()
    body2 = load_reflection_template("tool_call")
    assert "tool" in body2.lower()


def test_cache_returns_same_instance() -> None:
    a = load_reflection_template("periodic")
    b = load_reflection_template("periodic")
    assert a is b


def test_path_traversal_rejected() -> None:
    with pytest.raises(TemplateNotFoundError):
        load_reflection_template("../../etc/passwd")
    with pytest.raises(TemplateNotFoundError):
        load_reflection_template("../loader")
    with pytest.raises(TemplateNotFoundError):
        load_reflection_template("periodic/extra")


def test_unknown_name_rejected() -> None:
    with pytest.raises(TemplateNotFoundError):
        load_reflection_template("does_not_exist")


def test_whitelist_listing_stable() -> None:
    names = list_templates()
    assert names == sorted(REFLECTION_TEMPLATE_NAMES)
    assert "periodic" in names
    assert "tool_call" in names


def test_render_substitutes_placeholders() -> None:
    from app.agents.harness.reliability import _render_template

    rendered = _render_template(
        "iter={iteration_count} tools={tool_call_count} {recent_tools_summary}",
        iteration_count=4,
        tool_call_count=9,
        recent_tools=[{"name": "ls", "args": "", "ok": True}],
    )
    assert "iter=4" in rendered
    assert "tools=9" in rendered
    assert "ls" in rendered
    assert "ok" in rendered


def test_render_unknown_placeholder_becomes_empty() -> None:
    from app.agents.harness.reliability import _render_template

    rendered = _render_template(
        "x={iteration_count} y={mystery_field}",
        iteration_count=1,
        tool_call_count=0,
        recent_tools=[],
    )
    assert "x=1" in rendered
    assert "y=" in rendered
