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

import io
import re
import shutil
import zipfile
from pathlib import Path, PurePosixPath

import httpx
from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile, status
from pydantic import BaseModel, Field

from app.agents.harness.skills import BUNDLED_SKILLS_DIR
from app.api.deps import CurrentIdentityId, CurrentWorkspaceId, DBSession
from app.core.config import settings
from app.core.errors import Unauthorized
from app.services import audit as audit_svc
from app.services import workspace as ws_svc

router = APIRouter()

_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9\-_]{0,62}$")

# Reasonable upper bounds — Anthropic-style skills bundle SKILL.md plus
# a small set of reference docs / scripts. We refuse anything that
# smells like a bulk repo dump or zip-bomb.
_MAX_BUNDLE_BYTES = 5 * 1024 * 1024  # 5 MiB compressed
_MAX_UNCOMPRESSED_BYTES = 25 * 1024 * 1024  # 25 MiB extracted total
_MAX_FILES_PER_BUNDLE = 200
_ALLOWED_EXTS = {
    ".md",
    ".txt",
    ".json",
    ".yaml",
    ".yml",
    ".toml",
    ".py",
    ".js",
    ".ts",
    ".tsx",
    ".jsx",
    ".html",
    ".css",
    ".csv",
    ".sh",
    ".bash",
    ".sql",
    ".xml",
}


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
    await ws_svc.ensure_member_access(db, workspace_id=workspace_id, identity_id=identity_id)
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
    await ws_svc.ensure_member_access(db, workspace_id=workspace_id, identity_id=identity_id)
    md = _resolve_skill_path(source, slug, workspace_id)
    if md is None or not md.exists():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="skill_not_found")
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
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="skill_not_found")
    # Safety: only allow deletion of directories inside the workspace skills
    # tree. The ``relative_to`` call throws if skill_dir escaped via ../.
    skill_dir.resolve().relative_to(_workspace_skills_dir(workspace_id).resolve())
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


# ─── Bundle / URL import ────────────────────────────────────
class SkillImportUrlBody(BaseModel):
    """Body for `POST /skills/import-url`."""

    url: str = Field(min_length=1, max_length=2048)
    slug: str | None = Field(default=None, max_length=63)
    # Currently only `workspace` is supported; accepted for forward-compat.
    visibility: str = Field(default="workspace")


def _normalize_github_to_raw(url: str) -> str:
    """Convert a github.com blob/tree URL to its raw equivalent.

    `https://github.com/owner/repo/blob/main/path/SKILL.md`
    → `https://raw.githubusercontent.com/owner/repo/main/path/SKILL.md`

    Leaves non-GitHub URLs untouched so plain raw URLs and arbitrary
    HTTPS endpoints still work.
    """
    if "raw.githubusercontent.com" in url:
        return url
    if "github.com" in url:
        return (
            url.replace("https://github.com/", "https://raw.githubusercontent.com/")
            .replace("http://github.com/", "https://raw.githubusercontent.com/")
            .replace("/blob/", "/")
            .replace("/tree/", "/")
        )
    return url


def _slug_from_front_matter_or_default(content: str, fallback: str) -> str:
    fm, _ = _parse_front_matter(content)
    raw = (fm.get("name") or fallback).strip().lower()
    cleaned = re.sub(r"[^a-z0-9_-]+", "-", raw).strip("-_") or fallback
    return cleaned[:63]


def _safe_relative(root: Path, target: Path) -> Path:
    """Return ``target`` relative to ``root`` or raise on traversal."""
    resolved = target.resolve()
    return resolved.relative_to(root.resolve())


def _validate_bundle_member(name: str) -> str:
    """Sanity-check a ZIP entry path. Returns the cleaned posix path."""
    if not name or name.startswith("/"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="invalid_bundle_path (absolute path)",
        )
    posix = PurePosixPath(name)
    parts = posix.parts
    if any(part in {"..", ""} for part in parts):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="invalid_bundle_path (traversal)",
        )
    if any(part.startswith(".") for part in parts):
        # Allow hidden files? Skills don't need them; reject for safety.
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="invalid_bundle_path (dotfiles not allowed)",
        )
    return str(posix)


def _common_top_dir(members: list[str]) -> str | None:
    """If every entry shares one top-level dir, return it; else None."""
    if not members:
        return None
    tops = {m.split("/", 1)[0] for m in members}
    if len(tops) != 1:
        return None
    only = next(iter(tops))
    return only if any("/" in m for m in members) else None


@router.post(
    "/import-url",
    response_model=SkillRead,
    status_code=status.HTTP_201_CREATED,
)
async def import_skill_from_url(
    body: SkillImportUrlBody,
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
    request: Request,
) -> SkillRead:
    """Fetch a SKILL.md from a public URL.

    Runs server-side so the user's browser doesn't run into CORS for
    arbitrary hosts (e.g. raw.githubusercontent.com works in the
    browser, but Gist / private CDN hosts often don't). GitHub
    `blob/` and `tree/` URLs are normalised to `raw.` automatically.
    """
    if workspace_id is None:
        raise Unauthorized("no_active_workspace", code="auth.no_active_workspace")
    await ws_svc.ensure_admin(db, workspace_id=workspace_id, identity_id=identity_id)

    raw_url = _normalize_github_to_raw(body.url.strip())
    if not raw_url.startswith(("http://", "https://")):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="invalid_url",
        )

    try:
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
            resp = await client.get(raw_url)
        if resp.status_code != 200:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"fetch_failed_{resp.status_code}",
            )
        content = resp.text
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="fetch_failed_network",
        ) from exc

    if len(content) > 200_000:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="content_too_large")

    fallback = body.slug or _safe_filename_to_slug(raw_url)
    slug = _slug_from_front_matter_or_default(content, fallback)
    if not _SLUG_RE.match(slug):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="invalid_slug")

    if not content.lstrip().startswith("---"):
        first_line = content.splitlines()[0] if content.strip() else slug
        content = f"---\nname: {slug}\ndescription: {first_line}\n---\n\n{content}"

    root = _workspace_skills_dir(workspace_id)
    skill_dir = root / slug
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(content, encoding="utf-8")

    await audit_svc.record(
        db,
        action="skill.import_url",
        actor_identity_id=identity_id,
        workspace_id=workspace_id,
        resource_type="skill",
        summary=f"imported skill {slug!r} from URL",
        metadata={"slug": slug, "url": raw_url, "bytes": len(content)},
        request=request,
    )
    await db.commit()
    return _read_skill(skill_dir / "SKILL.md", source="workspace", slug=slug)


def _safe_filename_to_slug(url: str) -> str:
    tail = url.rstrip("/").split("/")[-1].lower()
    base = re.sub(r"\.md$", "", tail)
    return re.sub(r"[^a-z0-9_-]+", "-", base).strip("-_")[:63] or "imported"


@router.post(
    "/import-bundle",
    response_model=SkillRead,
    status_code=status.HTTP_201_CREATED,
)
async def import_skill_bundle(
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
    request: Request,
    file: UploadFile = File(..., description="ZIP archive containing SKILL.md"),
    slug: str | None = Form(default=None),
) -> SkillRead:
    """Import a folder-shaped Skill (Anthropic Agent Skills standard).

    Accepts a ZIP archive that contains a `SKILL.md` plus any number
    of supporting reference docs / scripts. The bundle is extracted
    server-side into ``{STORAGE_LOCAL_PATH}/skills/<workspace>/<slug>/``
    so the runtime can later expose references to the agent.

    Safety rails:
      - Hard caps on compressed/uncompressed size + file count
      - Path-traversal guard on every entry
      - Allow-list of file extensions (text/code only)
      - Strips a single common top-level directory if present, so
        bundles produced by ``zip -r foo.zip foo/`` don't end up
        nested twice.
    """
    if workspace_id is None:
        raise Unauthorized("no_active_workspace", code="auth.no_active_workspace")
    await ws_svc.ensure_admin(db, workspace_id=workspace_id, identity_id=identity_id)

    raw = await file.read()
    if not raw:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="empty_bundle")
    if len(raw) > _MAX_BUNDLE_BYTES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="bundle_too_large",
        )

    try:
        zf = zipfile.ZipFile(io.BytesIO(raw))
    except zipfile.BadZipFile as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="invalid_zip") from exc

    members = [m for m in zf.namelist() if not m.endswith("/")]
    if len(members) == 0:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="empty_bundle")
    if len(members) > _MAX_FILES_PER_BUNDLE:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="too_many_files")

    cleaned: list[tuple[str, str]] = []  # (cleaned_path, original_name)
    for name in members:
        cleaned.append((_validate_bundle_member(name), name))

    common = _common_top_dir([c for c, _ in cleaned])
    if common:
        cleaned = [(c[len(common) + 1 :], orig) for c, orig in cleaned]

    skill_md_paths = [c for c, _ in cleaned if c.lower().endswith("skill.md")]
    if not skill_md_paths:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="missing_skill_md")
    skill_md_paths.sort(key=lambda p: p.count("/"))
    skill_md_path = skill_md_paths[0]
    if "/" in skill_md_path:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="skill_md_must_be_at_root",
        )

    # Read SKILL.md to derive the slug from front-matter when not given.
    skill_orig = next(orig for c, orig in cleaned if c == skill_md_path)
    skill_md_bytes = zf.read(skill_orig)
    try:
        skill_md_text = skill_md_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="skill_md_not_utf8"
        ) from exc

    fallback = (slug or "").strip().lower() or _safe_filename_to_slug(file.filename or "skill")
    final_slug = _slug_from_front_matter_or_default(skill_md_text, fallback)
    if not _SLUG_RE.match(final_slug):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="invalid_slug")

    # Synthesize front-matter if author forgot — same convention as
    # ``upload_skill`` so listing/runtime stay consistent.
    if not skill_md_text.lstrip().startswith("---"):
        first_line = skill_md_text.splitlines()[0] if skill_md_text.strip() else final_slug
        skill_md_text = (
            f"---\nname: {final_slug}\ndescription: {first_line}\n---\n\n{skill_md_text}"
        )

    root = _workspace_skills_dir(workspace_id)
    root.mkdir(parents=True, exist_ok=True)
    skill_dir = root / final_slug
    if skill_dir.exists():
        shutil.rmtree(skill_dir)
    skill_dir.mkdir(parents=True)

    total_uncompressed = 0
    persisted_files = 0

    for cleaned_path, orig in cleaned:
        if not cleaned_path:
            continue
        ext = Path(cleaned_path).suffix.lower()
        is_skill_md = cleaned_path == skill_md_path
        if not is_skill_md and ext not in _ALLOWED_EXTS:
            # Skip silently — keeps bundles forgiving for stray .DS_Store / images
            continue
        info = zf.getinfo(orig)
        if info.file_size > _MAX_UNCOMPRESSED_BYTES:
            shutil.rmtree(skill_dir, ignore_errors=True)
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="bundle_too_large")
        total_uncompressed += info.file_size
        if total_uncompressed > _MAX_UNCOMPRESSED_BYTES:
            shutil.rmtree(skill_dir, ignore_errors=True)
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="bundle_too_large")

        target = skill_dir / cleaned_path
        try:
            _safe_relative(skill_dir, target)
        except ValueError as exc:
            shutil.rmtree(skill_dir, ignore_errors=True)
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="invalid_bundle_path (traversal)",
            ) from exc
        target.parent.mkdir(parents=True, exist_ok=True)
        if is_skill_md:
            target.write_text(skill_md_text, encoding="utf-8")
        else:
            target.write_bytes(zf.read(orig))
        persisted_files += 1

    await audit_svc.record(
        db,
        action="skill.import_bundle",
        actor_identity_id=identity_id,
        workspace_id=workspace_id,
        resource_type="skill",
        summary=f"imported skill bundle {final_slug!r} ({persisted_files} files)",
        metadata={
            "slug": final_slug,
            "files": persisted_files,
            "compressed_bytes": len(raw),
            "uncompressed_bytes": total_uncompressed,
        },
        request=request,
    )
    await db.commit()
    return _read_skill(skill_dir / "SKILL.md", source="workspace", slug=final_slug)
