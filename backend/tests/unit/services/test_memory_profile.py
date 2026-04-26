"""Unit tests for the four-layer memory profile service.

Focuses on the pure-python pieces that don't need a DB session:
char-cap truncation, SOUL dim sanitization, and the injection renderer.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from app.db.models.memory_profile import (
    MAX_CONTENT_CHARS,
    SOUL_DIMENSIONS,
    MemoryProfileKind,
)
from app.services.memory_profile import (
    _sanitize_soul_dims,
    _truncate,
    render_profile_fragment,
)


@dataclass
class _FakeProfile:
    """Duck-typed stand-in for :class:`MemoryProfile` — we only need the
    attributes the renderer reads."""

    content_md: str
    soul_dims_json: dict = field(default_factory=dict)


class TestTruncate:
    def test_no_truncate_when_under_cap(self):
        body = "Short note."
        out, n = _truncate(body, 200)
        assert out == body
        assert n == len(body)

    def test_hard_truncate_when_over_cap(self):
        body = "a" * 500
        out, n = _truncate(body, 100)
        assert len(out) <= 100
        assert n == len(out)
        assert out.endswith("…")

    def test_prefers_paragraph_boundary(self):
        body = (
            "Para one with enough text to fill.\n\n"
            "Para two has extra content we will drop."
        )
        cap = len("Para one with enough text to fill.\n\n") + 5
        out, _ = _truncate(body, cap)
        # Should cut at the paragraph break, leaving para one intact.
        assert out.startswith("Para one with enough text to fill.")
        assert "Para two" not in out

    def test_cap_per_kind_differs(self):
        assert (
            MAX_CONTENT_CHARS[MemoryProfileKind.WORKSPACE_MEMORY]
            > MAX_CONTENT_CHARS[MemoryProfileKind.USER_PROFILE]
        )


class TestSanitizeSoulDims:
    def test_drops_non_string_values(self):
        out = _sanitize_soul_dims({"goals": "ship v2", "count": 5, "flag": True})
        assert out == {"goals": "ship v2"}

    def test_normalizes_keys(self):
        out = _sanitize_soul_dims(
            {
                "Communication Style": "direct",
                "Domain-Expertise": "ML",
                "  tone_and_register ": "playful",
            }
        )
        assert "communication_style" in out
        assert "domain_expertise" in out
        assert "tone_and_register" in out

    def test_caps_value_length(self):
        out = _sanitize_soul_dims({"goals_current": "x" * 5000})
        assert len(out["goals_current"]) <= 400


class TestRenderProfileFragment:
    def test_none_when_all_empty(self):
        assert render_profile_fragment(
            workspace_memory=None, user_profile=None, user_soul=None
        ) is None
        assert render_profile_fragment(
            workspace_memory=_FakeProfile(content_md=""),
            user_profile=_FakeProfile(content_md="   "),
            user_soul=_FakeProfile(content_md="", soul_dims_json={}),
        ) is None

    def test_renders_present_profiles_with_headings(self):
        frag = render_profile_fragment(
            workspace_memory=_FakeProfile(content_md="ACME handbook snippet."),
            user_profile=_FakeProfile(content_md="Call me Alex, prefers bullets."),
            user_soul=None,
        )
        assert frag is not None
        assert "## WORKSPACE MEMORY" in frag
        assert "ACME handbook snippet." in frag
        assert "## USER PROFILE" in frag
        assert "Call me Alex" in frag
        # No SOUL section when soul is None.
        assert "## USER SOUL" not in frag

    def test_soul_dims_rendered_as_bullets_when_populated(self):
        frag = render_profile_fragment(
            workspace_memory=None,
            user_profile=None,
            user_soul=_FakeProfile(
                content_md="12-dimension summary here.",
                soul_dims_json={
                    "communication_style": "direct",
                    "preferences_language": "zh-CN",
                    "empty_dim": "",
                },
            ),
        )
        assert frag is not None
        assert "## USER SOUL" in frag
        assert "### SOUL DIMENSIONS" in frag
        assert "- communication_style: direct" in frag
        assert "- preferences_language: zh-CN" in frag
        # Empty dims are filtered out.
        assert "empty_dim" not in frag


class TestSoulDimensionConstants:
    def test_canonical_dims_non_empty_and_unique(self):
        assert len(SOUL_DIMENSIONS) == 12
        assert len(set(SOUL_DIMENSIONS)) == len(SOUL_DIMENSIONS)
        assert all(isinstance(d, str) and d for d in SOUL_DIMENSIONS)


class TestSessionSearchArgsSchema:
    """Smoke test: the tool's arg model accepts natural LLM aliases."""

    def test_query_aliases(self):
        from app.agents.tools.session_search import SessionSearchArgs

        # Pydantic should accept both the canonical name and the aliases.
        for key in ("query", "q", "text"):
            args = SessionSearchArgs.model_validate({key: "last week's OKR call"})
            assert args.query == "last week's OKR call"

    def test_invalid_session_id_not_a_hard_error_at_arg_level(self):
        """Invalid session_id values are accepted by the args model — the
        runner silently drops them rather than 500-ing. This asserts the
        arg model doesn't reject them outright (validation is deferred to
        the runner)."""
        from app.agents.tools.session_search import SessionSearchArgs

        args = SessionSearchArgs.model_validate(
            {"query": "hello", "session_id": "not-a-uuid"}
        )
        assert args.session_id == "not-a-uuid"

    def test_valid_uuid_accepted(self):
        from app.agents.tools.session_search import SessionSearchArgs

        sid = str(uuid.uuid4())
        args = SessionSearchArgs.model_validate({"query": "x", "session_id": sid})
        assert args.session_id == sid
