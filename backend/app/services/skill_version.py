"""Immutable SkillPackVersion authoring + state machine (M1.2).

Single source of truth for snapshotting SkillPack content, flipping
which snapshot is ACTIVE, and rolling back to a historical version.

Cross-cutting invariants enforced here:

* **content_hash dedup** — :func:`create_version` refuses to insert a
  second row with the same ``(pack_id, content_hash)`` pair. Repeated
  identical proposals (a slow editor that keeps Ctrl-S'ing the same
  body, the evolver re-emitting the same draft after a retry) collapse
  to the original row instead of creating a chain of cosmetic
  duplicates.
* **Single ACTIVE per pack** — :func:`activate_version` retires any
  current ACTIVE row inside the same transaction, so the unique state
  isn't enforced at the DB level (we can't easily express it without
  a partial unique index that doesn't survive downgrade) but is
  guaranteed at the service boundary.
* **Pack mirror sync** — when a version becomes ACTIVE the pack's
  ``content_md`` / ``content_hash`` columns get rewritten to mirror it.
  This keeps the existing read path
  (:func:`app.api.v1.skills_persistence.get_pack_content`) free of any
  version-aware code while the runtime read fast-path stays a single
  ``SELECT skill_packs.*``.
* **Audit on every transition** — five stable action keys land in
  ``audit_events``: ``skill_version.created``,
  ``skill_version.activated``, ``skill_version.retired``,
  ``skill_version.transitioned``, ``skill_version.rollback``.
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Mapping
from datetime import datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError, NotFound
from app.core.security import utcnow_naive
from app.db.models.skill_pack_version import SkillPackVersion, SkillPackVersionState
from app.db.models.skills import SkillPack
from app.repositories.skill_pack_version import SkillPackVersionRepository
from app.repositories.skills import SkillFileRepository, SkillPackRepository
from app.services import audit as audit_svc

__all__ = [
    "ALLOWED_VERSION_TRANSITIONS",
    "SkillPackVersionConflict",
    "SkillPackVersionTransitionError",
    "activate_version",
    "compute_content_hash",
    "create_version",
    "rollback_to_version",
    "transition_version",
]


# ── Errors ───────────────────────────────────────────────────
class SkillPackVersionConflict(AppError):
    """A proposal hash collides with a row already in the table."""

    code = "skill_version.duplicate_content_hash"
    default_status = 409


class SkillPackVersionTransitionError(AppError):
    """Edge not in :data:`ALLOWED_VERSION_TRANSITIONS`."""

    code = "skill_version.invalid_transition"
    default_status = 409


# ── State machine ────────────────────────────────────────────
ALLOWED_VERSION_TRANSITIONS: dict[SkillPackVersionState, set[SkillPackVersionState]] = {
    SkillPackVersionState.PROPOSED: {
        SkillPackVersionState.VALIDATING,
        SkillPackVersionState.REJECTED,
    },
    SkillPackVersionState.VALIDATING: {
        SkillPackVersionState.ACCEPTED,
        SkillPackVersionState.REJECTED,
    },
    SkillPackVersionState.ACCEPTED: {SkillPackVersionState.ACTIVE},
    SkillPackVersionState.ACTIVE: {SkillPackVersionState.RETIRED},
    SkillPackVersionState.RETIRED: set(),
    SkillPackVersionState.REJECTED: set(),
}


# ── Helpers ──────────────────────────────────────────────────
def compute_content_hash(content_md: str, files: Mapping[str, str] | None) -> str:
    """Stable, deterministic SHA-256 over body + sorted file map.

    Same inputs always produce the same hex digest so the dedup index
    can do its job. The ``files`` map is normalised by sorting on path
    before hashing so two callers passing equivalent dicts in different
    insertion orders converge on the same hash.
    """
    h = hashlib.sha256()
    h.update((content_md or "").encode("utf-8"))
    if files:
        h.update(b"\n--files--\n")
        for path in sorted(files.keys()):
            h.update(path.encode("utf-8"))
            h.update(b"\0")
            h.update(str(files[path]).encode("utf-8"))
            h.update(b"\n")
    return h.hexdigest()


async def _load_pack(
    db: AsyncSession, *, workspace_id: uuid.UUID, pack_id: uuid.UUID
) -> SkillPack:
    pack = await SkillPackRepository(db).get(pack_id, include_deleted=True)
    if pack is None or pack.workspace_id != workspace_id:
        raise NotFound("skill_pack_not_found", code="skill_pack.not_found")
    return pack


async def _load_version(
    db: AsyncSession, *, workspace_id: uuid.UUID, version_id: uuid.UUID
) -> SkillPackVersion:
    version = await SkillPackVersionRepository(db).get(version_id)
    if version is None or version.workspace_id != workspace_id:
        raise NotFound(
            "skill_pack_version_not_found",
            code="skill_version.not_found",
        )
    return version


def _check_edge(
    current: SkillPackVersionState, target: SkillPackVersionState
) -> None:
    allowed = ALLOWED_VERSION_TRANSITIONS.get(current, set())
    if target not in allowed:
        raise SkillPackVersionTransitionError(
            f"cannot transition {current.value} -> {target.value}",
            code="skill_version.invalid_transition",
            extras={
                "from": current.value,
                "to": target.value,
                "allowed": sorted(s.value for s in allowed),
            },
        )


# ── Authoring ────────────────────────────────────────────────
async def create_version(
    db: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    pack_id: uuid.UUID,
    content_md: str,
    files: Mapping[str, str] | None = None,
    created_by: str,
    creator_identity_id: uuid.UUID | None,
    source_run_ids: list[str] | None = None,
    request: Any = None,
) -> SkillPackVersion:
    """Insert a new ``PROPOSED`` snapshot.

    Raises :class:`SkillPackVersionConflict` if a row with the same
    ``content_hash`` already exists for the pack. Caller is
    responsible for ``await db.commit()``.
    """
    pack = await _load_pack(db, workspace_id=workspace_id, pack_id=pack_id)
    repo = SkillPackVersionRepository(db)

    files_dict: dict[str, str] = dict(files) if files else {}
    content_hash = compute_content_hash(content_md, files_dict)

    existing = await repo.find_by_hash(
        workspace_id=workspace_id, pack_id=pack.id, content_hash=content_hash
    )
    if existing is not None:
        raise SkillPackVersionConflict(
            "duplicate_content_hash",
            code="skill_version.duplicate_content_hash",
            extras={
                "pack_id": str(pack.id),
                "content_hash": content_hash,
                "existing_version_id": str(existing.id),
                "existing_version_no": existing.version_no,
            },
        )

    next_no = await repo.next_version_no(workspace_id=workspace_id, pack_id=pack.id)
    version = await repo.create(
        workspace_id=workspace_id,
        pack_id=pack.id,
        version_no=next_no,
        content_hash=content_hash,
        content_md=content_md or "",
        files_json=files_dict,
        state=SkillPackVersionState.PROPOSED,
        created_by=created_by,
        creator_identity_id=creator_identity_id,
        source_run_ids=list(source_run_ids or []),
        validation_results={},
    )

    await audit_svc.record(
        db,
        action="skill_version.created",
        actor_identity_id=creator_identity_id,
        workspace_id=workspace_id,
        resource_type="skill_pack_version",
        resource_id=version.id,
        summary=(
            f"created v{version.version_no} for skill pack {pack.slug!r}"
        ),
        metadata={
            "pack_id": str(pack.id),
            "slug": pack.slug,
            "version_no": version.version_no,
            "content_hash": content_hash,
            "created_by": created_by,
            "source_run_ids": list(source_run_ids or []),
        },
        request=request,
    )
    return version


# ── Activate ─────────────────────────────────────────────────
async def _retire_current_active(
    db: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    pack_id: uuid.UUID,
    superseded_by: SkillPackVersion,
    actor_identity_id: uuid.UUID | None,
    now: datetime,
    request: Any,
) -> SkillPackVersion | None:
    repo = SkillPackVersionRepository(db)
    current = await repo.get_active(workspace_id=workspace_id, pack_id=pack_id)
    if current is None or current.id == superseded_by.id:
        return None
    current.state = SkillPackVersionState.RETIRED
    current.retired_at = now
    current.superseded_by_version_id = superseded_by.id
    await db.flush([current])

    await audit_svc.record(
        db,
        action="skill_version.retired",
        actor_identity_id=actor_identity_id,
        workspace_id=workspace_id,
        resource_type="skill_pack_version",
        resource_id=current.id,
        summary=(
            f"retired v{current.version_no} (superseded by v{superseded_by.version_no})"
        ),
        metadata={
            "pack_id": str(pack_id),
            "version_no": current.version_no,
            "superseded_by_version_id": str(superseded_by.id),
            "superseded_by_version_no": superseded_by.version_no,
        },
        request=request,
    )
    return current


async def _mirror_pack_to_version(
    db: AsyncSession, *, pack: SkillPack, version: SkillPackVersion
) -> None:
    """Sync the pack's cache columns to the version about to go ACTIVE."""
    pack.content_hash = version.content_hash
    file_repo = SkillFileRepository(db)
    files = await file_repo.list_for_pack(
        workspace_id=pack.workspace_id, skill_pack_id=pack.id
    )
    skill_md = next((f for f in files if f.path == "SKILL.md"), None)
    if skill_md is None:
        await file_repo.create(
            workspace_id=pack.workspace_id,
            skill_pack_id=pack.id,
            path="SKILL.md",
            content_md=version.content_md,
        )
    elif skill_md.content_md != version.content_md:
        await file_repo.update(skill_md, content_md=version.content_md)
    await db.flush([pack])


async def activate_version(
    db: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    version_id: uuid.UUID,
    actor_identity_id: uuid.UUID | None,
    reason: str | None = None,
    request: Any = None,
) -> SkillPackVersion:
    """Mark ``version_id`` ACTIVE; retire the previous ACTIVE if any.

    Mirrors the version's ``content_md`` / ``content_hash`` onto the
    parent SkillPack so the existing read path keeps working without
    knowing about versions. Caller commits.
    """
    version = await _load_version(db, workspace_id=workspace_id, version_id=version_id)
    pack = await _load_pack(db, workspace_id=workspace_id, pack_id=version.pack_id)

    if version.state != SkillPackVersionState.ACTIVE:
        # Allow activation from any non-terminal state — service-level
        # rollback / "promote a freshly accepted draft" both legitimately
        # skip the linear ACCEPTED → ACTIVE step. The state machine is
        # only enforced via :func:`transition_version`; this entry point
        # is an explicit override path used by the activate endpoint.
        if version.state in {
            SkillPackVersionState.REJECTED,
        }:
            raise SkillPackVersionTransitionError(
                "cannot activate a rejected version",
                code="skill_version.invalid_transition",
                extras={"from": version.state.value, "to": "active"},
            )

    now = utcnow_naive()
    retired = await _retire_current_active(
        db,
        workspace_id=workspace_id,
        pack_id=pack.id,
        superseded_by=version,
        actor_identity_id=actor_identity_id,
        now=now,
        request=request,
    )

    version.state = SkillPackVersionState.ACTIVE
    version.activated_at = now
    version.superseded_by_version_id = None
    version.retired_at = None
    await db.flush([version])

    await _mirror_pack_to_version(db, pack=pack, version=version)

    await audit_svc.record(
        db,
        action="skill_version.activated",
        actor_identity_id=actor_identity_id,
        workspace_id=workspace_id,
        resource_type="skill_pack_version",
        resource_id=version.id,
        summary=(
            f"activated v{version.version_no} for skill pack {pack.slug!r}"
        ),
        metadata={
            "pack_id": str(pack.id),
            "slug": pack.slug,
            "version_no": version.version_no,
            "content_hash": version.content_hash,
            "previous_active_version_id": (
                str(retired.id) if retired is not None else None
            ),
            "previous_active_version_no": (
                retired.version_no if retired is not None else None
            ),
            "reason": reason,
        },
        request=request,
    )
    return version


# ── Generic transition ───────────────────────────────────────
async def transition_version(
    db: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    version_id: uuid.UUID,
    target_state: SkillPackVersionState,
    actor_identity_id: uuid.UUID | None,
    reason: str,
    request: Any = None,
) -> SkillPackVersion:
    """Move a version along the validation/rejection edges.

    For ``target_state == ACTIVE`` we delegate to
    :func:`activate_version` so the retire-previous semantics still
    fire; the audit row from this entrypoint reads
    ``skill_version.transitioned`` (with ``to=active``) plus the
    nested ``skill_version.activated`` row, which is intentional —
    the explicit transition feed shows the user-visible verb while the
    activated/retired feed shows the side effects.
    """
    version = await _load_version(db, workspace_id=workspace_id, version_id=version_id)
    current = version.state
    _check_edge(current, target_state)

    if target_state == SkillPackVersionState.ACTIVE:
        await audit_svc.record(
            db,
            action="skill_version.transitioned",
            actor_identity_id=actor_identity_id,
            workspace_id=workspace_id,
            resource_type="skill_pack_version",
            resource_id=version.id,
            summary=f"v{version.version_no} {current.value} -> active",
            metadata={
                "pack_id": str(version.pack_id),
                "version_no": version.version_no,
                "from": current.value,
                "to": target_state.value,
                "reason": reason,
            },
            request=request,
        )
        return await activate_version(
            db,
            workspace_id=workspace_id,
            version_id=version.id,
            actor_identity_id=actor_identity_id,
            reason=reason,
            request=request,
        )

    now = utcnow_naive()
    version.state = target_state
    if target_state == SkillPackVersionState.RETIRED:
        version.retired_at = now
    await db.flush([version])

    await audit_svc.record(
        db,
        action="skill_version.transitioned",
        actor_identity_id=actor_identity_id,
        workspace_id=workspace_id,
        resource_type="skill_pack_version",
        resource_id=version.id,
        summary=f"v{version.version_no} {current.value} -> {target_state.value}",
        metadata={
            "pack_id": str(version.pack_id),
            "version_no": version.version_no,
            "from": current.value,
            "to": target_state.value,
            "reason": reason,
        },
        request=request,
    )
    return version


# ── Rollback ─────────────────────────────────────────────────
async def rollback_to_version(
    db: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    pack_id: uuid.UUID,
    target_version_id: uuid.UUID,
    actor_identity_id: uuid.UUID | None,
    reason: str,
    request: Any = None,
) -> SkillPackVersion:
    """Re-promote a historical version to ACTIVE.

    Same machinery as :func:`activate_version`, plus an extra
    ``skill_version.rollback`` audit row so the operator history
    distinguishes a "I edited and saved" activate from a "I want
    last week's version back" rollback. M1.6 wires the verb endpoint;
    the service is callable from any service-layer caller today.
    """
    target = await _load_version(
        db, workspace_id=workspace_id, version_id=target_version_id
    )
    if target.pack_id != pack_id:
        raise NotFound(
            "skill_pack_version_not_found",
            code="skill_version.not_found",
        )

    activated = await activate_version(
        db,
        workspace_id=workspace_id,
        version_id=target.id,
        actor_identity_id=actor_identity_id,
        reason=reason,
        request=request,
    )

    await audit_svc.record(
        db,
        action="skill_version.rollback",
        actor_identity_id=actor_identity_id,
        workspace_id=workspace_id,
        resource_type="skill_pack_version",
        resource_id=activated.id,
        summary=f"rollback skill pack {pack_id} to v{activated.version_no}",
        metadata={
            "pack_id": str(pack_id),
            "target_version_id": str(activated.id),
            "target_version_no": activated.version_no,
            "reason": reason,
        },
        request=request,
    )
    return activated
