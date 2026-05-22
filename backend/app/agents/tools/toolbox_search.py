"""`find_tools` — toolbox search (aka pydantic-ai toolset deferred loading).

Large toolboxes overwhelm the model's tool-choice budget. This tool lets the
Agent **query** the registry for tools relevant to what it's about to do
(short natural-language description) and only then pick one to call.

Returns ``{matches: [{name, description, score}]}`` sorted by score. The
scoring is intentionally cheap (substring + token-overlap) so latency stays
flat even with hundreds of registered tools.

This pairs with the coding-agent prompt guidance: "If you don't see a tool
for the job, call `find_tools` before giving up."
"""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, Field


class FindToolsArgs(BaseModel):
    query: str = Field(
        ...,
        description=(
            "Natural-language description of what you want to do "
            "(e.g. 'parse a CSV', 'send a slack message')."
        ),
    )
    top_k: int = Field(default=5, ge=1, le=20, description="Max number of matches to return.")
    include_descriptions: bool = Field(
        default=True,
        description="Set false if you only need tool names (saves tokens).",
    )


_WORD_RE = re.compile(r"[a-z0-9_]+")


def _tokens(text: str) -> set[str]:
    return set(_WORD_RE.findall(text.lower()))


def _score(q_tokens: set[str], name: str, description: str) -> float:
    name_l = name.lower()
    desc_l = description.lower()
    n_tokens = _tokens(name_l)
    d_tokens = _tokens(desc_l)

    score = 0.0
    for token in q_tokens:
        if token in name_l:
            score += 2.0
        if token in n_tokens:
            score += 1.0
        if token in d_tokens:
            score += 0.4
        if token in desc_l:
            score += 0.2
    return score


def run_find_tools(args: FindToolsArgs) -> dict[str, Any]:
    from app.agents.tools import BUILTIN_TOOL_REGISTRY

    q_tokens = _tokens(args.query)
    if not q_tokens:
        return {"matches": []}

    scored: list[tuple[float, str, str]] = []
    for name, tool in BUILTIN_TOOL_REGISTRY.items():
        description = tool.description or ""
        s = _score(q_tokens, name, description)
        if s <= 0:
            continue
        scored.append((s, name, description))

    scored.sort(key=lambda x: x[0], reverse=True)
    scored = scored[: args.top_k]

    matches = [
        {
            "name": name,
            "score": round(score, 2),
            **({"description": description} if args.include_descriptions else {}),
        }
        for score, name, description in scored
    ]
    return {"matches": matches, "total": len(scored)}
