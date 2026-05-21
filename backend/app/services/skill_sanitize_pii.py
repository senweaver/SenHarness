"""Optional PII detector wiring for :mod:`app.services.skill_sanitize`.

The base sanitizer (:func:`app.services.skill_sanitize.sanitize_for_hub`)
treats name detection as a pluggable layer: if the runtime ships a
detector callable, it gets used; otherwise the step is a no-op and
``stats.redacted_pii`` stays at zero. This module concentrates the
"is the detector available?" decision in one place so the M3.3 promote
pipeline doesn't have to re-resolve it on every call.

Resolution order (first that succeeds wins):

1. ``pydantic_ai_shields.PiiDetector`` — the same library that powers
   the runtime input/output guards in :mod:`app.agents.harness.shields`.
   When installed we adapt its public ``detect`` shape to the
   ``[(start, end, surface), ...]`` contract the sanitizer expects.
2. ``presidio-analyzer`` — fallback when the operator installed the
   Microsoft Presidio engine instead of the harness shields. Adapter
   maps each ``RecognizerResult`` to the same span tuple.
3. ``None`` — neither installed; the sanitizer logs nothing extra and
   simply skips the name redaction pass. The hub upload still
   proceeds (the guard against rare edge cases sits at the
   ``HubSettings.sanitizer_required`` gate, not here).

The function is intentionally **synchronous**: the sanitizer runs
inside a request and the detector is always a CPU-bound regex / model.
The DB session parameter is accepted for forward compatibility — a
future workspace-scoped allowlist (curated names that the workspace
itself flagged) can read from the same row without churning the call
sites. M3.2 itself does not query the DB.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.skill_sanitize import PiiDetectionFn

__all__ = [
    "build_pii_detector_for_workspace",
    "is_pii_detector_available",
]

log = logging.getLogger(__name__)


def is_pii_detector_available() -> bool:
    """Whether at least one of the supported PII engines is importable.

    Cheap and side-effect free: the M3.3 ``preview_promotion`` reads
    this to decide whether the UI should warn the user that name
    detection is currently a no-op.
    """
    try:
        from pydantic_ai_shields import PiiDetector  # noqa: F401

        return True
    except ImportError:
        pass
    try:
        from presidio_analyzer import AnalyzerEngine  # noqa: F401

        return True
    except ImportError:
        return False


async def build_pii_detector_for_workspace(
    workspace_id: uuid.UUID,
    db: AsyncSession,
) -> PiiDetectionFn | None:
    """Resolve a PII detector callable for the given workspace.

    Returns ``None`` when no engine is available — the sanitizer
    treats that as "skip name detection" rather than failure. ``db``
    and ``workspace_id`` are accepted for forward compatibility (a
    workspace-curated allowlist may live on the workspace row in a
    future milestone); M3.2 does not consult either.
    """
    del workspace_id, db

    detector = _try_pydantic_ai_shields()
    if detector is not None:
        return detector

    detector = _try_presidio()
    if detector is not None:
        return detector

    return None


# ── Adapters ─────────────────────────────────────────────────
def _try_pydantic_ai_shields() -> PiiDetectionFn | None:
    try:
        from pydantic_ai_shields import PiiDetector
    except ImportError:
        return None

    try:
        engine = PiiDetector(action="log")
    except Exception as exc:  # pragma: no cover — defensive
        log.warning(
            "skill_sanitize_pii: failed to instantiate PiiDetector: %s",
            exc,
        )
        return None

    detect_fn = _resolve_shields_detect_fn(engine)
    if detect_fn is None:
        return None

    def _detect(text: str) -> list[tuple[int, int, str]]:
        try:
            raw = detect_fn(text)
        except Exception as exc:  # pragma: no cover — defensive
            log.warning(
                "skill_sanitize_pii: PiiDetector.detect failed: %s",
                exc,
            )
            return []
        return list(_normalise_shields_spans(raw, text))

    return _detect


def _resolve_shields_detect_fn(engine: Any) -> Any | None:
    """Return the most specific ``detect`` callable on ``engine``.

    The shields library evolves the public name across versions —
    ``detect`` is the M0+ shape; ``analyze`` is an older variant.
    Both return iterables of objects with ``start`` / ``end`` /
    ``entity_type`` (or compatible attributes).
    """
    for name in ("detect", "find", "analyze"):
        fn = getattr(engine, name, None)
        if callable(fn):
            return fn
    return None


def _normalise_shields_spans(raw: Any, text: str) -> list[tuple[int, int, str]]:
    """Coerce the shields detector output to ``(start, end, surface)``.

    Accepts:

    * iterables of dataclass-like objects with ``start`` / ``end``
      attributes (and an ``entity_type`` we ignore);
    * iterables of ``(start, end)`` or ``(start, end, label)`` tuples;
    * iterables of dicts with ``start`` / ``end`` keys.

    Anything else is silently dropped — the sanitizer treats a buggy
    detector as "no PII detected".
    """
    spans: list[tuple[int, int, str]] = []
    if raw is None:
        return spans
    try:
        items = list(raw)
    except TypeError:
        return spans

    for item in items:
        start, end = _extract_span_bounds(item)
        if start is None or end is None:
            continue
        if end <= start or start < 0 or end > len(text):
            continue
        spans.append((start, end, text[start:end]))
    return spans


def _extract_span_bounds(item: Any) -> tuple[int | None, int | None]:
    if isinstance(item, dict):
        start = item.get("start")
        end = item.get("end")
        return _coerce_int(start), _coerce_int(end)
    if isinstance(item, (list, tuple)):
        if len(item) >= 2:
            return _coerce_int(item[0]), _coerce_int(item[1])
        return None, None
    start = getattr(item, "start", None)
    end = getattr(item, "end", None)
    return _coerce_int(start), _coerce_int(end)


def _coerce_int(val: Any) -> int | None:
    if isinstance(val, bool):
        return None
    if isinstance(val, int):
        return val
    return None


# ── Presidio fallback ────────────────────────────────────────
def _try_presidio() -> PiiDetectionFn | None:
    try:
        from presidio_analyzer import AnalyzerEngine
    except ImportError:
        return None

    try:
        engine = AnalyzerEngine()
    except Exception as exc:  # pragma: no cover — defensive
        log.warning(
            "skill_sanitize_pii: failed to start Presidio AnalyzerEngine: %s",
            exc,
        )
        return None

    def _detect(text: str) -> list[tuple[int, int, str]]:
        try:
            raw = engine.analyze(
                text=text,
                language="en",
                entities=["PERSON"],
            )
        except Exception as exc:  # pragma: no cover — defensive
            log.warning(
                "skill_sanitize_pii: Presidio analyze failed: %s",
                exc,
            )
            return []
        return list(_normalise_shields_spans(raw, text))

    return _detect
