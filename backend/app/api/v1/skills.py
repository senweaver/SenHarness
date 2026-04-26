"""Skills CRUD — bundled (read-only) + per-workspace (mutable).

Directory layout:
    /app/app/agents/skills/<slug>/SKILL.md         — bundled (in the image)
    {STORAGE_LOCAL_PATH}/skills/<workspace>/<slug>/SKILL.md  — workspace-owned

SKILL.md uses standard YAML front-matter:

    ---
    name: <slug>
    description: <one-liner>
    license: MIT
    ---
    <markdown body>

The UI lets admins paste a full SKILL.md; we parse front-matter for the name
and description, fall back to the slug argument when missing.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field

from app.agents.harness.skills import BUNDLED_SKILLS_DIR
from app.api.deps import CurrentIdentityId, CurrentWorkspaceId, DBSession
from app.core.config import settings
from app.core.errors import Unauthorized
from app.services import audit as audit_svc
from app.services import workspace as ws_svc

router = APIRouter()

_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9\-_]{0,62}$")


# ─── DTOs ─────────────────────────────────────────────────
class SkillRead(BaseModel):
    slug: str
    name: str
    description: str
    source: str  # "bundled" | "workspace"
    prompt_preview: str = ""  # first ~400 chars of SKILL.md body
    body_length: int = 0


class SkillDetail(SkillRead):
    content: str  # full SKILL.md


class SkillUpload(BaseModel):
    slug: str = Field(min_length=1, max_length=63)
    content: str = Field(min_length=1, max_length=200_000)


# ─── Helpers ──────────────────────────────────────────────
def _workspace_skills_dir(workspace_id) -> Path:
    return Path(settings.STORAGE_LOCAL_PATH) / "skills" / str(workspace_id)


def _parse_front_matter(text: str) -> tuple[dict, str]:
    """Return (front_matter_dict, body_markdown)."""
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


def _read_skill(md_path: Path, source: str, slug: str) -> SkillRead:
    text = md_path.read_text(encoding="utf-8", errors="replace")
    fm, body = _parse_front_matter(text)
    preview = body.strip().split("\n\n", 1)[0].strip()
    if len(preview) > 400:
        preview = preview[:400].rstrip() + "…"
    return SkillRead(
        slug=fm.get("name", slug) or slug,
        name=fm.get("name", slug) or slug,
        description=fm.get("description", ""),
        source=source,
        prompt_preview=preview,
        body_length=len(body),
    )


def _scan(root: Path, source: str) -> list[SkillRead]:
    if not root.exists() or not root.is_dir():
        return []
    out: list[SkillRead] = []
    for sub in root.iterdir():
        if not sub.is_dir():
            continue
        md = sub / "SKILL.md"
        if not md.exists():
            continue
        try:
            out.append(_read_skill(md, source=source, slug=sub.name))
        except Exception:
            continue
    return out


def _require_admin_ws(_id):  # keeps the import used
    return _id


# ─── Endpoints ────────────────────────────────────────────
@router.get("", response_model=list[SkillRead])
async def list_skills(
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
) -> list[SkillRead]:
    if workspace_id is None:
        raise Unauthorized("no_active_workspace", code="auth.no_active_workspace")
    await ws_svc.ensure_member_access(
        db, workspace_id=workspace_id, identity_id=identity_id
    )
    rows: list[SkillRead] = []
    rows.extend(_scan(BUNDLED_SKILLS_DIR, source="bundled"))
    rows.extend(_scan(_workspace_skills_dir(workspace_id), source="workspace"))
    rows.sort(key=lambda s: (s.source == "workspace", s.name.lower()))
    return rows


@router.get("/{source}/{slug}", response_model=SkillDetail)
async def get_skill(
    source: str,
    slug: str,
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
) -> SkillDetail:
    if workspace_id is None:
        raise Unauthorized("no_active_workspace", code="auth.no_active_workspace")
    await ws_svc.ensure_member_access(
        db, workspace_id=workspace_id, identity_id=identity_id
    )
    md = _resolve_skill_path(source, slug, workspace_id)
    if md is None or not md.exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="skill_not_found"
        )
    text = md.read_text(encoding="utf-8", errors="replace")
    base = _read_skill(md, source=source, slug=slug)
    return SkillDetail(
        slug=base.slug,
        name=base.name,
        description=base.description,
        source=base.source,
        prompt_preview=base.prompt_preview,
        body_length=base.body_length,
        content=text,
    )


@router.post(
    "",
    response_model=SkillRead,
    status_code=status.HTTP_201_CREATED,
)
async def upload_skill(
    body: SkillUpload,
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
    request: Request,
) -> SkillRead:
    if workspace_id is None:
        raise Unauthorized("no_active_workspace", code="auth.no_active_workspace")
    await ws_svc.ensure_admin(db, workspace_id=workspace_id, identity_id=identity_id)

    slug = body.slug.strip().lower()
    if not _SLUG_RE.match(slug):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="invalid_slug (lowercase a-z, 0-9, -, _)",
        )

    # Ensure the content starts with valid front-matter — if the user forgot,
    # synthesize one from the slug + first line of the body as the description.
    content = body.content
    if not content.lstrip().startswith("---"):
        first_line = content.splitlines()[0] if content.strip() else slug
        content = f"---\nname: {slug}\ndescription: {first_line}\n---\n\n{content}"

    root = _workspace_skills_dir(workspace_id)
    root.mkdir(parents=True, exist_ok=True)
    skill_dir = root / slug
    skill_dir.mkdir(parents=True, exist_ok=True)
    md_path = skill_dir / "SKILL.md"
    md_path.write_text(content, encoding="utf-8")

    await audit_svc.record(
        db,
        action="skill.upload",
        actor_identity_id=identity_id,
        workspace_id=workspace_id,
        resource_type="skill",
        summary=f"uploaded skill {slug!r}",
        metadata={"slug": slug, "bytes": len(content)},
        request=request,
    )
    await db.commit()
    return _read_skill(md_path, source="workspace", slug=slug)


@router.delete("/workspace/{slug}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_workspace_skill(
    slug: str,
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
    request: Request,
) -> None:
    if workspace_id is None:
        raise Unauthorized("no_active_workspace", code="auth.no_active_workspace")
    await ws_svc.ensure_admin(db, workspace_id=workspace_id, identity_id=identity_id)

    skill_dir = _workspace_skills_dir(workspace_id) / slug
    if not skill_dir.exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="skill_not_found"
        )
    # Safety: only allow deletion of directories inside the workspace skills
    # tree. The ``relative_to`` call throws if skill_dir escaped via ../.
    skill_dir.resolve().relative_to(
        _workspace_skills_dir(workspace_id).resolve()
    )
    shutil.rmtree(skill_dir)

    await audit_svc.record(
        db,
        action="skill.delete",
        actor_identity_id=identity_id,
        workspace_id=workspace_id,
        resource_type="skill",
        summary=f"deleted skill {slug!r}",
        metadata={"slug": slug},
        request=request,
    )
    await db.commit()


def _resolve_skill_path(source: str, slug: str, workspace_id) -> Path | None:
    """Resolve the SKILL.md path, guarding against path-traversal."""
    if source == "bundled":
        root = BUNDLED_SKILLS_DIR
    elif source == "workspace":
        root = _workspace_skills_dir(workspace_id)
    else:
        return None
    if not root.exists():
        return None
    target = (root / slug / "SKILL.md").resolve()
    try:
        target.relative_to(root.resolve())
    except ValueError:
        return None
    return target
