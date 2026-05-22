"""Session-scoped filesystem tools: read / write / list / search / delete.

Each session owns a scratch directory at ``{SCRATCH_BASE}/{session_id}/`` and
every path is resolved relative to that root with traversal protection.

Limits:
  - write: 5 MiB per file
  - read:  returns at most 200k chars of text
  - search_files: matches up to 200 hits, 40 chars of context per hit
"""

from __future__ import annotations

import re
from pathlib import Path

from pydantic import BaseModel, Field, field_validator

from app.agents.tools._context import get_context

MAX_WRITE_BYTES = 5 * 1024 * 1024
MAX_READ_CHARS = 200_000
MAX_SEARCH_HITS = 200


# ─── Path guard ────────────────────────────────────────────
def _root() -> Path:
    ctx = get_context()
    root = (ctx.scratch_base / str(ctx.session_id)).resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def _safe(rel_path: str) -> Path:
    if not rel_path or rel_path.startswith("/") or rel_path.startswith("\\"):
        raise ValueError(f"path must be relative: {rel_path!r}")
    if ".." in Path(rel_path).parts:
        raise ValueError(f"path traversal not allowed: {rel_path!r}")
    root = _root()
    target = (root / rel_path).resolve()
    try:
        target.relative_to(root)
    except ValueError as e:
        raise ValueError(f"path escapes scratch root: {rel_path!r}") from e
    return target


# ─── read_file ─────────────────────────────────────────────
class ReadFileArgs(BaseModel):
    path: str = Field(..., description="Relative path under the session scratch root.")
    encoding: str = Field(
        default="utf-8", description="Text encoding; pass 'binary' for raw bytes summary."
    )
    start_line: int = Field(default=1, ge=1, description="1-based line number to start from.")
    end_line: int | None = Field(default=None, description="Inclusive end line; None = until EOF.")


def run_read_file(args: ReadFileArgs) -> dict:
    p = _safe(args.path)
    if not p.exists():
        return {"ok": False, "error": "not_found", "path": args.path}
    if p.is_dir():
        return {"ok": False, "error": "is_dir", "path": args.path}

    if args.encoding == "binary":
        size = p.stat().st_size
        return {"ok": True, "path": args.path, "binary": True, "bytes": size}

    try:
        text = p.read_text(encoding=args.encoding, errors="replace")
    except Exception as e:
        return {"ok": False, "error": f"read_failed: {e}", "path": args.path}

    lines = text.splitlines()
    end = args.end_line if args.end_line is not None else len(lines)
    slice_lines = lines[args.start_line - 1 : end]
    joined = "\n".join(slice_lines)
    if len(joined) > MAX_READ_CHARS:
        joined = joined[:MAX_READ_CHARS]
        truncated = True
    else:
        truncated = False
    return {
        "ok": True,
        "path": args.path,
        "start_line": args.start_line,
        "end_line": end,
        "total_lines": len(lines),
        "content": joined,
        "truncated": truncated,
    }


# ─── write_file ────────────────────────────────────────────
class WriteFileArgs(BaseModel):
    path: str = Field(..., description="Relative path under the session scratch root.")
    content: str = Field(..., description="File content.")
    encoding: str = Field(default="utf-8")
    append: bool = Field(default=False, description="Append instead of overwrite.")

    @field_validator("content")
    @classmethod
    def _check_size(cls, v: str) -> str:
        if len(v.encode("utf-8", errors="replace")) > MAX_WRITE_BYTES:
            raise ValueError(f"content exceeds {MAX_WRITE_BYTES} bytes")
        return v


def run_write_file(args: WriteFileArgs) -> dict:
    p = _safe(args.path)
    p.parent.mkdir(parents=True, exist_ok=True)
    mode = "a" if args.append else "w"
    try:
        with p.open(mode, encoding=args.encoding) as f:
            f.write(args.content)
    except Exception as e:
        return {"ok": False, "error": f"write_failed: {e}", "path": args.path}
    return {
        "ok": True,
        "path": args.path,
        "bytes_written": len(args.content.encode(args.encoding, errors="replace")),
        "appended": args.append,
    }


# ─── list_files ────────────────────────────────────────────
class ListFilesArgs(BaseModel):
    path: str = Field(default=".", description="Relative directory to list (defaults to root).")
    recursive: bool = Field(default=False)


def run_list_files(args: ListFilesArgs) -> dict:
    p = _safe(args.path or ".")
    if not p.exists():
        return {"ok": False, "error": "not_found", "path": args.path}
    if not p.is_dir():
        return {"ok": False, "error": "not_a_directory", "path": args.path}

    root = _root()
    items: list[dict] = []
    iterator = p.rglob("*") if args.recursive else p.iterdir()
    for entry in iterator:
        try:
            rel = entry.resolve().relative_to(root)
        except ValueError:
            continue
        items.append(
            {
                "path": str(rel).replace("\\", "/"),
                "kind": "dir" if entry.is_dir() else "file",
                "size": entry.stat().st_size if entry.is_file() else None,
            }
        )
        if len(items) >= 1000:
            break
    return {"ok": True, "path": args.path, "items": items}


# ─── search_files ──────────────────────────────────────────
class SearchFilesArgs(BaseModel):
    pattern: str = Field(..., description="Regular expression to search for (Python re syntax).")
    path: str = Field(default=".", description="Relative subdirectory to search under.")
    glob: str = Field(default="**/*", description="Glob filter for files (e.g. '**/*.py').")
    max_hits: int = Field(default=50, ge=1, le=MAX_SEARCH_HITS)


def run_search_files(args: SearchFilesArgs) -> dict:
    try:
        regex = re.compile(args.pattern)
    except re.error as e:
        return {"ok": False, "error": f"bad_regex: {e}"}

    p = _safe(args.path or ".")
    if not p.is_dir():
        return {"ok": False, "error": "not_a_directory", "path": args.path}

    root = _root()
    hits: list[dict] = []
    for file_path in p.glob(args.glob):
        if not file_path.is_file():
            continue
        try:
            text = file_path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        for i, line in enumerate(text.splitlines(), start=1):
            if regex.search(line):
                rel = file_path.resolve().relative_to(root)
                hits.append(
                    {
                        "path": str(rel).replace("\\", "/"),
                        "line": i,
                        "text": line[:200],
                    }
                )
                if len(hits) >= args.max_hits:
                    return {"ok": True, "pattern": args.pattern, "hits": hits, "truncated": True}
    return {"ok": True, "pattern": args.pattern, "hits": hits, "truncated": False}


# ─── delete_file ───────────────────────────────────────────
class DeleteFileArgs(BaseModel):
    path: str = Field(..., description="Relative file path to delete (files only).")


def run_delete_file(args: DeleteFileArgs) -> dict:
    p = _safe(args.path)
    if not p.exists():
        return {"ok": False, "error": "not_found", "path": args.path}
    if p.is_dir():
        return {"ok": False, "error": "is_dir_use_rm_recursive", "path": args.path}
    try:
        p.unlink()
    except Exception as e:
        return {"ok": False, "error": f"delete_failed: {e}", "path": args.path}
    return {"ok": True, "path": args.path}
