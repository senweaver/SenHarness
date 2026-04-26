"""SkillPack harness — Anthropic Agent Skills compatible via ``pydantic-ai-skills``.

Two skill sources are probed:
  1. **Bundled skills** at ``backend/app/agents/skills/<slug>/SKILL.md`` —
     read-only, shipped with the image. Browse/use always.
  2. **Per-workspace skills** at ``{STORAGE_LOCAL_PATH}/skills/<workspace>/<slug>/SKILL.md``
     — mutable, admins can upload future packs here.

Opt-in per Agent via ``metadata_json.skills``:

  - omitted / ``false``                     → no skills attached.
  - ``true``                                → all available skills attached.
  - ``list[str]``                           → only matching skill names attached
    (matches by SKILL.md front-matter ``name``).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from app.core.config import settings

log = logging.getLogger(__name__)

BUNDLED_SKILLS_DIR = Path(__file__).resolve().parents[1] / "skills"


def build_skills_capability(
    *, policy: dict[str, Any] | None, workspace_id: Any = None
) -> Any | None:
    """Return a ``SkillsCapability`` instance or ``None`` if disabled."""
    spec = (policy or {}).get("skills")
    if spec is None or spec is False:
        return None

    try:
        from pydantic_ai_skills import (
            SkillsCapability,
            discover_skills,
        )
    except ImportError:  # pragma: no cover
        log.info("pydantic-ai-skills not installed; skills disabled")
        return None

    # Collect skills from all sources.
    candidates: list[Any] = []
    candidates.extend(_discover(discover_skills, BUNDLED_SKILLS_DIR))

    if workspace_id is not None:
        ws_dir = Path(settings.STORAGE_LOCAL_PATH) / "skills" / str(workspace_id)
        candidates.extend(_discover(discover_skills, ws_dir))

    if not candidates:
        return None

    # Filter by names when spec is a list.
    if isinstance(spec, list):
        allowed = {str(n).strip() for n in spec if n}
        candidates = [s for s in candidates if _skill_name(s) in allowed]

    if not candidates:
        return None

    try:
        return SkillsCapability(skills=candidates, validate=False, auto_reload=False)
    except Exception as e:  # pragma: no cover
        log.warning("SkillsCapability init failed: %s", e)
        return None


def _discover(fn, path: Path) -> list[Any]:
    if not path.exists() or not path.is_dir():
        return []
    try:
        return list(fn(path, validate=False, max_depth=3))
    except Exception as e:
        log.debug("skill discovery in %s failed: %s", path, e)
        return []


def _skill_name(skill: Any) -> str:
    for attr in ("name", "id", "slug"):
        v = getattr(skill, attr, None)
        if v:
            return str(v)
    return ""
