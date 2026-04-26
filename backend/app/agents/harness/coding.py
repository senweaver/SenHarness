"""Coding-Agent harness — Repo context injection + planning + verification.

Covers four V2 capability items:

  * Repo context injection — auto-load workspace-level ``AGENTS.md`` / ``README``
    and prepend them to the system prompt for coding sessions.
  * Planning — render a compact ``<plan>...</plan>`` fence in the system
    prompt that asks the model to publish and update its plan.
  * Verification loop — expose a ``verification_hint`` fragment the caller can
    mix into the prompt (edit → run tests → fix) once a test command is wired.
  * Filesystem sandbox — if ``pydantic-ai-filesystem-sandbox`` is installed,
    prefer its hardened capability over our in-house scratch tools.

All pieces degrade gracefully: missing files / missing packages return
``None`` / empty strings so non-coding agents are unaffected.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from app.core.config import settings

log = logging.getLogger(__name__)


# ─── Repo context injection ────────────────────────────────────
# Default filenames we scan (in priority order). Operators can override via
# ``metadata.coding.repo_context_files = ["AGENTS.md", "docs/ARCH.md"]``.
DEFAULT_REPO_CONTEXT_FILES: tuple[str, ...] = (
    "AGENTS.md",
    "CLAUDE.md",
    "README.md",
    ".cursor/rules/overview.md",
)

# Total character budget for all repo context combined. Anything above this
# is elided with a short note so we don't blow the model's context window.
REPO_CONTEXT_MAX_CHARS = 6000


def _resolve_repo_root(policy: dict[str, Any] | None) -> Path | None:
    """Pick the repo root: explicit ``coding.repo_root`` wins; otherwise the
    workspace scratch base for the session, then the backend working dir.
    """
    block = (policy or {}).get("coding") or {}
    configured = block.get("repo_root")
    if configured:
        p = Path(str(configured)).expanduser()
        return p if p.is_dir() else None

    # Fall back to the workspace-scoped knowledge dir, if any.
    ws_id = (policy or {}).get("workspace_id")
    if ws_id:
        ws_dir = Path(settings.STORAGE_LOCAL_PATH) / "workspace_repos" / str(ws_id)
        if ws_dir.is_dir():
            return ws_dir
    return None


def load_repo_context(policy: dict[str, Any] | None) -> str | None:
    """Return a markdown blob with the workspace's repo context, or ``None``.

    The blob is capped at ``REPO_CONTEXT_MAX_CHARS`` total; when over budget
    we keep the files in priority order and elide the rest.
    """
    block = (policy or {}).get("coding") or {}
    if not block.get("repo_context", True):
        return None

    root = _resolve_repo_root(policy)
    if root is None:
        return None

    filenames: tuple[str, ...] = tuple(
        block.get("repo_context_files") or DEFAULT_REPO_CONTEXT_FILES
    )

    chunks: list[str] = []
    remaining = REPO_CONTEXT_MAX_CHARS
    for name in filenames:
        candidate = (root / name).resolve()
        try:
            candidate.relative_to(root.resolve())
        except ValueError:
            continue
        if not candidate.is_file():
            continue
        try:
            text = candidate.read_text(encoding="utf-8", errors="ignore")
        except Exception:  # pragma: no cover
            continue
        text = text.strip()
        if not text:
            continue
        if len(text) > remaining:
            text = (
                text[:remaining].rstrip()
                + f"\n\n… (truncated; {len(text) - remaining} chars elided)"
            )
        chunks.append(f"## `{name}`\n\n{text}")
        remaining -= len(text)
        if remaining <= 256:
            break

    if not chunks:
        return None

    header = (
        "# Workspace repo context\n"
        "The files below describe conventions, architecture, and rules for this "
        "workspace's codebase. Treat them as high-priority ground truth."
    )
    return header + "\n\n" + "\n\n---\n\n".join(chunks)


# ─── Planning prompt fragment ──────────────────────────────────
_PLANNING_FRAGMENT = (
    "# Planning protocol\n"
    "For non-trivial tasks, publish a short plan before acting:\n"
    "  1. Write a numbered plan inside a `<plan>…</plan>` block (3-7 steps).\n"
    "  2. Execute one step at a time.\n"
    "  3. Update the plan when assumptions change — emit a new `<plan>` block.\n"
    "Keep the plan tight; delete completed items."
)


def planning_prompt_fragment(policy: dict[str, Any] | None) -> str | None:
    block = (policy or {}).get("coding") or {}
    if not block.get("planning", True):
        return None
    return _PLANNING_FRAGMENT


# ─── Verification loop fragment ────────────────────────────────
def verification_prompt_fragment(policy: dict[str, Any] | None) -> str | None:
    """Tell the model to verify edits via tests when a test command is wired.

    The actual test command is configured per-agent via
    ``metadata.coding.test_command`` (e.g. ``pytest -x``). The tool runner
    for ``run_tests`` (if present in the toolbox) consumes it.
    """
    block = (policy or {}).get("coding") or {}
    if not block.get("verification", True):
        return None
    cmd = block.get("test_command")
    if not cmd:
        return None
    return (
        "# Verification loop\n"
        f"After editing code, verify via the `run_tests` tool (runs `{cmd}`). "
        "If tests fail, inspect the output, fix, and re-run. Do not end the "
        "turn with failing tests unless the user explicitly asked to stop."
    )


# ─── Filesystem sandbox preference ─────────────────────────────
def prefer_filesystem_sandbox_capability() -> Any | None:
    """Return a ``FilesystemSandboxCapability`` instance when the external
    ``pydantic-ai-filesystem-sandbox`` package is installed; otherwise ``None``
    so the caller falls back to the existing scratch-scoped tools.
    """
    try:
        import pydantic_ai_filesystem_sandbox as pafs  # type: ignore
    except ImportError:
        return None
    try:
        cap = getattr(pafs, "FilesystemSandboxCapability", None)
        if cap is None:
            return None
        return cap()
    except Exception as e:  # pragma: no cover
        log.warning("pydantic-ai-filesystem-sandbox init failed: %s", e)
        return None


# ─── Combined coding-prompt assembler ──────────────────────────
def build_coding_prompt_fragment(policy: dict[str, Any] | None) -> str | None:
    """Produce the aggregated coding-specific prompt fragment or ``None``."""
    parts: list[str] = []
    repo = load_repo_context(policy)
    if repo:
        parts.append(repo)
    plan = planning_prompt_fragment(policy)
    if plan:
        parts.append(plan)
    verify = verification_prompt_fragment(policy)
    if verify:
        parts.append(verify)
    if not parts:
        return None
    return "\n\n".join(parts)
