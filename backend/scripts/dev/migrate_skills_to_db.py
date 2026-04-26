"""Migrate workspace disk skills into DB skill_packs/skill_files.

Usage:
    python scripts/dev/migrate_skills_to_db.py --workspace-id <uuid>
    python scripts/dev/migrate_skills_to_db.py --all-workspaces
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import re
import uuid
from pathlib import Path

from sqlalchemy import select

from app.core.config import settings
from app.db.models.skills import SkillPackSource
from app.db.models.workspace import Workspace
from app.db.session import get_session_factory
from app.repositories.skills import SkillFileRepository, SkillPackRepository

_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9\-_]{0,62}$")
log = logging.getLogger(__name__)


def _parse_front_matter(text: str) -> tuple[dict, str]:
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


async def _workspace_ids(all_workspaces: bool, workspace_id: str | None) -> list[uuid.UUID]:
    if workspace_id:
        return [uuid.UUID(workspace_id)]
    if not all_workspaces:
        raise ValueError("pass --workspace-id or --all-workspaces")
    factory = get_session_factory()
    async with factory() as db:
        rows = (
            await db.execute(select(Workspace.id).where(Workspace.deleted_at.is_(None)))
        ).scalars()
        return list(rows)


async def _migrate_workspace(ws_id: uuid.UUID) -> tuple[int, int]:
    root = Path(settings.STORAGE_LOCAL_PATH) / "skills" / str(ws_id)
    if not root.exists() or not root.is_dir():
        return 0, 0
    factory = get_session_factory()
    created = 0
    skipped = 0
    async with factory() as db:
        pack_repo = SkillPackRepository(db)
        file_repo = SkillFileRepository(db)
        for sub in root.iterdir():
            if not sub.is_dir():
                continue
            slug = sub.name.strip().lower()
            if not _SLUG_RE.match(slug):
                skipped += 1
                continue
            md = sub / "SKILL.md"
            if not md.exists():
                skipped += 1
                continue
            text = md.read_text(encoding="utf-8", errors="replace")
            fm, body = _parse_front_matter(text)
            existing = await pack_repo.get_by_slug(workspace_id=ws_id, slug=slug)
            if existing is not None:
                skipped += 1
                continue
            pack = await pack_repo.create(
                workspace_id=ws_id,
                slug=slug,
                name=fm.get("name", slug) or slug,
                description=fm.get("description", ""),
                version="0.1.0",
                publisher="workspace-migration",
                source=SkillPackSource.IMPORTED,
                manifest_json={"migrated_from_disk": True},
                enabled=True,
                metadata_json={"migrated": True},
            )
            await file_repo.create(
                workspace_id=ws_id,
                skill_pack_id=pack.id,
                path="SKILL.md",
                content_md=body,
                metadata_json={"migrated_from_disk": True},
            )
            created += 1
        await db.commit()
    return created, skipped


async def _main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace-id", type=str, default=None)
    parser.add_argument("--all-workspaces", action="store_true")
    args = parser.parse_args()

    ids = await _workspace_ids(
        all_workspaces=args.all_workspaces,
        workspace_id=args.workspace_id,
    )
    total_created = 0
    total_skipped = 0
    for ws_id in ids:
        created, skipped = await _migrate_workspace(ws_id)
        total_created += created
        total_skipped += skipped
        log.info("[%s] created=%s skipped=%s", ws_id, created, skipped)
    log.info("done: created=%s skipped=%s", total_created, total_skipped)


if __name__ == "__main__":
    asyncio.run(_main())
