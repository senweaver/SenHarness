"""Idempotent loader: vendored Markdown templates → ``agents`` rows.

Each ``backend/app/agents/templates/<category>/<slug>.md`` file becomes
a public, system-workspace-owned Agent row that ordinary users can
clone via the existing ``POST /agents/{id}/clone`` endpoint. Every row
is tagged with ``metadata_json.template = true`` plus
``metadata_json.template_slug = <file stem>`` so the loader can
re-run safely (upsert by slug).

The loader does not modify rows that humans have edited beyond the
template-managed fields — it always overwrites ``name``,
``description``, ``persona_md`` and the four template-owned keys in
``metadata_json``, but preserves any other keys an admin might have
added (e.g. ``code_mode``, ``approvals``).
"""

from __future__ import annotations

import logging
import uuid
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.templates import catalog
from app.db.models.agent import Agent, AgentVisibility, AutonomyLevel, BackendKind

log = logging.getLogger(__name__)

TEMPLATES_DIR = Path(__file__).resolve().parents[2] / "agents" / "templates"

# Skip non-agent files in the templates root (LICENSE, README, helper py).
_SKIP_NAMES: frozenset[str] = frozenset(
    {
        "LICENSE",
        "LICENSE.md",
        "README.md",
        "README",
        "catalog.py",
        # Standalone harness templates (instructions for sub-agents like the
        # planner); they live next to the catalogued templates but are not
        # marketplace agents.
        "planner.md",
        "evolver_persona.md",
        "judge_run.md",
        "session_search_summary.md",
    }
)

# Subdirectories whose contents are NOT marketplace agents (system prompt
# partials, helper Markdown). The loader recurses everything else.
_SKIP_DIRS: frozenset[str] = frozenset({"_partials", "reflection"})

# Max length for ``Agent.description`` per the column definition.
_DESC_MAX = 512

# Keys inside ``metadata_json`` we own and rewrite on every load.
_TEMPLATE_KEYS: frozenset[str] = frozenset(
    {"template", "template_slug", "category", "tags", "color"}
)


def split_frontmatter(text: str) -> tuple[dict[str, str], str]:
    """Tiny YAML-front-matter parser — no PyYAML dependency.

    Returns ``({}, text)`` if the document doesn't open with ``---``.
    Only flat ``key: value`` pairs are recognized; quoted values keep
    just the inner string.
    """
    if not text.startswith("---"):
        return {}, text
    end = text.find("\n---", 3)
    if end < 0:
        return {}, text
    block = text[3:end].strip()
    body = text[end + 4 :].lstrip("\n")
    fm: dict[str, str] = {}
    for line in block.splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            fm[k.strip()] = v.strip().strip("\"'")
    return fm, body


def _truncate(s: str | None, limit: int = _DESC_MAX) -> str | None:
    if not s:
        return None
    s = s.strip()
    if len(s) <= limit:
        return s
    return s[: limit - 1].rstrip() + "…"


def iter_template_files(root: Path = TEMPLATES_DIR) -> list[Path]:
    """Recursively list every ``*.md`` template file (excluding skip list).

    Skips:
        * Files named in :data:`_SKIP_NAMES` (LICENSE, README, planner.md, …).
        * Files inside any directory listed in :data:`_SKIP_DIRS` (e.g.
          ``_partials/`` which holds system-prompt fragments, not agents).
    """
    if not root.exists() or not root.is_dir():
        return []
    out: list[Path] = []
    for p in sorted(root.rglob("*.md")):
        if not p.is_file():
            continue
        if p.name in _SKIP_NAMES:
            continue
        if any(part in _SKIP_DIRS for part in p.relative_to(root).parts):
            continue
        out.append(p)
    return out


def parse_template(path: Path) -> dict | None:
    """Parse one template file into a dict ready for upsert.

    Returns ``None`` and logs a warning if the file lacks a ``name``
    front-matter field (defensive: should never happen for vendored
    files, but a contributor adding a stray ``.md`` shouldn't crash
    the seed).
    """
    text = path.read_text(encoding="utf-8", errors="replace")
    fm, body = split_frontmatter(text)
    name = fm.get("name", "").strip()
    if not name:
        log.warning("template %s missing front-matter `name`; skipping", path)
        return None

    slug = path.stem
    rel = path.relative_to(TEMPLATES_DIR)
    category = rel.parts[0] if len(rel.parts) > 1 else "specialized"

    return {
        "slug": slug,
        "category": category,
        "name": name,
        "description": _truncate(fm.get("description")),
        "persona_md": text,  # keep raw front-matter so re-export round-trips cleanly
        "color": fm.get("color") or None,
        "tags": catalog.get_tags(slug, category),
        "_body_present": bool(body.strip()),
    }


async def _find_existing(session: AsyncSession, slug: str) -> Agent | None:
    """Locate an existing template row by ``metadata_json.template_slug``."""
    stmt = (
        select(Agent)
        .where(
            Agent.metadata_json["template_slug"].astext == slug,
            Agent.deleted_at.is_(None),
        )
        .limit(1)
    )
    return (await session.execute(stmt)).scalar_one_or_none()


def _build_metadata(parsed: dict, existing: dict | None = None) -> dict:
    """Merge template-owned keys onto whatever an admin may have added.

    Anything inside :data:`_TEMPLATE_KEYS` is overwritten; everything
    else (e.g. ``code_mode``, ``sandbox`` flags) is preserved.
    """
    merged: dict = dict(existing or {})
    merged["template"] = True
    merged["template_slug"] = parsed["slug"]
    merged["category"] = parsed["category"]
    merged["tags"] = list(parsed["tags"])
    if parsed["color"]:
        merged["color"] = parsed["color"]
    elif "color" in _TEMPLATE_KEYS:
        merged.pop("color", None)
    return merged


async def load_all(
    session: AsyncSession,
    *,
    system_workspace_id: uuid.UUID,
    system_identity_id: uuid.UUID,
) -> tuple[int, int]:
    """Idempotent — load every vendored template into the system workspace.

    Returns ``(created, updated)`` counts. The caller owns the
    transaction (``session.commit()`` is intentionally not called here
    so the seeder can wrap multiple steps).
    """
    files = iter_template_files()
    created = 0
    updated = 0

    for f in files:
        parsed = parse_template(f)
        if parsed is None:
            continue

        existing = await _find_existing(session, parsed["slug"])
        if existing is None:
            session.add(
                Agent(
                    workspace_id=system_workspace_id,
                    created_by=system_identity_id,
                    name=parsed["name"],
                    description=parsed["description"],
                    persona_md=parsed["persona_md"],
                    backend_kind=BackendKind.NATIVE,
                    visibility=AgentVisibility.PUBLIC,
                    autonomy_level=AutonomyLevel.L2,
                    skill_refs_json=[],
                    memory_config_json={},
                    quotas_json={},
                    metadata_json=_build_metadata(parsed),
                )
            )
            created += 1
        else:
            existing.name = parsed["name"]
            existing.description = parsed["description"]
            existing.persona_md = parsed["persona_md"]
            if existing.visibility != AgentVisibility.PUBLIC:
                existing.visibility = AgentVisibility.PUBLIC
            existing.metadata_json = _build_metadata(parsed, existing.metadata_json)
            updated += 1

    await session.flush()
    return created, updated
