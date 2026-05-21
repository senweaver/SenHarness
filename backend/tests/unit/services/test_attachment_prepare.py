"""Pure-helper tests for ``services.attachment.prepare_for_chat_turn``.

The full helper needs a DB session (for ``get_for_read`` / ``flush``); we
exercise it through the service-level integration tests. The helpers
below are pure functions and worth a fast unit covering name-collision
allocation + prompt-prefix shape.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.services.attachment import (
    PreparedAttachment,
    _allocate_scratch_path,
    _build_prompt_prefix,
    _safe_scratch_filename,
)


class TestSafeScratchFilename:
    def test_strips_path_separators(self):
        assert _safe_scratch_filename("../etc/passwd") == ".._etc_passwd"
        assert _safe_scratch_filename("foo/bar.txt") == "foo_bar.txt"
        assert _safe_scratch_filename("foo\\bar.txt") == "foo_bar.txt"

    def test_caps_length(self):
        long = "a" * 500 + ".txt"
        out = _safe_scratch_filename(long)
        assert len(out) <= 120
        assert out.startswith("a")

    def test_empty_falls_back(self):
        assert _safe_scratch_filename("") == "attachment"
        assert _safe_scratch_filename(None) == "attachment"  # type: ignore[arg-type]


class TestAllocateScratchPath:
    def test_uses_filename_when_free(self, tmp_path: Path):
        out = _allocate_scratch_path(tmp_path, "report.pdf")
        assert out.name == "report.pdf"
        assert out.parent == tmp_path

    def test_collision_appends_counter(self, tmp_path: Path):
        (tmp_path / "report.pdf").write_bytes(b"x")
        out = _allocate_scratch_path(tmp_path, "report.pdf")
        assert out.name == "report (2).pdf"

    def test_multiple_collisions(self, tmp_path: Path):
        (tmp_path / "report.pdf").write_bytes(b"x")
        (tmp_path / "report (2).pdf").write_bytes(b"x")
        (tmp_path / "report (3).pdf").write_bytes(b"x")
        out = _allocate_scratch_path(tmp_path, "report.pdf")
        assert out.name == "report (4).pdf"

    def test_extensionless(self, tmp_path: Path):
        (tmp_path / "README").write_bytes(b"x")
        out = _allocate_scratch_path(tmp_path, "README")
        assert out.name == "README (2)"


class TestPromptPrefix:
    def test_empty_when_nothing_useful(self):
        assert _build_prompt_prefix([]) == ""
        assert (
            _build_prompt_prefix(
                [PreparedAttachment(ref={"filename": "x.png", "kind": "image"})]
            )
            == ""
        )

    def test_lists_scratch_files(self):
        prefix = _build_prompt_prefix(
            [
                PreparedAttachment(
                    ref={"filename": "a.pdf", "size_bytes": 100, "kind": "document"},
                    scratch_relpath="a.pdf",
                    text_relpath="a.pdf",
                ),
                PreparedAttachment(
                    ref={"filename": "b.txt", "size_bytes": 50, "kind": "document"},
                    scratch_relpath="b.txt",
                    text_relpath="b.txt",
                ),
            ]
        )
        assert "[Attached files]" in prefix
        assert "a.pdf (100 bytes)" in prefix
        assert "b.txt (50 bytes)" in prefix
        assert "session scratch" in prefix

    def test_binary_with_text_sidecar(self):
        """DOCX/PDF with extracted .txt sidecar → prefix points at the .txt."""
        prefix = _build_prompt_prefix(
            [
                PreparedAttachment(
                    ref={
                        "filename": "report.docx",
                        "size_bytes": 20_000,
                        "kind": "document",
                    },
                    scratch_relpath="report.docx",
                    text_relpath="report.txt",
                )
            ]
        )
        assert "report.docx" in prefix
        assert "binary; use `read_file('report.txt')`" in prefix
        assert "extracted plain text" in prefix

    def test_includes_excerpt_block(self):
        prefix = _build_prompt_prefix(
            [
                PreparedAttachment(
                    ref={"filename": "note.md", "size_bytes": 12, "kind": "document"},
                    scratch_relpath="note.md",
                    text_relpath="note.md",
                    inline_excerpt="Hello world\nLine two",
                    inline_truncated=False,
                )
            ]
        )
        assert "BEGIN excerpt: note.md ---" in prefix
        assert "Hello world" in prefix
        assert "END excerpt: note.md ---" in prefix
        assert "truncated" not in prefix

    def test_truncated_marker(self):
        prefix = _build_prompt_prefix(
            [
                PreparedAttachment(
                    ref={"filename": "long.txt", "size_bytes": 9_999, "kind": "document"},
                    scratch_relpath="long.txt",
                    text_relpath="long.txt",
                    inline_excerpt="abc",
                    inline_truncated=True,
                )
            ]
        )
        assert "truncated" in prefix
        assert "use `read_file` for more" in prefix


@pytest.mark.parametrize(
    "given,expected",
    [
        ("foo", "foo"),
        ("foo.txt", "foo.txt"),
        ("foo bar.csv", "foo bar.csv"),
    ],
)
def test_safe_filename_passthrough(given: str, expected: str):
    assert _safe_scratch_filename(given) == expected
