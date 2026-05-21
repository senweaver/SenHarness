"""Unit: ``sanitize_for_hub`` email redaction (M3.2).

The sanitizer is a pure function — these tests stay free of DB / IO
and focus on the email-rewrite step:

* single + multiple addresses on a line
* tagged form (``user+tag@domain.com``)
* embedded inside markdown ``mailto:`` links
* boundary positions (start of body, end of line, end of body)
* statistics counter matches the actual replacements
"""

from __future__ import annotations

import uuid

from app.services.skill_sanitize import (
    EMAIL_PLACEHOLDER,
    sanitize_for_hub,
)

_WS = uuid.uuid4()


def _sanitize(body: str) -> tuple[str, int]:
    out = sanitize_for_hub(body, [], workspace_slug="acme-team", workspace_id=_WS)
    return out.content_md, out.stats.redacted_emails


def test_single_email_replaced() -> None:
    rewritten, count = _sanitize("ping alice@example.com for help")
    assert EMAIL_PLACEHOLDER in rewritten
    assert "alice@example.com" not in rewritten
    assert count == 1


def test_multiple_emails_on_one_line_all_replaced() -> None:
    body = "owners: alice@example.com, bob@example.org and carol@x.io"
    rewritten, count = _sanitize(body)
    assert rewritten.count(EMAIL_PLACEHOLDER) == 3
    for raw in ("alice@example.com", "bob@example.org", "carol@x.io"):
        assert raw not in rewritten
    assert count == 3


def test_tagged_email_form_is_caught() -> None:
    rewritten, count = _sanitize("filter: dev+release@team.acme.io")
    assert EMAIL_PLACEHOLDER in rewritten
    assert "dev+release@team.acme.io" not in rewritten
    assert count == 1


def test_email_inside_markdown_mailto_link() -> None:
    body = "Reach out via [mail](mailto:alice@example.com) anytime."
    rewritten, count = _sanitize(body)
    assert "alice@example.com" not in rewritten
    assert "mailto:[email-redacted]" in rewritten
    assert count == 1


def test_email_at_start_of_body() -> None:
    rewritten, count = _sanitize("alice@example.com — owner")
    assert rewritten.startswith(EMAIL_PLACEHOLDER)
    assert count == 1


def test_email_at_end_of_line() -> None:
    body = "Owner: alice@example.com\nNext line"
    rewritten, count = _sanitize(body)
    assert rewritten.startswith("Owner: ")
    assert "[email-redacted]\nNext line" in rewritten
    assert count == 1


def test_no_email_in_body_zero_stats() -> None:
    rewritten, count = _sanitize("nothing to see here")
    assert rewritten == "nothing to see here"
    assert count == 0


def test_email_count_matches_stats_field() -> None:
    body = "a@b.io, c@d.io, e@f.io"
    out = sanitize_for_hub(body, [], workspace_slug="acme", workspace_id=_WS)
    assert out.stats.redacted_emails == 3
    assert out.stats.failure_reason is None
