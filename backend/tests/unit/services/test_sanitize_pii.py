"""Unit: ``sanitize_for_hub`` PII detector wiring (M3.2).

The base sanitizer treats name detection as a pluggable layer:

* Detector callable provided → spans are replaced with
  ``[name-redacted]`` and ``stats.redacted_pii`` reflects the count.
* Detector ``None`` → step is silently skipped, ``stats.redacted_pii``
  stays at 0, body unchanged.
* Detector raises → caught defensively, treated as "no PII detected".

The companion ``build_pii_detector_for_workspace`` is also exercised:
when neither ``pydantic_ai_shields`` nor ``presidio-analyzer`` is
installed, it returns ``None`` (test environments don't always have
them).
"""

from __future__ import annotations

import uuid

import pytest

from app.services.skill_sanitize import (
    PII_PLACEHOLDER,
    sanitize_for_hub,
)
from app.services.skill_sanitize_pii import (
    build_pii_detector_for_workspace,
    is_pii_detector_available,
)

pytestmark = pytest.mark.asyncio


def _fake_name_detector(spans: list[tuple[int, int, str]]):
    def _detect(_text: str) -> list[tuple[int, int, str]]:
        return list(spans)

    return _detect


async def test_detector_present_replaces_spans() -> None:
    body = "Owner: Alice Wong, escalate to Bob Lee"
    spans = [
        (body.index("Alice Wong"), body.index("Alice Wong") + len("Alice Wong"), "Alice Wong"),
        (body.index("Bob Lee"), body.index("Bob Lee") + len("Bob Lee"), "Bob Lee"),
    ]
    out = sanitize_for_hub(
        body,
        [],
        workspace_slug="acme",
        workspace_id=uuid.uuid4(),
        pii_detector_fn=_fake_name_detector(spans),
    )
    assert "Alice Wong" not in out.content_md
    assert "Bob Lee" not in out.content_md
    assert out.content_md.count(PII_PLACEHOLDER) == 2
    assert out.stats.redacted_pii == 2


async def test_detector_absent_zero_pii_count() -> None:
    body = "Owner: Alice Wong"
    out = sanitize_for_hub(
        body,
        [],
        workspace_slug="acme",
        workspace_id=uuid.uuid4(),
        pii_detector_fn=None,
    )
    assert out.content_md == body
    assert out.stats.redacted_pii == 0


async def test_skip_pii_detection_flag_skips_call_even_if_detector_present() -> None:
    body = "Owner: Alice Wong"
    spans = [(0, 5, "Owner")]
    out = sanitize_for_hub(
        body,
        [],
        workspace_slug="acme",
        workspace_id=uuid.uuid4(),
        pii_detector_fn=_fake_name_detector(spans),
        skip_pii_detection=True,
    )
    assert out.stats.redacted_pii == 0
    assert "Owner" in out.content_md


async def test_overlapping_spans_only_outermost_replaced() -> None:
    body = "Alice Wong runs the team"
    spans = [
        (0, 10, "Alice Wong"),
        (0, 5, "Alice"),
        (6, 10, "Wong"),
    ]
    out = sanitize_for_hub(
        body,
        [],
        workspace_slug="acme",
        workspace_id=uuid.uuid4(),
        pii_detector_fn=_fake_name_detector(spans),
    )
    assert out.content_md.startswith(PII_PLACEHOLDER)
    assert out.stats.redacted_pii == 1


async def test_buggy_detector_returns_invalid_spans_handled() -> None:
    body = "Owner: Alice Wong"
    spans = [
        (-1, 100, "out of range"),
        (5, 5, "zero width"),
    ]
    out = sanitize_for_hub(
        body,
        [],
        workspace_slug="acme",
        workspace_id=uuid.uuid4(),
        pii_detector_fn=_fake_name_detector(spans),  # type: ignore[arg-type]
    )
    assert out.content_md == body
    assert out.stats.redacted_pii == 0


async def test_build_pii_detector_returns_none_when_no_engine() -> None:
    detector = await build_pii_detector_for_workspace(
        uuid.uuid4(),
        db=None,  # type: ignore[arg-type]
    )
    if not is_pii_detector_available():
        assert detector is None
    else:
        # Engine importable. The helper either resolves a callable, or
        # falls back to ``None`` when the shipped engine's __init__
        # signature drifted from what the adapter expects (a logged
        # warning, not a hard failure).
        assert detector is None or callable(detector)


async def test_extra_redaction_patterns_applied() -> None:
    body = "Internal codename PROJECT-NEPTUNE is hot."
    out = sanitize_for_hub(
        body,
        [],
        workspace_slug="acme",
        workspace_id=uuid.uuid4(),
        pii_detector_fn=None,
        extra_redaction_patterns=[r"PROJECT-[A-Z]+"],
    )
    assert "PROJECT-NEPTUNE" not in out.content_md
    assert out.stats.redacted_extra == 1


async def test_invalid_regex_pattern_silently_skipped() -> None:
    body = "Mention PROJECT-NEPTUNE here."
    out = sanitize_for_hub(
        body,
        [],
        workspace_slug="acme",
        workspace_id=uuid.uuid4(),
        extra_redaction_patterns=["[unbalanced"],
    )
    assert "PROJECT-NEPTUNE" in out.content_md
    assert out.stats.redacted_extra == 0
    assert out.stats.failure_reason is None
