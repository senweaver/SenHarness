"""Context-management harness — sliding window + optional LLM summarization.

We use `pydantic-ai-summarization` which exposes:
  - ``SlidingWindowProcessor`` — zero-cost trim of old messages (default)
  - ``SummarizationProcessor``  — LLM-backed summary of older messages, preserving head/tail

Processors attach to an ``Agent`` via ``history_processors=[...]``. pydantic-ai
calls them between every turn so the model never sees bloated history.

Policy (reasonable defaults, overridable per-agent via ``metadata_json.context``):
  - Always install ``SlidingWindowProcessor`` trigger=150 messages, keep=60.
  - If ``SummarizationProcessor`` is requested (``context.summarize=true``) AND the
    agent has an auxiliary/same model wired, swap the sliding window for a
    summarize-then-keep approach.
"""

from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger(__name__)


def build_history_processors(
    *,
    policy: dict[str, Any] | None,
    primary_model: Any,
) -> list[Any]:
    """Return pydantic-ai history_processors for this run.

    `policy` keys consumed:
      - ``context.summarize`` (bool)     → use SummarizationProcessor instead of plain sliding
      - ``context.trigger_messages`` int → threshold message count (default 150)
      - ``context.keep_messages`` int    → messages to keep after trim (default 60)
      - ``context.keep_head_messages`` int → system+first-N to always preserve (default 4)

    The final stage of the chain is always ``repair_orphan_tool_calls``: the
    summarisation / sliding window stages above it operate on raw message
    counts and can slice through a ``ToolCallPart`` / ``ToolReturnPart`` pair,
    which makes OpenAI-compatible providers (DeepSeek especially) reject the
    request with HTTP 400 ``insufficient tool messages``. Running the
    pairing repair after any trim guarantees every payload that reaches the
    upstream model is self-consistent regardless of how the prior stages
    reshape the list.
    """
    from app.agents.harness.reliability import repair_orphan_tool_calls

    processors: list[Any] = []

    try:
        from pydantic_ai_summarization import (
            SlidingWindowProcessor,
            SummarizationProcessor,
        )
    except ImportError:  # pragma: no cover
        log.debug("pydantic-ai-summarization not installed; history unbounded")
        processors.append(repair_orphan_tool_calls)
        return processors

    ctx = (policy or {}).get("context") or {}
    trigger_msgs = int(ctx.get("trigger_messages") or 150)
    keep_msgs = int(ctx.get("keep_messages") or 60)
    keep_head = int(ctx.get("keep_head_messages") or 4)

    if ctx.get("summarize"):
        try:
            processors.append(
                SummarizationProcessor(
                    model=primary_model,
                    trigger=("messages", trigger_msgs),
                    keep=("messages", keep_msgs),
                )
            )
        except Exception as e:  # pragma: no cover
            log.warning("SummarizationProcessor init failed: %s; falling back to sliding", e)

    if not processors:
        try:
            processors.append(
                SlidingWindowProcessor(
                    trigger=("messages", trigger_msgs),
                    keep=("messages", keep_msgs),
                    keep_head=("messages", keep_head),
                )
            )
        except Exception as e:  # pragma: no cover
            log.warning("SlidingWindowProcessor init failed: %s; running without trim", e)

    processors.append(repair_orphan_tool_calls)
    return processors
