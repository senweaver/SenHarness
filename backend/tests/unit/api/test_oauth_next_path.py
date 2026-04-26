"""OAuth ``next`` parameter sanitisation tests.

Every item here corresponds to a real open-redirect class we want closed.
"""

from __future__ import annotations

import pytest

from app.api.helpers import sanitize_next_path as _sanitize_next_path


class TestSameOriginPassthrough:
    @pytest.mark.parametrize(
        "path",
        [
            "/",
            "/dashboard",
            "/agents/abc-123",
            "/settings/profile?tab=mfa",
            "/path/with/deep/segments",
            "/?foo=bar&baz=qux",
        ],
    )
    def test_safe_same_origin_paths_preserved(self, path):
        assert _sanitize_next_path(path) == path


class TestOpenRedirectBlocked:
    @pytest.mark.parametrize(
        "evil",
        [
            "http://evil.com",
            "https://evil.com/path",
            "//evil.com/path",          # protocol-relative
            "//evil.com?sess=abc",
            "\\\\evil.com\\share",      # UNC (Windows)
            "/\\evil.com/path",         # mixed-slash protocol-relative
            "javascript:alert(1)",
            "javascript:alert(document.cookie)",
            "data:text/html,<script>x</script>",
            "mailto:user@evil.com",
        ],
    )
    def test_open_redirect_patterns_neutralised(self, evil):
        assert _sanitize_next_path(evil) == "/"


class TestEdgeCases:
    def test_none_defaults_to_root(self):
        assert _sanitize_next_path(None) == "/"

    def test_empty_defaults_to_root(self):
        assert _sanitize_next_path("") == "/"

    def test_single_slash_is_root(self):
        assert _sanitize_next_path("/") == "/"

    def test_trailing_colon_in_first_segment_blocked(self):
        # javascript:// tricks resolve through if we only looked at scheme.
        assert _sanitize_next_path("/evil:path") == "/"
