"""Background helper: AI-generated follow-up question suggestions.

After a successful assistant turn (or whenever the chat surface asks for
"what could I follow up with?"), we feed the most recent ~6 messages to a
cheap LLM and ask for 3-5 short follow-up prompts.

The suggestions are *advisory only* — they never persist to the DB; we
return them straight to the caller. This keeps multi-tenant
isolation intact (no shared cache, no cross-tenant leakage) at the cost
of one extra LLM round-trip per request. Latency-sensitive callers can
fold the result into a TanStack Query cache on the frontend instead.

Lifecycle:
    1. ``POST /api/v1/sessions/{id}/suggestions`` → call
       :func:`generate_suggestions`.
    2. Pull the recent transcript via ``MessageRepository``.
    3. Round-trip through the same model resolver that powers the AI
       title task (cheap SKU when known) under a tight timeout.
    4. Parse the LLM output into a list of plain strings; cap at 5.
"""

from __future__ import annotations

import asyncio
import logging
import re
import uuid

from app.agents.kernels.model_client import (
    ResolvedModel,
    build_pydantic_ai_model,
    resolve_for_agent,
)
from app.db.session import get_session_factory
from app.repositories.session import MessageRepository, SessionRepository

log = logging.getLogger(__name__)


_PROMPT = (
    "You are a follow-up suggester. Read the conversation below and propose "
    "3 to 5 short follow-up questions the *user* might want to ask next. "
    "Each suggestion must:\n"
    "  - Be self-contained (no pronouns referring to earlier turns).\n"
    "  - Stay in the user's voice (imperative or interrogative).\n"
    "  - Be ≤ 14 words.\n"
    "  - Use the language of the user's last message.\n\n"
    "Output exactly one suggestion per line, prefixed with '- '. "
    "No numbering, no preface, no markdown other than the leading hyphen."
)

_MAX_SUGGESTIONS = 5
_TRANSCRIPT_TURN_BUDGET = 6
_TRANSCRIPT_CHARS_PER_TURN = 800


def _cheap_model_for(resolved: ResolvedModel | None) -> ResolvedModel | None:
    """Replace the resolved SKU with the provider's cheapest known model.

    Mirrors the policy in ``session_title.py`` so the suggestion task and
    the title task share the same "small / cheap" SKU table; no new env
    var, no new tenant config.
    """
    if resolved is None:
        return None
    cheap = {
        "openai": "gpt-4o-mini",
        "anthropic": "claude-3-5-haiku-latest",
        "google": "gemini-1.5-flash",
        "openrouter": "openai/gpt-4o-mini",
        "deepseek": "deepseek-chat",
        "moonshot": "moonshot-v1-8k",
        "groq": "llama-3.1-8b-instant",
    }.get(resolved.provider_kind)
    if not cheap:
        return resolved
    return ResolvedModel(
        provider_kind=resolved.provider_kind,
        model_name=cheap,
        api_key=resolved.api_key,
        base_url=resolved.base_url,
        extra=resolved.extra,
        source=resolved.source,
    )


def _parse_suggestions(raw: str) -> list[str]:
    """Slice the LLM's reply into at most ``_MAX_SUGGESTIONS`` clean strings.

    Accepts ``- foo``, ``* foo``, ``1. foo``, ``1) foo`` or bare lines.
    Filters out blank lines and lines longer than 200 chars (defends
    against a runaway model that emits a wall of text).
    """
    out: list[str] = []
    for line in (raw or "").splitlines():
        s = line.strip()
        if not s:
            continue
        s = re.sub(r"^[\-\*•·]+\s*", "", s)
        s = re.sub(r"^\d+[\.\)]\s*", "", s)
        s = s.strip().strip("\"'`").strip()
        if not s or len(s) > 200:
            continue
        out.append(s)
        if len(out) >= _MAX_SUGGESTIONS:
            break
    return out


async def generate_suggestions(
    *,
    workspace_id: uuid.UUID,
    session_id: uuid.UUID,
    agent_id: uuid.UUID | None,
) -> list[str]:
    """Round-trip the suggestion prompt and return a list of follow-ups.

    Returns ``[]`` for any short-circuit (no model, no transcript, LLM
    error / timeout). The caller turns ``[]`` into "no suggestions" — the
    chat surface should never crash on this path.
    """
    factory = get_session_factory()
    async with factory() as db:
        sess = await SessionRepository(db).get(session_id)
        if sess is None or sess.workspace_id != workspace_id:
            return []
        # Defensive guard — the route is supposed to short-circuit on
        # ``chat_features.suggestions_enabled=false``, but the service
        # is also reachable from queue tasks and tests so we re-check
        # here. Defaults off; opt-in via Agent.metadata_json.
        target_agent_id = agent_id or sess.subject_id
        if target_agent_id is not None:
            from app.repositories.agent import AgentRepository

            agent = await AgentRepository(db).get(target_agent_id)
            if agent is not None:
                features = (agent.metadata_json or {}).get("chat_features") or {}
                if not bool(features.get("suggestions_enabled", False)):
                    return []
        msgs = await MessageRepository(db).list_for_session(
            session_id=session_id, limit=_TRANSCRIPT_TURN_BUDGET
        )

    if not msgs:
        return []

    snippets: list[str] = []
    from app.db.models.message import MessageRole

    for m in msgs:
        if m.role not in {MessageRole.USER, MessageRole.ASSISTANT}:
            continue
        text = ""
        if isinstance(m.content_json, dict):
            t = m.content_json.get("text")
            if isinstance(t, str):
                text = t
        if not text:
            continue
        role = "user" if m.role == MessageRole.USER else "assistant"
        snippets.append(f"[{role}] {text.strip()[:_TRANSCRIPT_CHARS_PER_TURN]}")
    if not snippets:
        return []
    transcript = "\n\n".join(snippets)

    if target_agent_id is None:
        return []

    resolved = await resolve_for_agent(
        workspace_id=workspace_id, agent_id=target_agent_id, override=None
    )
    resolved = _cheap_model_for(resolved)
    if resolved is None:
        return []
    model = build_pydantic_ai_model(resolved)
    if model is None:
        return []

    try:
        from pydantic_ai import Agent
    except ImportError:  # pragma: no cover
        return []

    agent = Agent(model=model, system_prompt=_PROMPT)
    try:
        # Tight timeout — suggestions are best-effort UX sugar.
        result = await asyncio.wait_for(agent.run(transcript), timeout=12.0)
    except TimeoutError:
        log.info("suggestion generation timed out for session=%s", session_id)
        return []
    except Exception:  # pragma: no cover
        log.exception("suggestion generation failed for session=%s", session_id)
        return []
    raw = getattr(result, "output", None) or getattr(result, "data", None) or ""
    if not isinstance(raw, str):
        raw = str(raw)
    return _parse_suggestions(raw)
