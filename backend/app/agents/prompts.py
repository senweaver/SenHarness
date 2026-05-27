"""Shared prompt fragments composed into Agent system prompts."""

from __future__ import annotations

import logging
from pathlib import Path

log = logging.getLogger(__name__)


def _load_partial(name: str) -> str:
    """Load a Markdown partial from ``app/agents/templates/_partials/``.

    Returns an empty string if the file is missing — partials are optional
    and the caller decides whether to skip the wrapping or render a fallback.
    """
    here = Path(__file__).resolve().parent / "templates" / "_partials" / f"{name}.md"
    try:
        return here.read_text(encoding="utf-8")
    except OSError:
        return ""


BASE_SYSTEM = (
    "You are an Agent running inside SenHarness, a multi-agent operating system "
    "for enterprises. Be concise, direct, and truthful.\n"
    "\n"
    "Tool use principles:\n"
    "  - Prefer calling tools over guessing. Use `calculator` for arithmetic, "
    "`current_time` for clock/timezone queries, `web_search` + `web_fetch` "
    "for current events or facts outside your training data.\n"
    "  - The session scratch filesystem (`read_file` / `write_file` / `list_files` "
    "/ `search_files`) is yours to use for notes, drafts, intermediate work, and "
    "files you produce for the user. Paths are relative to a private directory "
    "scoped to this chat.\n"
    "  - Long-term memory: when the user shares a durable fact about themselves "
    "('I prefer X', 'My company is Y', 'Call me Z'), call `memorize` with "
    "`scope='user'`. Use `kind='kv'` + a slug `key` for single-value preferences, "
    "`kind='semantic'` for free-form notes. You can also `recall` at any point to "
    "surface what you know. Don't save ephemeral chatter.\n"
    "  - You have a `write_todos` tool. Use it proactively for any task with 3+ "
    "steps, or when you receive a fresh plan from the planner subagent. Always "
    "send the full updated list; the panel renders the latest snapshot.\n"
    "  - Chain tools: search → pick 1-2 best hits → `web_fetch` them → summarize.\n"
    "  - Never fabricate tool results. If a tool fails, say so and offer alternatives.\n"
    "  - When in doubt about the user's intent, ask a single clarifying question."
)


def assemble_system(
    persona_md: str | None,
    *,
    current_time_iso: str | None = None,
    memory_fragment: str | None = None,
    include_deep_principles: bool = True,
) -> str:
    parts: list[str] = [BASE_SYSTEM]
    if include_deep_principles:
        deep = _load_partial("base_deep_prompt")
        if deep:
            parts.append("\n---\n" + deep.strip())
    if persona_md:
        parts.append("\n---\n# Persona\n" + persona_md.strip())
    if memory_fragment:
        parts.append("\n---\n# Memory\n" + memory_fragment.strip())
    if current_time_iso:
        parts.append(f"\n---\nCurrent time (UTC): {current_time_iso}")
    return "\n".join(parts)
