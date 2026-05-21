"""Background task: AI-generated session titles.

Lifecycle:
    1. New chat → first user message → ``title = first_message[:48]``,
       ``title_source = 'auto_truncate'`` (existing behaviour).
    2. After every successful assistant turn the WS handler calls
       :func:`maybe_upgrade_title` in a fire-and-forget task. If the
       session's ``title_source`` is anything other than ``'user'`` we ask
       the cheapest available LLM to summarise the first ~6 messages into a
       3-5 word title and persist it as ``title_source = 'auto_ai'``.
    3. The user manually renames → ``title_source = 'user'`` → step 2 is a
       no-op forever for that session.

Design notes:
    * We never block the user-facing turn on this. Silent failures are
      preferred over surfacing an error.
    * We pick a small / cheap model: env var ``AI_TITLE_MODEL`` overrides;
      otherwise we fall back to the agent's own resolved provider's
      cheapest SKU.
    * We stay tenant-scoped: the LLM call goes through the same model
      resolver used by the chat run, so the workspace's vault keys + rate
      limits apply.
"""

from __future__ import annotations

import asyncio
import logging
import os
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


_TITLE_PROMPT = (
    "Generate a 3-5 word title summarising this conversation. "
    "Output ONLY the title, no quotes, no punctuation at the end. "
    "Use the language of the user's first message."
)

_TITLE_MAX_CHARS = 64
_TITLE_MAX_WORDS = 8


def _cheap_model_for(resolved: ResolvedModel | None) -> ResolvedModel | None:
    """Replace ``resolved.model_name`` with a cheaper SKU when one is known.

    Idempotent for unrecognised providers — we just return ``resolved``
    unchanged so the upgrade still happens, just on the same SKU as the chat.
    """
    if resolved is None:
        return None
    override = os.environ.get("AI_TITLE_MODEL", "").strip()
    if override:
        return ResolvedModel(
            provider_kind=resolved.provider_kind,
            model_name=override,
            api_key=resolved.api_key,
            base_url=resolved.base_url,
            extra=resolved.extra,
            source=resolved.source,
        )
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


def _clean_title(raw: str) -> str:
    """Trim whitespace, strip surrounding quotes, collapse newlines, cap len."""
    s = (raw or "").strip()
    # Strip the most common LLM wrapping quirks (single chars only — ``str.strip``
    # treats multi-char arguments as a *set* of chars to strip, which the linter
    # flags as misleading. We loop the candidate chars instead).
    for ch in ('"', "'", "『", "』", "「", "」"):
        s = s.strip(ch)
    s = re.sub(r"\s+", " ", s)
    for ch in (".", "·", "。", "!", "?", "！", "？"):  # noqa: RUF001 - intentional CJK punctuation strip
        s = s.rstrip(ch)
    if len(s) > _TITLE_MAX_CHARS:
        s = s[:_TITLE_MAX_CHARS].rstrip()
    words = s.split(" ")
    if len(words) > _TITLE_MAX_WORDS:
        s = " ".join(words[:_TITLE_MAX_WORDS])
    return s


async def _summarise_with_llm(
    *,
    workspace_id: uuid.UUID,
    agent_id: uuid.UUID,
    transcript: str,
) -> str | None:
    """Round-trip the title prompt through pydantic-ai. ``None`` on any failure."""
    resolved = await resolve_for_agent(
        workspace_id=workspace_id, agent_id=agent_id, override=None
    )
    resolved = _cheap_model_for(resolved)
    if resolved is None:
        return None
    model = build_pydantic_ai_model(resolved)
    if model is None:
        return None

    try:
        from pydantic_ai import Agent
    except ImportError:  # pragma: no cover
        return None

    agent = Agent(model=model, system_prompt=_TITLE_PROMPT)
    try:
        # Tight timeout — title generation is best-effort, never block on it.
        result = await asyncio.wait_for(agent.run(transcript), timeout=12.0)
    except TimeoutError:
        log.info("title upgrade timed out for agent=%s", agent_id)
        return None
    except Exception:  # pragma: no cover
        log.exception("title upgrade run failed for agent=%s", agent_id)
        return None
    text = getattr(result, "output", None) or getattr(result, "data", None) or ""
    if not isinstance(text, str):
        text = str(text)
    return _clean_title(text) or None


async def maybe_upgrade_title(
    *,
    session_id: uuid.UUID,
    workspace_id: uuid.UUID,
    agent_id: uuid.UUID,
) -> str | None:
    """If eligible, generate + persist a fresh AI title; return the new title.

    Returns ``None`` when:
        * The session is gone or not in this workspace.
        * The user has manually renamed (``title_source == 'user'``).
        * There is < 1 user message and < 1 assistant reply yet.
        * The LLM call fails.
    """
    from app.db.models.message import MessageRole
    from app.db.models.session import TitleSource

    factory = get_session_factory()

    # Read the transcript + decide eligibility in one short transaction.
    async with factory() as db:
        sess = await SessionRepository(db).get(session_id)
        if sess is None or sess.workspace_id != workspace_id:
            return None
        if str(sess.title_source) == TitleSource.USER.value:
            return None

        msgs = await MessageRepository(db).list_for_session(
            session_id=session_id, limit=8
        )
        # Need at least one user + one assistant turn for a sensible summary.
        roles = {m.role for m in msgs}
        if MessageRole.USER not in roles or MessageRole.ASSISTANT not in roles:
            return None

        snippets: list[str] = []
        for m in msgs:
            text = ""
            if isinstance(m.content_json, dict):
                t = m.content_json.get("text")
                if isinstance(t, str):
                    text = t
            if not text:
                continue
            role_label = "user" if m.role == MessageRole.USER else "assistant"
            snippets.append(f"[{role_label}] {text.strip()[:600]}")
        transcript = "\n\n".join(snippets)
        if not transcript.strip():
            return None

    # LLM call happens *outside* the DB session so we don't hold a connection
    # for the full request latency.
    new_title = await _summarise_with_llm(
        workspace_id=workspace_id, agent_id=agent_id, transcript=transcript
    )
    if not new_title:
        return None

    # Persist — re-check title_source under a fresh session so we don't race
    # a manual rename that landed while the LLM was thinking.
    async with factory() as db:
        sess = await SessionRepository(db).get(session_id)
        if sess is None or sess.workspace_id != workspace_id:
            return None
        if str(sess.title_source) == TitleSource.USER.value:
            return None
        if sess.title == new_title:
            # Nothing to update; the cheap model produced the same string.
            return None
        sess.title = new_title
        sess.title_source = TitleSource.AUTO_AI
        await db.flush([sess])
        await db.commit()

    return new_title
