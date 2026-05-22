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
    # ``available_for_kinds`` restricts which agent kinds (set on
    # ``RunRequest.policy["agent_kind"]``) may register the tool. The
    # default ``None`` means "available to every kind" (current
    # behaviour for all M0/M1 tools). A non-empty tuple gates the
    # registration: M2.7 evolver verbs ship with
    # ``("evolver",)`` so only the M2.2 builtin evolver agent can call
    # them — every other agent skips registration so the tool is not
    # even visible in its tool catalogue.
    available_for_kinds: tuple[str, ...] | None = None


# ─── Import tool implementations ─────────────────────────────
from app.agents.tools.calculator import CalculatorArgs, run_calculator
from app.agents.tools.cronjob_propose import (
    ProposeCronjobArgs,
    run_propose_cronjob,
)
from app.agents.tools.current_time import CurrentTimeArgs, run_current_time
from app.agents.tools.delegate_batch import (
    DelegateBatchArgs,
    run_delegate_batch,
)
from app.agents.tools.echo import EchoArgs, run_echo
from app.agents.tools.evolver_helpers import (
    ListSessionArtifactsArgs,
    MarkSkipArgs,
    ReadSkillPackArgs,
    run_list_session_artifacts,
    run_mark_skip,
    run_read_skill_pack,
)
from app.agents.tools.filesystem import (
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
from app.agents.tools.knowledge import KnowledgeSearchArgs, run_knowledge_search
from app.agents.tools.memory import (
    ForgetArgs,
    ListMemoriesArgs,
    MemorizeArgs,
    RecallArgs,
    run_forget,
    run_list_memories,
    run_memorize,
    run_recall,
)
from app.agents.tools.multimedia import (
    GenerateImageArgs,
    SpeakArgs,
    TranscribeArgs,
    run_generate_image,
    run_speak,
    run_transcribe,
)
from app.agents.tools.run_tests import RunTestsArgs, run_run_tests
from app.agents.tools.session_search import SessionSearchArgs, run_session_search
from app.agents.tools.shell import ShellArgs, run_shell
from app.agents.tools.skill_propose import (
    ProposeSkillCreateArgs,
    ProposeSkillDeleteArgs,
    ProposeSkillEditArgs,
    ProposeSkillPatchArgs,
    ProposeSkillRemoveFileArgs,
    ProposeSkillWriteFileArgs,
    run_propose_skill_create,
    run_propose_skill_delete,
    run_propose_skill_edit,
    run_propose_skill_patch,
    run_propose_skill_remove_file,
    run_propose_skill_write_file,
)
from app.agents.tools.toolbox_search import FindToolsArgs, run_find_tools
from app.agents.tools.web_fetch import WebFetchArgs, run_web_fetch
from app.agents.tools.web_search import WebSearchArgs, run_web_search

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
    # ── Sub-agent batch spawn (M2.5.6) ────────────────────
    "delegate_batch": BuiltinTool(
        name="delegate_batch",
        description=(
            "Spawn N parallel sub-agents with independent prompts and "
            "aggregate their results. Each task carries `task_id` "
            "(caller-defined identifier), `prompt`, and `target_agent_id` "
            "(an agent in this workspace). Returns "
            "`{parent_run_id, total, completed, failed, timed_out, "
            "halluc_review, rejected, results: {task_id: {status, "
            "output, error_kind, ...}}}`. Use this when you need "
            "multiple independent investigations or verifications "
            "running in parallel — one child failing does not block "
            "the others. Hard limits: <= 20 tasks per call; "
            "max_concurrent defaults to the workspace policy (5)."
        ),
        args_model=DelegateBatchArgs,
        runner=run_delegate_batch,
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
    # ── Dangerous: arbitrary shell ────────────────────────
    # Default-OFF. Not in CODING_TOOLBOX or DEFAULT_TOOLBOX. An agent
    # must opt in via metadata.tools.builtin and *also* set
    # sandbox.kind=docker — the runner refuses any other sandbox kind
    # because shell on the local backend would equal shell on the
    # SenHarness host. Each invocation goes through the HITL approval
    # queue regardless.
    "shell": BuiltinTool(
        name="shell",
        description=(
            "Run a shell command inside the agent's Docker sandbox. ONLY "
            "available when sandbox.kind=docker. Each call goes through "
            "the HITL approval queue. Pass `cwd` to chdir before "
            "executing. Returns {ok, exit_code, command, cwd, stdout, "
            "stderr, truncated}."
        ),
        args_model=ShellArgs,
        runner=run_shell,
    ),
    # ── Evolver-only proposal verbs (M2.1 + M2.7) ─────────────
    # Restricted via ``available_for_kinds=("evolver",)``: every other
    # agent skips registration. Each verb files an Approval row + (for
    # create/patch/edit) a candidate SkillPackVersion row; the M2.5
    # dispatch handler is what eventually applies the change after an
    # admin approves. Disabled workspaces or a tripped breaker reject
    # before any DB write.
    "propose_skill_create": BuiltinTool(
        name="propose_skill_create",
        description=(
            "Propose a brand-new SkillPack. Creates a DRAFT pack + a "
            "PROPOSED v1 version and files an admin Approval; nothing "
            "is enabled until an admin approves. Pass slug (lowercase "
            "hyphens), full SKILL.md content_md, optional supporting "
            "run ids, and a rationale the admin will read."
        ),
        args_model=ProposeSkillCreateArgs,
        runner=run_propose_skill_create,
        available_for_kinds=("evolver",),
    ),
    "propose_skill_patch": BuiltinTool(
        name="propose_skill_patch",
        description=(
            "Propose a precise edit to an existing SkillPack. "
            "old_text must appear verbatim in the current ACTIVE "
            "version (else returns evolver.patch_conflict so you can "
            "re-read and re-propose). On success files a PROPOSED "
            "version + admin Approval; nothing live changes until "
            "approval."
        ),
        args_model=ProposeSkillPatchArgs,
        runner=run_propose_skill_patch,
        available_for_kinds=("evolver",),
    ),
    "propose_skill_edit": BuiltinTool(
        name="propose_skill_edit",
        description=(
            "Propose a full-document replacement of the SKILL.md body. "
            "Prefer propose_skill_patch when you can express the "
            "change as a small old_text → new_text replacement; this "
            "verb is for structural rewrites where a patch is "
            "infeasible."
        ),
        args_model=ProposeSkillEditArgs,
        runner=run_propose_skill_edit,
        available_for_kinds=("evolver",),
    ),
    "propose_skill_delete": BuiltinTool(
        name="propose_skill_delete",
        description=(
            "Propose deleting a SkillPack (transitions to ARCHIVED on "
            "approval). Pinned packs are rejected — the user must "
            "explicitly unpin before the evolver can propose deletion."
        ),
        args_model=ProposeSkillDeleteArgs,
        runner=run_propose_skill_delete,
        available_for_kinds=("evolver",),
    ),
    "propose_skill_write_file": BuiltinTool(
        name="propose_skill_write_file",
        description=(
            "Propose adding (or replacing) a supplementary file inside "
            "an existing pack folder (e.g. 'scripts/run.sh', "
            "'references/api.md'). Cannot target SKILL.md; use "
            "propose_skill_patch / propose_skill_edit for the body."
        ),
        args_model=ProposeSkillWriteFileArgs,
        runner=run_propose_skill_write_file,
        available_for_kinds=("evolver",),
    ),
    "propose_skill_remove_file": BuiltinTool(
        name="propose_skill_remove_file",
        description=(
            "Propose removing a supplementary file from a pack folder. "
            "Cannot target SKILL.md; use propose_skill_delete to "
            "remove the entire pack."
        ),
        args_model=ProposeSkillRemoveFileArgs,
        runner=run_propose_skill_remove_file,
        available_for_kinds=("evolver",),
    ),
    # ── Evolver-only helpers (M2.2) ─────────────────────────
    # The platform-builtin evolver agent reads recent low-scoring
    # artifacts via ``list_session_artifacts`` (structural metadata
    # only — no raw user_text leaves the row), pulls the SKILL.md
    # body to be patched via ``read_skill_pack``, and signals "no
    # change worth filing" via ``mark_skip``. All three carry
    # ``available_for_kinds=("evolver",)`` so they never appear in a
    # regular workspace agent's tool catalogue.
    "list_session_artifacts": BuiltinTool(
        name="list_session_artifacts",
        description=(
            "List recent low-scoring session artifacts in this "
            "workspace (structural metadata only — judge_score, "
            "error_kind, invoked_tools, injected_skill_pack_ids). Use "
            "this FIRST to identify failing runs worth fixing before "
            "proposing a SkillPack change."
        ),
        args_model=ListSessionArtifactsArgs,
        runner=run_list_session_artifacts,
        available_for_kinds=("evolver",),
    ),
    "read_skill_pack": BuiltinTool(
        name="read_skill_pack",
        description=(
            "Read a SkillPack's metadata + ACTIVE version body "
            "(truncated to 8000 chars). Call this BEFORE "
            "propose_skill_patch / propose_skill_edit so old_text "
            "matches the live bytes."
        ),
        args_model=ReadSkillPackArgs,
        runner=run_read_skill_pack,
        available_for_kinds=("evolver",),
    ),
    "mark_skip": BuiltinTool(
        name="mark_skip",
        description=(
            "Record that this batch of artifacts is healthy and no "
            "SkillPack change is worth filing. Calling this stops "
            "the evolver run gracefully — prefer it over filing "
            "speculative proposals."
        ),
        args_model=MarkSkipArgs,
        runner=run_mark_skip,
        available_for_kinds=("evolver",),
    ),
    # ── Evolver-only cronjob proposal verb (M2.8) ─────────────
    # File a flow_create Approval row asking an admin to materialise
    # a recurring or one-shot Flow. NEVER calls flow_svc.create_flow
    # directly; the M2.5 dispatch handler is what eventually creates
    # the Flow (and lands it ``enabled=False`` so the admin must
    # explicitly turn it on from the Flow UI as the second human
    # gate before any cron tick fires).
    "propose_cronjob_create": BuiltinTool(
        name="propose_cronjob_create",
        description=(
            "Propose a recurring or one-shot Flow (e.g. 'every morning "
            "at 09:00 read me the OKR'). schedule accepts a 5-field cron "
            "expression (UTC), an 'every Nu' interval ('every 2h' / "
            "'every 30m', u in {s,m,h,d}), or an ISO 8601 timestamp for "
            "a one-shot run. target_agent_id defaults to the calling "
            "agent. Files an admin Approval (TTL 7d); nothing fires "
            "until the admin both approves the proposal AND enables "
            "the resulting Flow row from the Flow UI."
        ),
        args_model=ProposeCronjobArgs,
        runner=run_propose_cronjob,
        available_for_kinds=("evolver",),
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
