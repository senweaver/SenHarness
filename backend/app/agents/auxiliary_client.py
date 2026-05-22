"""Minimal auxiliary LLM client (M0.1 skeleton; M0.3 extends with judge).

The "auxiliary" tier is for *single-shot* LLM calls outside the agent
loop — judges, summarisers, alignment scorers — that must not pollute
the persisted message history nor break prompt cache.

Resolution order for the model used to serve a task:

1. ``workspace_settings.aux_model_<task>`` (e.g. ``aux_model_judge``)
   — explicit per-task override.
2. ``workspace_settings.aux_model_default`` — workspace-wide default.
3. The workspace's first enabled chat model (via ``resolve_for_workspace``)
   — fallback so a fresh workspace gets aux scoring without extra setup.

Aux tunables live under ``workspace.home_config_json["aux"]`` so M0.13's
schema-driven settings panel can claim them later without a migration.
The shape and defaults are codified in :data:`DEFAULT_AUX_SETTINGS`;
:func:`get_workspace_aux_settings` merges the persisted dict over those
defaults for read-only consumers.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.kernels.model_client import (
    ResolvedModel,
    parse_override,
    resolve_for_workspace,
)

log = logging.getLogger(__name__)


# ─── Aux config defaults ─────────────────────────────────────
# Single source of truth for the ``workspace.home_config_json["aux"]``
# schema. Adding a new key here is the *only* code change required to
# expose a new tunable to the read-only aux readout in
# ``settings/workspace/providers``; M0.13 will lift this into a real
# schema-driven settings table.
DEFAULT_AUX_SETTINGS: dict[str, Any] = {
    "aux_model_default": None,
    "aux_model_judge": None,
    "aux_model_goal_alignment": None,
    "aux_model_summarize": None,
    "judge_rate_per_minute": 60,
    "judge_fail_strikes": 5,
    "judge_fail_window_seconds": 300,
    "judge_breaker_recover_seconds": 3600,
    "judge_turns_serialized_chars": 12000,
    "judge_prompt_max_chars": 800,
    "summarize_rate_per_minute": 30,
    "summarize_fail_strikes": 3,
    "summarize_fail_window_seconds": 300,
    "summarize_breaker_recover_seconds": 1800,
}


class AuxiliaryTask(StrEnum):
    GOAL_ALIGNMENT = "goal_alignment"
    JUDGE = "judge"
    SUMMARIZE = "summarize"
    SKILL_REVIEW = "skill_review"


# Per-task fallback chain for ``aux_model_*`` resolution. The first
# non-empty entry in the workspace's ``aux`` settings wins. SUMMARIZE
# falls through JUDGE because the two share a "compress text into a
# short verdict" shape — operators that wired a cheap judge model
# typically want the same model for episodic summaries.
_TASK_FALLBACK_CHAIN: dict[AuxiliaryTask, tuple[str, ...]] = {
    AuxiliaryTask.SUMMARIZE: (
        "aux_model_summarize",
        "aux_model_judge",
        "aux_model_default",
    ),
}


# ─── Judge response schema (M0.3) ────────────────────────────
class JudgeVerdict(BaseModel):
    """Structured verdict the aux LLM produces for one captured run.

    ``score`` maps directly to the ``session_artifacts.judge_score``
    column: 1 → success, 0 → partial, -1 → failure. Service-layer code
    casts to float so SQL NULL still distinguishes "never judged".
    """

    score: Literal[1, 0, -1]
    confidence: float = Field(ge=0.0, le=1.0)
    rationale: str = Field(max_length=600)
    process_notes: list[str] = Field(default_factory=list, max_length=5)
    error_kind_hint: str | None = None


@dataclass(slots=True)
class AuxiliaryConfig:
    task: AuxiliaryTask
    model: str
    base_url: str | None = None
    api_key_ref: str | None = None
    max_tokens: int = 2048
    temperature: float = 0.3
    extra: dict[str, Any] = field(default_factory=dict)


# ─── Resolution ──────────────────────────────────────────────
async def get_aux_model(
    db: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    task: AuxiliaryTask,
) -> AuxiliaryConfig | None:
    """Resolve the aux model config for ``(workspace_id, task)``.

    Returns ``None`` when no provider is configured at all. The caller
    decides whether that's a hard error (rare) or a heuristic-fallback
    cue (judge / scorer paths).
    """
    aux_settings = await _read_workspace_aux_settings(db, workspace_id=workspace_id)
    chain = _TASK_FALLBACK_CHAIN.get(task, (f"aux_model_{task.value}", "aux_model_default"))
    task_override: Any = None
    for key in chain:
        candidate = aux_settings.get(key)
        if isinstance(candidate, str) and candidate.strip():
            task_override = candidate
            break

    parsed: ResolvedModel | None = None
    if isinstance(task_override, str) and task_override.strip():
        parsed = parse_override(task_override.strip())
        if parsed is not None and parsed.api_key is None:
            from_db = await _try_fill_api_key(
                workspace_id=workspace_id, prefer_kind=parsed.provider_kind
            )
            if from_db is not None:
                parsed.api_key = from_db.api_key
                parsed.base_url = parsed.base_url or from_db.base_url

    if parsed is None:
        # Fall back to whatever chat model the workspace already has.
        parsed = await resolve_for_workspace(workspace_id=workspace_id)

    if parsed is None:
        return None

    return AuxiliaryConfig(
        task=task,
        model=f"{parsed.provider_kind}:{parsed.model_name}",
        base_url=parsed.base_url,
        api_key_ref=parsed.api_key,
        extra={"_resolved": parsed},
    )


async def _read_workspace_aux_settings(
    db: AsyncSession, *, workspace_id: uuid.UUID
) -> dict[str, Any]:
    """Return ``workspace.home_config_json.get("aux", {})`` defensively.

    Internal — the resolver only needs the raw persisted overrides. Use
    :func:`get_workspace_aux_settings` from outside this module so you
    get the merged defaults too.
    """
    from app.repositories.workspace import WorkspaceRepository

    ws = await WorkspaceRepository(db).get(workspace_id)
    if ws is None:
        return {}
    home = ws.home_config_json or {}
    aux = home.get("aux") if isinstance(home, dict) else None
    return aux if isinstance(aux, dict) else {}


async def get_workspace_aux_settings(
    db: AsyncSession, *, workspace_id: uuid.UUID
) -> dict[str, Any]:
    """Read-side accessor merging persisted ``aux`` over module defaults.

    Lives here (and not in ``app/services/workspace.py``) so M0.12's
    workspace-quota subagent can keep evolving ``services/workspace.py``
    without a merge conflict; M0.13 will move both into a schema-driven
    settings table.
    """
    raw = await _read_workspace_aux_settings(db, workspace_id=workspace_id)
    merged: dict[str, Any] = {**DEFAULT_AUX_SETTINGS}
    for key, value in raw.items():
        if value is None:
            continue
        merged[key] = value
    return merged


async def _try_fill_api_key(*, workspace_id: uuid.UUID, prefer_kind: str) -> ResolvedModel | None:
    try:
        return await resolve_for_workspace(workspace_id=workspace_id, kind=prefer_kind)
    except Exception:  # pragma: no cover - defensive
        log.exception("aux api-key lookup failed")
        return None


# ─── Single-shot chat ────────────────────────────────────────
async def call_aux_chat(
    *,
    config: AuxiliaryConfig,
    system: str,
    user: str,
    response_format: type | None = None,
    timeout_s: float = 25.0,
) -> Any:
    """Issue one chat completion and return the assistant text (or pydantic model).

    ``response_format`` is forwarded as ``output_type`` to the underlying
    pydantic-ai Agent run. Returns ``None`` on any failure — judges /
    scorers degrade to a heuristic; loud failures are written to audit
    by the *caller*, not here.
    """
    resolved = config.extra.get("_resolved") if config.extra else None
    if not isinstance(resolved, ResolvedModel):
        # Allow callers to construct an AuxiliaryConfig directly without
        # going through ``get_aux_model``; we then synthesise a resolver.
        provider_kind, _, model_name = config.model.partition(":")
        if not provider_kind or not model_name:
            return None
        resolved = ResolvedModel(
            provider_kind=provider_kind,
            model_name=model_name,
            api_key=config.api_key_ref,
            base_url=config.base_url,
            source="override",
        )

    from app.agents.kernels.model_client import build_pydantic_ai_model

    model = build_pydantic_ai_model(resolved)
    if model is None:
        return None

    try:
        from pydantic_ai import Agent
    except ImportError:  # pragma: no cover - prod always has it
        return None

    kwargs: dict[str, Any] = {"model": model, "system_prompt": system}
    if response_format is not None:
        kwargs["output_type"] = response_format

    agent = Agent(**kwargs)
    try:
        result = await asyncio.wait_for(agent.run(user), timeout=timeout_s)
    except TimeoutError:
        log.info("aux chat timed out task=%s model=%s", config.task.value, config.model)
        return None
    except Exception:
        log.exception("aux chat failed task=%s model=%s", config.task.value, config.model)
        return None

    output = getattr(result, "output", None)
    if output is None:
        output = getattr(result, "data", None)
    if response_format is not None:
        return output
    return output if isinstance(output, str) else (str(output) if output is not None else None)


# ─── M0.3 — judge helper ─────────────────────────────────────
def _serialise_artifact_turns(turns: list[dict[str, Any]] | None, *, max_chars: int) -> str:
    """Compact JSON-ish dump of artifact turns, hard-trimmed to ``max_chars``.

    Strips internal-only fields (``thinking``, raw tool args/results) so
    the aux model sees the *user-visible* trace only. The trim keeps the
    head — the start of a run usually tells the judge enough to score
    correctness; the tail of a long failure loop is mostly retries.
    """
    parts: list[str] = []
    for turn in turns or ():
        if not isinstance(turn, dict):
            continue
        role = str(turn.get("role") or "?")
        text = str(turn.get("text") or "").strip()
        tool_calls = turn.get("tool_calls") or []
        tool_results = turn.get("tool_results") or []
        line = f"[{role} #{turn.get('iteration', 0)}] {text}".strip()
        if tool_calls:
            names = sorted({str(tc.get("name") or "") for tc in tool_calls if isinstance(tc, dict)})
            names_clean = [n for n in names if n]
            if names_clean:
                line += f" | tools: {', '.join(names_clean)}"
        if tool_results:
            ok_count = sum(1 for tr in tool_results if isinstance(tr, dict) and tr.get("ok"))
            err_count = len(tool_results) - ok_count
            line += f" | tool_results ok={ok_count} err={err_count}"
        parts.append(line)
    rendered = "\n".join(parts)
    suffix = "\n[truncated]"
    if len(rendered) > max_chars:
        head_room = max(1, max_chars - len(suffix))
        rendered = rendered[:head_room] + suffix
    return rendered


def _load_judge_prompt() -> str:
    """Load the judge system prompt from disk (cached)."""
    from functools import cache
    from pathlib import Path

    @cache
    def _read() -> str:
        path = Path(__file__).parent / "templates" / "judge_run.md"
        try:
            return path.read_text(encoding="utf-8").strip()
        except FileNotFoundError:  # pragma: no cover - dev sanity
            return "Score the run as 1 / 0 / -1."

    return _read()


def render_judge_user_prompt(
    *,
    artifact: Any,
    turns_serialized: str,
    max_chars: int = 800,
) -> str:
    """Build the user prompt for ``call_aux_judge``.

    The runtime trace itself goes *outside* the rendered prompt budget
    because trimming the trace is the caller's job (via
    ``_serialise_artifact_turns``). The header lives inside the budget
    so a future tweak to the framing instructions can't blow up the
    aux call.
    """
    header = (
        f"final_outcome={getattr(artifact, 'final_outcome', '?')} "
        f"error_kind={getattr(artifact, 'error_kind', None) or 'none'} "
        f"iterations={getattr(artifact, 'iteration_count', 0)} "
        f"tools={','.join(getattr(artifact, 'invoked_tools', []) or [])}\n"
        "Score the run."
    )
    if len(header) > max_chars:
        header = header[: max_chars - 1] + "…"
    return f"{header}\n---\nTRACE:\n{turns_serialized}"


async def call_aux_judge(
    db: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    artifact: Any,
    turns_serialized: str,
    response_format: type[BaseModel] = JudgeVerdict,
    timeout_s: float = 25.0,
    prompt_max_chars: int = 800,
) -> tuple[BaseModel | None, AuxiliaryConfig | None]:
    """Run a single judge call. Returns ``(parsed_verdict, config_used)``.

    ``parsed_verdict`` is ``None`` when:
    * no aux model is configured for the workspace, or
    * the aux call timed out, raised, or produced an unparseable shape.

    ``config_used`` is returned even on failure so the caller can audit
    which model was attempted (degrades to ``None`` only when no model
    resolves at all).
    """
    config = await get_aux_model(db, workspace_id=workspace_id, task=AuxiliaryTask.JUDGE)
    if config is None:
        return None, None

    system = _load_judge_prompt()
    user_prompt = render_judge_user_prompt(
        artifact=artifact,
        turns_serialized=turns_serialized,
        max_chars=prompt_max_chars,
    )

    response = await call_aux_chat(
        config=config,
        system=system,
        user=user_prompt,
        response_format=response_format,
        timeout_s=timeout_s,
    )
    if isinstance(response, response_format):
        return response, config
    return None, config
