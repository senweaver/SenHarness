"""Pure-Python unified diff helpers for SkillPack content (M1.10).

All helpers are side-effect free, do no DB I/O, and never raise on
malformed input. M2 evolver approval UI imports the same functions
to render its patch previews; the API layer
(:mod:`app.api.v1.skills_persistence` `diff_router`) is just a thin
wrapper.

Truncation is applied only at the display boundary. Callers that need
the full diff (downloads, audit) bypass :func:`truncate_diff_for_display`
and read the raw output of :func:`render_unified_diff` /
:func:`render_multi_file_diff`.
"""

from __future__ import annotations

import difflib
from dataclasses import asdict, dataclass


@dataclass(slots=True)
class DiffStats:
    """Aggregate per-diff counts. ``hunks`` is the number of ``@@``
    headers, useful as a rough "is this a wholesale rewrite?" signal."""

    added_lines: int
    removed_lines: int
    hunks: int

    def as_dict(self) -> dict[str, int]:
        return asdict(self)


@dataclass(slots=True)
class UnifiedDiffResult:
    """Output envelope for the diff helpers."""

    diff: str
    stats: DiffStats
    files_changed: list[str]


_DEFAULT_CONTEXT = 3
_MIN_CONTEXT = 0
_MAX_CONTEXT = 10
_TRUNCATION_MARKER = "\n… [diff truncated, view full via download API] …\n"


def _coerce_text(value: str | None) -> str:
    if value is None:
        return ""
    return str(value)


def _clamp_context(value: int) -> int:
    if value < _MIN_CONTEXT:
        return _MIN_CONTEXT
    if value > _MAX_CONTEXT:
        return _MAX_CONTEXT
    return value


def _split_keepends(text: str) -> list[str]:
    """``difflib.unified_diff`` works best on ``splitlines(keepends=True)``;
    we centralise the call so a missing trailing newline is preserved
    consistently across single- and multi-file paths."""
    return text.splitlines(keepends=True)


def _count_added_removed(diff_lines: list[str]) -> tuple[int, int, int]:
    added = 0
    removed = 0
    hunks = 0
    for line in diff_lines:
        if line.startswith("+++") or line.startswith("---"):
            continue
        if line.startswith("@@"):
            hunks += 1
            continue
        if line.startswith("+"):
            added += 1
        elif line.startswith("-"):
            removed += 1
    return added, removed, hunks


def render_unified_diff(
    old_content: str | None,
    new_content: str | None,
    *,
    context_lines: int = _DEFAULT_CONTEXT,
    file_label: str = "SKILL.md",
    from_label: str = "old",
    to_label: str = "new",
) -> UnifiedDiffResult:
    """Compute a single-file unified diff with stats.

    Empty change → ``diff=""``, ``stats`` zeroed, but ``files_changed``
    still carries ``file_label`` so the caller knows which file was
    compared.
    """
    old_text = _coerce_text(old_content)
    new_text = _coerce_text(new_content)
    n = _clamp_context(context_lines)

    if old_text == new_text:
        return UnifiedDiffResult(
            diff="",
            stats=DiffStats(added_lines=0, removed_lines=0, hunks=0),
            files_changed=[file_label],
        )

    diff_lines = list(
        difflib.unified_diff(
            _split_keepends(old_text),
            _split_keepends(new_text),
            fromfile=f"{from_label}/{file_label}",
            tofile=f"{to_label}/{file_label}",
            n=n,
            lineterm="",
        )
    )
    added, removed, hunks = _count_added_removed(diff_lines)
    return UnifiedDiffResult(
        diff="".join(diff_lines),
        stats=DiffStats(added_lines=added, removed_lines=removed, hunks=hunks),
        files_changed=[file_label],
    )


def render_multi_file_diff(
    old_files: dict[str, str] | None,
    new_files: dict[str, str] | None,
    *,
    context_lines: int = _DEFAULT_CONTEXT,
    from_label: str = "old",
    to_label: str = "new",
) -> UnifiedDiffResult:
    """Stitched multi-file unified diff. Sorted by path for determinism.

    File semantics:

    * Added (in *new* only): emit a diff against ``/dev/null``.
    * Removed (in *old* only): emit a diff against ``/dev/null``.
    * Both sides + identical content: skipped (not listed in
      ``files_changed``).
    * Both sides + different content: standard diff.

    M2 evolver patch sets typically include SKILL.md plus
    ``scripts/`` / ``references/``; this helper aggregates them all.
    """
    old_map = old_files or {}
    new_map = new_files or {}
    n = _clamp_context(context_lines)
    paths = sorted(set(old_map.keys()) | set(new_map.keys()))

    parts: list[str] = []
    files_changed: list[str] = []
    total_added = 0
    total_removed = 0
    total_hunks = 0

    for path in paths:
        old_text = _coerce_text(old_map.get(path))
        new_text = _coerce_text(new_map.get(path))
        if path in old_map and path in new_map and old_text == new_text:
            continue

        from_path = f"{from_label}/{path}" if path in old_map else "/dev/null"
        to_path = f"{to_label}/{path}" if path in new_map else "/dev/null"
        diff_lines = list(
            difflib.unified_diff(
                _split_keepends(old_text) if path in old_map else [],
                _split_keepends(new_text) if path in new_map else [],
                fromfile=from_path,
                tofile=to_path,
                n=n,
                lineterm="",
            )
        )
        if not diff_lines:
            continue
        files_changed.append(path)
        added, removed, hunks = _count_added_removed(diff_lines)
        total_added += added
        total_removed += removed
        total_hunks += hunks
        parts.append("".join(diff_lines))

    return UnifiedDiffResult(
        diff="\n".join(parts),
        stats=DiffStats(
            added_lines=total_added,
            removed_lines=total_removed,
            hunks=total_hunks,
        ),
        files_changed=files_changed,
    )


def truncate_diff_for_display(
    diff_text: str | None,
    *,
    max_lines: int = 2000,
    max_chars: int = 80_000,
) -> tuple[str, bool]:
    """Cap a diff blob for direct rendering in the browser.

    Returns ``(text, was_truncated)``. Both bounds are checked
    independently — whichever fires first wins. The caller is expected
    to surface a "view full" UI when ``was_truncated`` is true so the
    user can fetch the unbounded payload separately.
    """
    if not diff_text:
        return "", False

    truncated = False
    text = diff_text

    if max_lines > 0:
        lines = text.split("\n")
        if len(lines) > max_lines:
            text = "\n".join(lines[:max_lines])
            truncated = True

    if max_chars > 0 and len(text) > max_chars:
        text = text[:max_chars]
        truncated = True

    if truncated:
        text = text + _TRUNCATION_MARKER
    return text, truncated
