"""Unit: ``sanitize_for_hub`` workspace-slug rewrite (M3.2).

After the URL pass, the sanitizer scans the entire body for any
remaining literal occurrence of the workspace slug — file paths,
inline mentions, identifier-like tokens — and replaces each match
with ``[ws-slug-redacted]`` (case-insensitive).

These tests pin:

* file-path occurrences (``/data/<slug>/...``)
* inline mention (``the <slug> workspace``)
* mixed-case match
* multiple occurrences counted independently
* empty / whitespace slug is a no-op (defensive)
"""

from __future__ import annotations

import uuid

from app.services.skill_sanitize import (
    WORKSPACE_SLUG_PLACEHOLDER,
    sanitize_for_hub,
)

_WS = uuid.uuid4()
_SLUG = "globex"


def _sanitize(body: str, slug: str = _SLUG) -> tuple[str, int]:
    out = sanitize_for_hub(body, [], workspace_slug=slug, workspace_id=_WS)
    return out.content_md, out.stats.redacted_paths


def test_path_with_slug_replaced() -> None:
    body = "Run `/data/globex/runtime/log.txt` to inspect."
    rewritten, count = _sanitize(body)
    assert "globex" not in rewritten
    assert WORKSPACE_SLUG_PLACEHOLDER in rewritten
    assert count == 1


def test_inline_mention_replaced() -> None:
    body = "All members of the globex workspace can run this skill."
    rewritten, count = _sanitize(body)
    assert "globex" not in rewritten
    assert count == 1


def test_case_insensitive_match() -> None:
    body = "Compare /data/Globex/v1 vs /data/GLOBEX/v2"
    rewritten, count = _sanitize(body)
    assert "Globex" not in rewritten
    assert "GLOBEX" not in rewritten
    assert count == 2


def test_multiple_occurrences_each_counted() -> None:
    body = "globex prod and globex stage and globex dev"
    rewritten, count = _sanitize(body)
    assert rewritten.count(WORKSPACE_SLUG_PLACEHOLDER) == 3
    assert count == 3


def test_empty_slug_is_noop() -> None:
    body = "anything goes here, even @anyone or globex"
    out = sanitize_for_hub(body, [], workspace_slug="", workspace_id=_WS)
    assert out.content_md == body
    assert out.stats.redacted_paths == 0
    assert out.stats.redacted_urls == 0


def test_whitespace_only_slug_is_noop() -> None:
    body = "globex still appears"
    out = sanitize_for_hub(body, [], workspace_slug="   ", workspace_id=_WS)
    assert out.content_md == body
    assert out.stats.redacted_paths == 0


def test_slug_inside_url_handled_via_url_pass_then_paths_zero() -> None:
    body = "Repo at https://github.com/globex/skills"
    out = sanitize_for_hub(body, [], workspace_slug=_SLUG, workspace_id=_WS)
    assert out.stats.redacted_urls == 1
    assert "globex" not in out.content_md


def test_slug_appears_in_path_after_url_redaction() -> None:
    body = "URL https://example.com/globex/page and bare path /var/globex/cache"
    out = sanitize_for_hub(body, [], workspace_slug=_SLUG, workspace_id=_WS)
    assert out.stats.redacted_urls == 1
    assert out.stats.redacted_paths >= 1
    assert "globex" not in out.content_md
