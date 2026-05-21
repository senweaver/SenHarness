"""Builtin tool registry — lightweight read-only view for the UI picker."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel

from app.agents.tools import (
    BUILTIN_TOOL_REGISTRY,
    CODING_TOOLBOX,
    DEFAULT_TOOLBOX,
)

router = APIRouter(prefix="/tools", tags=["tools"])


ToolCategory = Literal[
    "utility",
    "web",
    "filesystem",
    "memory",
    "multimedia",
    "coding",
]


# Source-of-truth categorisation. Keys are tool names that must already
# exist in BUILTIN_TOOL_REGISTRY; entries missing a mapping fall back to
# ``utility``. Mirrors the import-file grouping inside
# ``app/agents/tools/`` so a category map matches the source layout.
_CATEGORY_MAP: dict[str, ToolCategory] = {
    # utility
    "echo": "utility",
    "current_time": "utility",
    "calculator": "utility",
    "delegate_batch": "utility",
    "find_tools": "utility",
    # web
    "web_search": "web",
    "web_fetch": "web",
    # filesystem (session scratch)
    "read_file": "filesystem",
    "write_file": "filesystem",
    "list_files": "filesystem",
    "search_files": "filesystem",
    "delete_file": "filesystem",
    # memory
    "memorize": "memory",
    "recall": "memory",
    "list_memories": "memory",
    "forget": "memory",
    "session_search": "memory",
    "knowledge_search": "memory",
    # multimedia
    "generate_image": "multimedia",
    "speak": "multimedia",
    "transcribe": "multimedia",
    # coding
    "run_tests": "coding",
    "shell": "coding",
}


class ToolRegistryRow(BaseModel):
    """One row in the agent-editor builtin-tool picker."""

    name: str
    description: str
    category: ToolCategory
    default_in: list[Literal["default", "coding"]]


@router.get("/registry", response_model=list[ToolRegistryRow])
async def list_tool_registry() -> list[ToolRegistryRow]:
    """Return the user-selectable subset of BUILTIN_TOOL_REGISTRY.

    Evolver-only tools (``available_for_kinds=("evolver",)``) are
    filtered out — they're never useful in a workspace agent and would
    clutter the picker.
    """
    default_set = set(DEFAULT_TOOLBOX)
    coding_set = set(CODING_TOOLBOX)
    rows: list[ToolRegistryRow] = []
    for name, tool in BUILTIN_TOOL_REGISTRY.items():
        kinds = tool.available_for_kinds or ()
        if kinds and "evolver" in kinds:
            continue
        default_in: list[Literal["default", "coding"]] = []
        if name in default_set:
            default_in.append("default")
        if name in coding_set:
            default_in.append("coding")
        rows.append(
            ToolRegistryRow(
                name=name,
                description=tool.description,
                category=_CATEGORY_MAP.get(name, "utility"),
                default_in=default_in,
            )
        )
    rows.sort(key=lambda r: (r.category, r.name))
    return rows
