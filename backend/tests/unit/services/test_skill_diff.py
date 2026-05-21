"""Pure-function unit tests for ``app.services.skill_diff`` (M1.10).

The helpers underpin both the M1.10 REST surface and M2's evolver
approval UI, so the contract has to be airtight: zero side effects,
deterministic stats, graceful handling of None / oversize input,
truncation that never silently drops the marker.
"""

from __future__ import annotations

from app.services.skill_diff import (
    DiffStats,
    UnifiedDiffResult,
    render_multi_file_diff,
    render_unified_diff,
    truncate_diff_for_display,
)


# ─── render_unified_diff ─────────────────────────────────────
def test_identical_inputs_yield_empty_diff_and_zero_stats() -> None:
    out = render_unified_diff("hello\nworld\n", "hello\nworld\n")
    assert isinstance(out, UnifiedDiffResult)
    assert out.diff == ""
    assert out.stats == DiffStats(added_lines=0, removed_lines=0, hunks=0)
    assert out.files_changed == ["SKILL.md"]


def test_simple_one_line_change() -> None:
    out = render_unified_diff(
        old_content="alpha\nbeta\ngamma\n",
        new_content="alpha\nBETA\ngamma\n",
    )
    assert out.stats.added_lines == 1
    assert out.stats.removed_lines == 1
    assert out.stats.hunks == 1
    assert "-beta" in out.diff
    assert "+BETA" in out.diff
    assert "old/SKILL.md" in out.diff
    assert "new/SKILL.md" in out.diff


def test_multiple_hunks_counted_independently() -> None:
    old = "\n".join(["a", "b", "c", "x", "y", "z", "p", "q", "r", "s"]) + "\n"
    new = "\n".join(["a", "B", "c", "x", "y", "z", "p", "Q", "r", "s"]) + "\n"
    out = render_unified_diff(old, new, context_lines=1)
    assert out.stats.hunks == 2
    assert out.stats.added_lines == 2
    assert out.stats.removed_lines == 2


def test_context_lines_clamped_to_legal_range() -> None:
    base = "\n".join(str(i) for i in range(20)) + "\n"
    target = base.replace("10", "TEN")
    zero = render_unified_diff(base, target, context_lines=0)
    three = render_unified_diff(base, target, context_lines=3)
    huge = render_unified_diff(base, target, context_lines=999)
    assert zero.diff != "" and three.diff != "" and huge.diff != ""
    # zero context = tightest output; clamped huge value still produces
    # a valid diff (no exception).
    assert len(zero.diff) <= len(three.diff) <= len(huge.diff)


def test_none_inputs_treated_as_empty() -> None:
    out = render_unified_diff(None, "hello\n")
    assert out.stats.added_lines == 1
    assert out.stats.removed_lines == 0
    assert "+hello" in out.diff


def test_empty_to_empty_yields_no_diff() -> None:
    out = render_unified_diff("", "")
    assert out.diff == ""
    assert out.stats.added_lines == 0


def test_custom_labels_propagate_to_diff_header() -> None:
    out = render_unified_diff(
        "x\n",
        "y\n",
        file_label="scripts/run.sh",
        from_label="v1",
        to_label="v2",
    )
    assert "v1/scripts/run.sh" in out.diff
    assert "v2/scripts/run.sh" in out.diff
    assert out.files_changed == ["scripts/run.sh"]


def test_added_line_without_trailing_newline_still_counted() -> None:
    out = render_unified_diff("a\nb", "a\nb\nc")
    assert out.stats.added_lines >= 1


# ─── render_multi_file_diff ──────────────────────────────────
def test_multifile_diff_added_file() -> None:
    out = render_multi_file_diff(
        old_files={"SKILL.md": "hi\n"},
        new_files={"SKILL.md": "hi\n", "scripts/run.sh": "echo ok\n"},
    )
    assert out.files_changed == ["scripts/run.sh"]
    assert "/dev/null" in out.diff
    assert "+echo ok" in out.diff
    assert out.stats.added_lines == 1


def test_multifile_diff_removed_file() -> None:
    out = render_multi_file_diff(
        old_files={"SKILL.md": "hi\n", "scripts/old.sh": "echo gone\n"},
        new_files={"SKILL.md": "hi\n"},
    )
    assert out.files_changed == ["scripts/old.sh"]
    assert "/dev/null" in out.diff
    assert "-echo gone" in out.diff
    assert out.stats.removed_lines == 1


def test_multifile_diff_modified_file() -> None:
    out = render_multi_file_diff(
        old_files={"SKILL.md": "v1\n"},
        new_files={"SKILL.md": "v2\n"},
    )
    assert out.files_changed == ["SKILL.md"]
    assert out.stats.added_lines == 1
    assert out.stats.removed_lines == 1


def test_multifile_skips_unchanged_files() -> None:
    out = render_multi_file_diff(
        old_files={"a": "same\n", "b": "old\n"},
        new_files={"a": "same\n", "b": "new\n"},
    )
    assert out.files_changed == ["b"]


def test_multifile_empty_dicts_yield_empty_result() -> None:
    out = render_multi_file_diff(None, None)
    assert out.diff == ""
    assert out.files_changed == []
    assert out.stats == DiffStats(0, 0, 0)


def test_multifile_paths_sorted_for_determinism() -> None:
    out = render_multi_file_diff(
        old_files={},
        new_files={"z.md": "z\n", "a.md": "a\n", "m.md": "m\n"},
    )
    assert out.files_changed == ["a.md", "m.md", "z.md"]


# ─── truncate_diff_for_display ───────────────────────────────
def test_truncate_below_caps_is_pass_through() -> None:
    text = "small\ndiff\nbody"
    out, was = truncate_diff_for_display(text, max_lines=100, max_chars=1000)
    assert out == text
    assert was is False


def test_truncate_line_cap_triggers_marker() -> None:
    text = "\n".join(f"line {i}" for i in range(500))
    out, was = truncate_diff_for_display(text, max_lines=100, max_chars=10**9)
    assert was is True
    assert out.count("\n") <= 200
    assert "diff truncated" in out


def test_truncate_char_cap_triggers_marker() -> None:
    text = "x" * 10_000
    out, was = truncate_diff_for_display(text, max_lines=10**9, max_chars=1_000)
    assert was is True
    assert "diff truncated" in out
    assert len(out) < len(text)


def test_truncate_empty_returns_empty() -> None:
    out, was = truncate_diff_for_display("")
    assert out == ""
    assert was is False
    out2, was2 = truncate_diff_for_display(None)
    assert out2 == ""
    assert was2 is False


def test_truncate_default_caps_match_documented_values() -> None:
    blob = ("a" * 79) + "\n"
    text = blob * 1500
    out, was = truncate_diff_for_display(text)
    assert was is True
    assert "diff truncated" in out


def test_truncate_does_not_corrupt_short_blob_with_low_cap() -> None:
    out, was = truncate_diff_for_display("hi", max_lines=1, max_chars=1)
    assert was is True
    assert out.startswith("h")
