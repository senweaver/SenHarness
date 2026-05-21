"""Privacy sanitizer for cross-workspace SkillPack uploads (M3.2).

Purpose
-------

When a workspace promotes one of its SkillPack bodies to the federation
``Skill Hub`` (M3.1 / M3.3), the body must not leak workspace-identifying
or person-identifying tokens. The sanitizer is the single choke point
that walks the SkillPack content + metadata and strips:

1. **Email addresses.** Replaced with ``[email-redacted]``.
2. **URLs that embed the workspace slug.** Reduced to their host so a
   future link still tells the reader the rough source kind without
   exposing the tenant slug. URLs that don't carry the slug pass
   through (they're typically docs / SDK links the author wants kept).
3. **Bare workspace slug occurrences.** Path segments, mentions, and
   any other plain-text appearance of the slug are replaced with
   ``[ws-slug-redacted]`` (case-insensitive). File-path references
   like ``/data/{slug}/foo.txt`` are covered by the same rewrite.
4. **Person names.** Optional and dependency-driven: when a PII
   detector is provided the sanitizer replaces matches with
   ``[name-redacted]``. When the detector is unavailable the step is
   silently skipped (``stats.redacted_pii == 0``).
5. **Workspace-admin custom redaction patterns.** Each entry under
   ``workspace.home_config_json["hub_promotion"]["extra_redaction_patterns"]``
   is compiled as a regex and applied as one extra redaction layer.
   Invalid regex strings are silently dropped (the workspace settings
   UI is responsible for surfacing the validation error before the
   row lands in the DB).

The companion :func:`hash_run_id` collapses each ``source_run_id`` to a
SHA-256 prefix salted with the source workspace id. Two distinct
workspaces hashing the same UUID land on different digests so a hub
audit can't cross-correlate sessions.

Pure function
-------------

The sanitizer never reads the database or commits anything: callers
hand it a body + metadata and receive a :class:`SanitizedHubPayload`.
The companion :mod:`app.services.hub_promote_pipeline` is what drives
the M3.3 promote / preview flow on top of it. M3.2 keeps the sanitizer
deterministic and side-effect-free so it can be tested without a DB
or a Redis pair.
"""

from __future__ import annotations

import hashlib
import logging
import re
import uuid
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field

__all__ = [
    "EMAIL_PLACEHOLDER",
    "EMAIL_REGEX",
    "EXTRA_PATTERN_PLACEHOLDER",
    "PII_PLACEHOLDER",
    "URL_REGEX",
    "WORKSPACE_SLUG_PLACEHOLDER",
    "PiiDetectionFn",
    "SanitizationStats",
    "SanitizedHubPayload",
    "hash_run_id",
    "hash_source_run_ids",
    "sanitize_for_hub",
]

log = logging.getLogger(__name__)


# ── Constants ────────────────────────────────────────────────
EMAIL_PLACEHOLDER = "[email-redacted]"
WORKSPACE_SLUG_PLACEHOLDER = "[ws-slug-redacted]"
PII_PLACEHOLDER = "[name-redacted]"
EXTRA_PATTERN_PLACEHOLDER = "[redacted]"

EMAIL_REGEX = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
URL_REGEX = re.compile(r"https?://[^\s)\]>}'\"`]+", re.IGNORECASE)

# A run_id collapsed via :func:`hash_run_id` keeps the first 16 hex
# characters of a SHA-256 digest. 64 bits is plenty for an audit
# pointer where the goal is "show that two hub versions share a
# proposing run, never actually look the run up".
_RUN_ID_HASH_LEN = 16

# Type alias for the optional PII detector callable: receives the
# text to scan and returns a list of ``(start, end, surface_form)``
# tuples for each match. Caller is responsible for non-overlapping,
# sorted output; the sanitizer normalises to safe replacement order.
PiiDetectionFn = Callable[[str], Sequence[tuple[int, int, str]]]


# ── Data shapes ──────────────────────────────────────────────
@dataclass
class SanitizationStats:
    redacted_emails: int = 0
    redacted_urls: int = 0
    redacted_paths: int = 0
    redacted_pii: int = 0
    redacted_extra: int = 0
    run_id_hashed_count: int = 0
    failure_reason: str | None = None


@dataclass
class SanitizedHubPayload:
    content_md: str
    source_run_id_hashes: list[str] = field(default_factory=list)
    stats: SanitizationStats = field(default_factory=SanitizationStats)

    @property
    def succeeded(self) -> bool:
        return self.stats.failure_reason is None


# ── Public API ───────────────────────────────────────────────
def sanitize_for_hub(
    content_md: str,
    source_run_ids: Iterable[uuid.UUID | str] | None,
    *,
    workspace_slug: str,
    workspace_id: uuid.UUID,
    pii_detector_fn: PiiDetectionFn | None = None,
    extra_redaction_patterns: Iterable[str] | None = None,
    skip_pii_detection: bool = False,
) -> SanitizedHubPayload:
    """Sanitize ``content_md`` and hash ``source_run_ids`` for hub upload.

    Parameters
    ----------
    content_md:
        Markdown body of the SkillPack about to be promoted.
    source_run_ids:
        Run identifiers that fed the version. Each id is hashed to a
        16-hex-character SHA-256 digest salted with ``workspace_id``.
    workspace_slug:
        Source workspace slug. Used for both URL host reduction and
        the bare-slug rewrite.
    workspace_id:
        Source workspace id, used as the salt for run-id hashing so
        two workspaces never produce overlapping hashes for the same
        UUID.
    pii_detector_fn:
        Optional callable that returns ``[(start, end, surface), ...]``
        spans to redact. Provided by
        :mod:`app.services.skill_sanitize_pii` when the runtime ships
        a PII engine; ``None`` skips name detection.
    extra_redaction_patterns:
        Optional regex strings supplied by the workspace
        ``home_config_json["hub_promotion"]["extra_redaction_patterns"]``.
        Each is compiled lazily and applied after the built-in steps.
    skip_pii_detection:
        Workspace-level opt-out (very simple skills with no PII risk).
        Defaults to ``False`` so the safer path is the default.

    Returns
    -------
    SanitizedHubPayload
        Always populated. On exception the original body is preserved,
        ``stats.failure_reason`` carries the cause, and the run-id
        hashes that *did* compute successfully are returned. The
        caller decides whether ``HubSettings.sanitizer_required``
        forces it to block (see :mod:`app.services.hub_promote_pipeline`).
    """
    stats = SanitizationStats()
    body = content_md or ""
    try:
        body, emails = _redact_emails(body)
        stats.redacted_emails = emails

        body, url_count, path_count = _redact_workspace_slug_and_urls(
            body, workspace_slug=workspace_slug
        )
        stats.redacted_urls = url_count
        stats.redacted_paths = path_count

        if pii_detector_fn is not None and not skip_pii_detection:
            body, pii_count = _redact_pii(body, detector=pii_detector_fn)
            stats.redacted_pii = pii_count

        if extra_redaction_patterns:
            body, extra_count = _apply_extra_patterns(body, patterns=extra_redaction_patterns)
            stats.redacted_extra = extra_count
    except Exception as exc:  # pragma: no cover — defensive
        log.warning(
            "skill_sanitize: sanitize_for_hub failed for workspace %s: %s",
            workspace_id,
            exc,
        )
        stats.failure_reason = f"{type(exc).__name__}: {exc}"
        run_hashes = hash_source_run_ids(source_run_ids or [], workspace_id=workspace_id)
        stats.run_id_hashed_count = len(run_hashes)
        return SanitizedHubPayload(
            content_md=content_md or "",
            source_run_id_hashes=run_hashes,
            stats=stats,
        )

    run_hashes = hash_source_run_ids(source_run_ids or [], workspace_id=workspace_id)
    stats.run_id_hashed_count = len(run_hashes)
    return SanitizedHubPayload(
        content_md=body,
        source_run_id_hashes=run_hashes,
        stats=stats,
    )


def hash_run_id(
    run_id: uuid.UUID | str,
    *,
    salt: str = "",
) -> str:
    """SHA-256 prefix of ``salt + run_id`` (first 16 hex characters).

    Deterministic for fixed inputs; never reverses to the original
    UUID. Salt collapses to empty string when the caller has no
    workspace context — prefer :func:`hash_source_run_ids` so the
    workspace id is always mixed in.
    """
    payload = (salt + str(run_id)).encode("utf-8")
    digest = hashlib.sha256(payload).hexdigest()
    return digest[:_RUN_ID_HASH_LEN]


def hash_source_run_ids(
    run_ids: Iterable[uuid.UUID | str],
    *,
    workspace_id: uuid.UUID,
) -> list[str]:
    """Bulk-hash run ids with the workspace id as salt.

    Two different workspaces calling with the same UUID produce
    different digests so a hub audit cannot correlate sessions across
    tenants. Order is preserved; duplicates are kept (the caller
    decides whether to deduplicate post-hash).
    """
    salt = str(workspace_id)
    return [hash_run_id(rid, salt=salt) for rid in run_ids]


# ── Step implementations ─────────────────────────────────────
def _redact_emails(text: str) -> tuple[str, int]:
    count = 0

    def _sub(_m: re.Match[str]) -> str:
        nonlocal count
        count += 1
        return EMAIL_PLACEHOLDER

    return EMAIL_REGEX.sub(_sub, text), count


def _redact_workspace_slug_and_urls(text: str, *, workspace_slug: str) -> tuple[str, int, int]:
    """Rewrite URLs that carry the slug, then strip bare slug occurrences.

    Returns ``(text, url_count, path_count)`` where ``url_count`` is
    the number of URL hosts that were collapsed and ``path_count`` is
    the number of remaining bare-slug rewrites (file paths, inline
    mentions, etc.). Empty / whitespace-only ``workspace_slug`` is
    treated as a no-op so a malformed call doesn't accidentally rewrite
    every line of the body.
    """
    slug = (workspace_slug or "").strip()
    if not slug:
        return text, 0, 0

    url_count = 0

    def _url_sub(m: re.Match[str]) -> str:
        nonlocal url_count
        url = m.group(0).rstrip(".,;:!?")
        trailing = m.group(0)[len(url) :]
        if slug.lower() in url.lower():
            url_count += 1
            host = _extract_host(url) or ""
            if host:
                return f"https://{host}/{WORKSPACE_SLUG_PLACEHOLDER}{trailing}"
            return f"{WORKSPACE_SLUG_PLACEHOLDER}{trailing}"
        return m.group(0)

    rewritten = URL_REGEX.sub(_url_sub, text)

    bare_pattern = re.compile(re.escape(slug), re.IGNORECASE)
    path_count = 0

    def _bare_sub(_m: re.Match[str]) -> str:
        nonlocal path_count
        path_count += 1
        return WORKSPACE_SLUG_PLACEHOLDER

    rewritten = bare_pattern.sub(_bare_sub, rewritten)
    return rewritten, url_count, path_count


def _extract_host(url: str) -> str | None:
    """Return the bare host from an ``http(s)://`` URL.

    Lightweight on purpose — we don't want to bring in ``urllib.parse``
    just to pull the netloc, because the URL_REGEX result is always a
    ``http(s)://...`` string and a slash split is enough.
    """
    if "://" not in url:
        return None
    after_scheme = url.split("://", 1)[1]
    host = after_scheme.split("/", 1)[0]
    host = host.split("?", 1)[0].split("#", 1)[0]
    return host or None


def _redact_pii(text: str, *, detector: PiiDetectionFn) -> tuple[str, int]:
    """Apply ``detector`` and replace each span with the PII placeholder.

    When two spans overlap, the **outermost / longest** wins so a
    detector that emits both ``"Alice Wong"`` and the constituent
    ``"Alice"`` collapses to one replacement — the larger surface form
    is what the user wrote, and the per-token splits would
    double-count in the audit stats. Zero-width or out-of-range spans
    are silently dropped (defensive — a buggy detector cannot corrupt
    the body).
    """
    spans = list(detector(text) or [])
    if not spans:
        return text, 0

    coerced: list[tuple[int, int]] = []
    for entry in spans:
        try:
            start, end, _surface = entry
        except (TypeError, ValueError):
            continue
        if not isinstance(start, int) or not isinstance(end, int):
            continue
        if end <= start or start < 0 or end > len(text):
            continue
        coerced.append((start, end))

    # Sort by start ascending then end descending — that way the
    # longest enclosing span at each position wins; subsequent
    # overlapping spans are dropped against the previously-claimed
    # range.
    coerced.sort(key=lambda pair: (pair[0], -pair[1]))
    merged: list[tuple[int, int]] = []
    for start, end in coerced:
        if merged and start < merged[-1][1]:
            continue
        merged.append((start, end))

    rewritten = text
    for start, end in reversed(merged):
        rewritten = rewritten[:start] + PII_PLACEHOLDER + rewritten[end:]
    return rewritten, len(merged)


def _apply_extra_patterns(text: str, *, patterns: Iterable[str]) -> tuple[str, int]:
    count = 0
    rewritten = text
    for raw in patterns:
        if not raw:
            continue
        try:
            compiled = re.compile(raw, re.IGNORECASE)
        except re.error:
            # Invalid admin-defined regex: skip silently. The
            # workspace settings UI is responsible for surfacing the
            # validation error; the sanitizer must not crash a hub
            # promotion just because one pattern is malformed.
            continue

        def _sub(_m: re.Match[str]) -> str:
            nonlocal count
            count += 1
            return EXTRA_PATTERN_PLACEHOLDER

        rewritten = compiled.sub(_sub, rewritten)
    return rewritten, count
