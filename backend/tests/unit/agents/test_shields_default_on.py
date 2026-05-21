"""M0.8 — ``build_content_guards`` default-ON posture.

Three input shapes:

* ``policy.shields`` missing entirely — apply baseline shields.
* ``policy.shields = []`` — explicit opt-out, return empty list.
* ``policy.shields = {dict}`` — author-supplied configuration wins.

The pydantic-ai-shields package is an optional dependency; when it
isn't importable the helper degrades to ``[]`` for every input. We
treat that as "test environment cannot exercise the default-ON
branch" and skip cleanly.
"""

from __future__ import annotations

import pytest

from app.agents.harness.shields import build_content_guards

SHIELDS_AVAILABLE = True
try:
    import pydantic_ai_shields  # noqa: F401
except Exception:  # pragma: no cover
    SHIELDS_AVAILABLE = False


pytestmark = pytest.mark.skipif(
    not SHIELDS_AVAILABLE,
    reason="pydantic-ai-shields not installed in this env",
)


def test_missing_shields_key_returns_default_three() -> None:
    guards = build_content_guards({"approvals": False})
    assert len(guards) == 3


def test_none_policy_returns_default_three() -> None:
    guards = build_content_guards(None)
    assert len(guards) == 3


def test_explicit_empty_list_returns_zero() -> None:
    assert build_content_guards({"shields": []}) == []


def test_explicit_false_returns_zero() -> None:
    assert build_content_guards({"shields": False}) == []


def test_explicit_dict_takes_precedence() -> None:
    guards = build_content_guards({"shields": {"pii": "log"}})
    assert len(guards) == 1
