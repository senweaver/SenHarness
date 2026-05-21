"""Unit: ``sanitize_for_hub`` URL handling (M3.2).

URL rewrites only fire when the workspace slug appears anywhere inside
the URL. URLs that don't carry the slug pass through unchanged so
documentation and SDK links stay readable. After the URL pass, any
remaining bare-slug occurrence in the rewritten host is also stripped.
"""

from __future__ import annotations

import uuid

from app.services.skill_sanitize import (
    WORKSPACE_SLUG_PLACEHOLDER,
    sanitize_for_hub,
)

_WS = uuid.uuid4()
_SLUG = "myteam"


def _sanitize(body: str) -> tuple[str, int, int]:
    out = sanitize_for_hub(body, [], workspace_slug=_SLUG, workspace_id=_WS)
    return out.content_md, out.stats.redacted_urls, out.stats.redacted_paths


def test_url_with_slug_collapses_to_host_plus_placeholder() -> None:
    body = "Doc: https://example.com/workspaces/myteam/setup"
    rewritten, urls, _paths = _sanitize(body)
    assert urls == 1
    assert WORKSPACE_SLUG_PLACEHOLDER in rewritten
    assert "/myteam/" not in rewritten
    assert rewritten.startswith("Doc: https://example.com/")


def test_url_without_slug_passes_through() -> None:
    body = "Reference https://docs.example.com/api/v1/agents"
    rewritten, urls, paths = _sanitize(body)
    assert urls == 0
    assert paths == 0
    assert "https://docs.example.com/api/v1/agents" in rewritten


def test_markdown_link_with_slug_collapses() -> None:
    body = "[Setup](https://example.com/myteam/setup) for new members."
    rewritten, urls, _paths = _sanitize(body)
    assert urls == 1
    assert "(https://example.com/myteam/setup)" not in rewritten
    assert WORKSPACE_SLUG_PLACEHOLDER in rewritten


def test_multiple_urls_one_line_each_handled() -> None:
    body = (
        "Mix https://docs.example.com/page and "
        "https://app.example.com/myteam/dash with "
        "https://app.example.com/myteam/skills end."
    )
    rewritten, urls, _paths = _sanitize(body)
    assert urls == 2
    assert "https://docs.example.com/page" in rewritten
    assert "/myteam/" not in rewritten
    assert rewritten.count(WORKSPACE_SLUG_PLACEHOLDER) >= 2


def test_url_with_slug_in_query_string_redacted() -> None:
    body = "Search: https://example.com/?ws=myteam&q=foo"
    rewritten, urls, _paths = _sanitize(body)
    assert urls == 1
    assert "myteam" not in rewritten


def test_trailing_punctuation_preserved() -> None:
    body = "Visit https://example.com/myteam/skills, then click."
    rewritten, urls, _paths = _sanitize(body)
    assert urls == 1
    assert rewritten.endswith(", then click.")
    assert "/myteam/" not in rewritten


def test_case_insensitive_url_match() -> None:
    body = "Url: https://example.com/MyTeam/Setup"
    rewritten, urls, _paths = _sanitize(body)
    assert urls == 1
    assert "/MyTeam/" not in rewritten


def test_zero_url_zero_path_clean_body() -> None:
    body = "no urls or slug references here at all"
    out = sanitize_for_hub(body, [], workspace_slug=_SLUG, workspace_id=_WS)
    assert out.stats.redacted_urls == 0
    assert out.stats.redacted_paths == 0
    assert out.content_md == body
