"""Builtin tool registry.

Each entry in `BUILTIN_TOOL_REGISTRY` maps `name -> BuiltinTool` and can be
attached to a pydantic-ai `Agent` via `kernels.native.runner._build_agent`.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel


@dataclass(slots=True)
class BuiltinTool:
    name: str
    description: str
    args_model: type[BaseModel]
    runner: Callable[[Any], dict | Awaitable[dict]]


# ─── Import tool implementations ─────────────────────────────
from app.agents.tools.calculator import CalculatorArgs, run_calculator  # noqa: E402
from app.agents.tools.current_time import CurrentTimeArgs, run_current_time  # noqa: E402
from app.agents.tools.echo import EchoArgs, run_echo  # noqa: E402
from app.agents.tools.filesystem import (  # noqa: E402
    DeleteFileArgs,
    ListFilesArgs,
    ReadFileArgs,
    SearchFilesArgs,
    WriteFileArgs,
    run_delete_file,
    run_list_files,
    run_read_file,
    run_search_files,
    run_write_file,
)
from app.agents.tools.knowledge import KnowledgeSearchArgs, run_knowledge_search  # noqa: E402
from app.agents.tools.memory import (  # noqa: E402
    ForgetArgs,
    ListMemoriesArgs,
    MemorizeArgs,
    RecallArgs,
    run_forget,
    run_list_memories,
    run_memorize,
    run_recall,
)
from app.agents.tools.multimedia import (  # noqa: E402
    GenerateImageArgs,
    SpeakArgs,
    TranscribeArgs,
    run_generate_image,
    run_speak,
    run_transcribe,
)
from app.agents.tools.run_tests import RunTestsArgs, run_run_tests  # noqa: E402
from app.agents.tools.session_search import SessionSearchArgs, run_session_search  # noqa: E402
from app.agents.tools.toolbox_search import FindToolsArgs, run_find_tools  # noqa: E402
from app.agents.tools.web_fetch import WebFetchArgs, run_web_fetch  # noqa: E402
from app.agents.tools.web_search import WebSearchArgs, run_web_search  # noqa: E402

BUILTIN_TOOL_REGISTRY: dict[str, BuiltinTool] = {
    # ── Lightweight utilities ─────────────────────────────
    "echo": BuiltinTool(
        name="echo",
        description="Echo the input text verbatim. For smoke tests and debugging.",
        args_model=EchoArgs,
        runner=run_echo,
    ),
    "current_time": BuiltinTool(
        name="current_time",
        description=(
            "Return the current wall-clock time in a given IANA timezone. "
            "ALWAYS pass the `timezone` parameter using an IANA name when the user asks "
            "about a specific city/region (examples: 'Asia/Shanghai', 'Asia/Tokyo', "
            "'America/Los_Angeles', 'Europe/London'). Only omit `timezone` when the user "
            "explicitly asks for UTC."
        ),
        args_model=CurrentTimeArgs,
        runner=run_current_time,
    ),
    "calculator": BuiltinTool(
        name="calculator",
        description="Evaluate a basic arithmetic expression (no variables, no function calls).",
        args_model=CalculatorArgs,
        runner=run_calculator,
    ),

    # ── Web ───────────────────────────────────────────────
    "web_search": BuiltinTool(
        name="web_search",
        description=(
            "Search the web. Returns a list of {title, url, snippet, source} hits. "
            "Use this when the user asks about current events, specific facts, or "
            "information outside your training cutoff. Pass a focused `query` and a "
            "reasonable `max_results` (default 5). Combine with `web_fetch` to read "
            "the most promising URLs in full."
        ),
        args_model=WebSearchArgs,
        runner=run_web_search,
    ),
    "web_fetch": BuiltinTool(
        name="web_fetch",
        description=(
            "Fetch an http(s) URL and return its main content as clean markdown. "
            "Strips navigation/footers/ads. Use AFTER `web_search` on the 1-2 most "
            "relevant hits. Output is capped at 40,000 chars; pages larger than 2 MiB "
            "are truncated."
        ),
        args_model=WebFetchArgs,
        runner=run_web_fetch,
    ),

    # ── Filesystem (session-scoped scratch) ───────────────
    "read_file": BuiltinTool(
        name="read_file",
        description=(
            "Read a file under the session scratch directory. Use relative paths "
            "(e.g. 'notes.md' or 'data/report.csv'). Supports line-range reads via "
            "`start_line` and `end_line`. Use this to review earlier work in the "
            "current session."
        ),
        args_model=ReadFileArgs,
        runner=run_read_file,
    ),
    "write_file": BuiltinTool(
        name="write_file",
        description=(
            "Write text content to a file under the session scratch directory. Use "
            "relative paths. Set `append=true` to append rather than overwrite. "
            "Parent directories are created automatically. Single-file limit: 5 MiB."
        ),
        args_model=WriteFileArgs,
        runner=run_write_file,
    ),
    "list_files": BuiltinTool(
        name="list_files",
        description=(
            "List entries under the session scratch directory. Pass `recursive=true` "
            "to walk all subdirectories. Useful to remind yourself what files exist "
            "before reading them."
        ),
        args_model=ListFilesArgs,
        runner=run_list_files,
    ),
    "search_files": BuiltinTool(
        name="search_files",
        description=(
            "Search file contents for a regex. Use when you need to locate a snippet "
            "across many files. Limit with `path` (subdir) and `glob` (file pattern)."
        ),
        args_model=SearchFilesArgs,
        runner=run_search_files,
    ),
    "delete_file": BuiltinTool(
        name="delete_file",
        description="Delete a single file under the session scratch directory.",
        args_model=DeleteFileArgs,
        runner=run_delete_file,
    ),

    # ── Long-term memory ─────────────────────────────────
    "memorize": BuiltinTool(
        name="memorize",
        description=(
            "Persist a fact / preference / note for later recall. Use `scope='user'` for "
            "things about the HUMAN you're helping, `scope='assistant'` for your own "
            "ongoing notes, and `scope='workspace'` for team-wide facts. Prefer `kind='kv'` "
            "with a slug `key` (e.g. 'preferred_editor') for single-value facts; use "
            "`kind='semantic'` for free-form notes that should be recallable by vibe."
        ),
        args_model=MemorizeArgs,
        runner=run_memorize,
    ),
    "recall": BuiltinTool(
        name="recall",
        description=(
            "Semantically recall memories relevant to a query. Use this near the start of "
            "a conversation to surface what you already know about the user / task / topic."
        ),
        args_model=RecallArgs,
        runner=run_recall,
    ),
    "list_memories": BuiltinTool(
        name="list_memories",
        description="List stored memories for a given scope. Useful for audit/debug/review.",
        args_model=ListMemoriesArgs,
        runner=run_list_memories,
    ),
    "forget": BuiltinTool(
        name="forget",
        description="Delete a memory by id (soft delete). Confirm with the user first for user-scope memories.",
        args_model=ForgetArgs,
        runner=run_forget,
    ),
    "session_search": BuiltinTool(
        name="session_search",
        description=(
            "Full-text search over past messages in this workspace (L2 episodic "
            "memory). Use this when the user references something they said "
            "before, or when you need to reconstruct what happened in a prior "
            "session. Prefer this over `recall` for 'what did we discuss last "
            "week?'-style queries. Returns {session_id, message_id, role, "
            "created_at, body, score, session_title}."
        ),
        args_model=SessionSearchArgs,
        runner=run_session_search,
    ),

    # ── Knowledge (RAG) ──────────────────────────────────
    "knowledge_search": BuiltinTool(
        name="knowledge_search",
        description=(
            "Semantic search over a knowledge collection in this workspace. Pass "
            "the collection NAME or UUID as `collection`. Returns up to `top_k` "
            "passages with their doc title and similarity score (0-1, higher is "
            "better). Quote or paraphrase with citations when answering the user."
        ),
        args_model=KnowledgeSearchArgs,
        runner=run_knowledge_search,
    ),

    # ── Multimedia (images / speech) ──────────────────────
    "generate_image": BuiltinTool(
        name="generate_image",
        description=(
            "Generate an image from a text prompt via OpenAI-compatible images API. "
            "Requires an enabled OpenAI-compatible model provider in the workspace. "
            "Result is saved as a session attachment; return the attachment_id so the UI renders it."
        ),
        args_model=GenerateImageArgs,
        runner=run_generate_image,
    ),
    "speak": BuiltinTool(
        name="speak",
        description=(
            "Synthesize speech from text (TTS). Output is stored as a session "
            "audio attachment. Use sparingly — audio uploads count against quota."
        ),
        args_model=SpeakArgs,
        runner=run_speak,
    ),
    "transcribe": BuiltinTool(
        name="transcribe",
        description=(
            "Transcribe an existing audio attachment to text (STT). Pass the "
            "attachment_id of an audio file already uploaded in the session. "
            "Returns {text, language}."
        ),
        args_model=TranscribeArgs,
        runner=run_transcribe,
    ),

    # ── Coding agent support ─────────────────────────────
    "find_tools": BuiltinTool(
        name="find_tools",
        description=(
            "Search the tool registry for tools relevant to a task. Use this "
            "FIRST when you're unsure which tool fits — cheaper than "
            "guessing and retrying. Returns {matches: [{name, score, "
            "description}]}."
        ),
        args_model=FindToolsArgs,
        runner=run_find_tools,
    ),
    "run_tests": BuiltinTool(
        name="run_tests",
        description=(
            "Run the workspace test command (configured via "
            "`metadata.coding.test_command`) inside the agent sandbox. Use "
            "after editing code to verify the change. Returns "
            "{ok, exit_code, stdout, stderr}."
        ),
        args_model=RunTestsArgs,
        runner=run_run_tests,
    ),
}


# ─── Opt-in coding toolbox (used by coding-agent templates) ───
CODING_TOOLBOX = [
    "read_file",
    "write_file",
    "list_files",
    "search_files",
    "delete_file",
    "find_tools",
    "run_tests",
    "web_search",
    "web_fetch",
    "knowledge_search",
    "memorize",
    "recall",
    "session_search",
    "current_time",
    "calculator",
]


# ─── Default toolbox per agent ────────────────────────────
DEFAULT_TOOLBOX = [
    "calculator",
    "current_time",
    "web_search",
    "web_fetch",
    "read_file",
    "write_file",
    "list_files",
    "search_files",
    "memorize",
    "recall",
    "list_memories",
    "session_search",
    "knowledge_search",
]


__all__ = [
    "BUILTIN_TOOL_REGISTRY",
    "CODING_TOOLBOX",
    "DEFAULT_TOOLBOX",
    "BuiltinTool",
]
