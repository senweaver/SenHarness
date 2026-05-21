"""SkillPack 9-state concept lifecycle (M1.1).

Single source of truth for every state transition on
:class:`~app.db.models.skills.SkillPack`. Every mutation that wants
to flip ``state``, ``pinned`` or move a pack to ``TOMBSTONE`` *must*
go through :func:`transition`, :func:`pin_pack` or
:func:`unpin_pack`; direct ORM updates skip the audit / pinned guard
and break the M1 acceptance criteria.

The state diagram (see also ``docs/skills.md`` → Concept lifecycle)::

    DRAFT ─→ CANDIDATE ─→ ACTIVE ─→ STALE ─→ ACTIVE
              │             │ │ │      │
              └→ REJECTED   │ │ │      └→ ARCHIVED ─→ ACTIVE
                  │         │ │ │              └→ TOMBSTONE
                  ↓         │ │ ↓
              TOMBSTONE     │ │ DEPRECATED ─→ ARCHIVED
                            │ ↓
                            │ SUPERSEDED ─→ ARCHIVED
                            ↓
                            PINNED ─→ ACTIVE   (manual unpin only)

Three hard rules enforced by this module:

1. **Edge whitelist** — :data:`ALLOWED_TRANSITIONS` is a plain dict;
   any target not in the source set raises
   :class:`InvalidStateTransition`. ``TOMBSTONE`` has no outgoing
   edges (terminal).
2. **Pinned exemption** — ``pinned=True`` packs are skipped by
   *automatic* transitions (curator / evolver / sweep). Callers from
   those flows pass ``bypass_pinned=False`` (the default) and catch
   :class:`PackPinnedAutoSkipped`. Manual user / admin actions pass
   ``bypass_pinned=True`` and proceed normally; pin / unpin
   themselves never change ``state``.
3. **Slug tombstoning** — moving a pack to ``TOMBSTONE`` writes a
   :class:`~app.db.models.tombstone_slug.TombstoneSlug` row. The
   create path on ``POST /skills/packs`` calls
   :func:`is_slug_tombstoned` and rejects with
   :class:`~app.core.errors.SlugTombstoned` so the slug can never be
   reused inside the same workspace (principle 10: "never delete,
   only archive — tombstones retain slug + content_hash for audit").
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Mapping
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError, NotFound
from app.core.security import utcnow_naive
from app.db.models.audit import AuditEvent
from app.db.models.skills import SkillPack, SkillPackState
from app.db.models.tombstone_slug import TombstoneSlug
from app.repositories.skills import SkillPackRepository
from app.services import audit as audit_svc

__all__ = [
    "ALLOWED_TRANSITIONS",
    "ActorKind",
    "InvalidStateTransition",
    "PackPinnedAutoSkipped",
    "TerminalStateError",
    "is_slug_tombstoned",
    "list_transitions",
    "pin_pack",
    "transition",
    "unpin_pack",
]


ActorKind = Literal["user", "curator", "system", "evolver"]


# ── State machine table ──────────────────────────────────────
ALLOWED_TRANSITIONS: dict[SkillPackState, set[SkillPackState]] = {
    SkillPackState.DRAFT: {
        SkillPackState.CANDIDATE,
        SkillPackState.ARCHIVED,
    },
    SkillPackState.CANDIDATE: {
        SkillPackState.ACTIVE,
        SkillPackState.REJECTED,
    },
    SkillPackState.ACTIVE: {
        SkillPackState.STALE,
        SkillPackState.PINNED,
        SkillPackState.SUPERSEDED,
        SkillPackState.DEPRECATED,
        SkillPackState.ARCHIVED,
    },
    SkillPackState.STALE: {
        SkillPackState.ACTIVE,
        SkillPackState.ARCHIVED,
        SkillPackState.PINNED,
    },
    # PINNED → ACTIVE is the only edge; the auto-flow guard sits on
    # the ``pinned`` flag, not on ``state == PINNED``.
    SkillPackState.PINNED: {SkillPackState.ACTIVE},
    SkillPackState.DEPRECATED: {SkillPackState.ARCHIVED},
    SkillPackState.SUPERSEDED: {SkillPackState.ARCHIVED},
    SkillPackState.ARCHIVED: {
        SkillPackState.ACTIVE,
        SkillPackState.TOMBSTONE,
    },
    SkillPackState.REJECTED: {SkillPackState.TOMBSTONE},
    SkillPackState.TOMBSTONE: set(),
}


# ── Errors ───────────────────────────────────────────────────
class InvalidStateTransition(AppError):
    """Edge not in :data:`ALLOWED_TRANSITIONS`."""

    code = "skill.invalid_transition"
    default_status = 409


class TerminalStateError(AppError):
    """Pack is in TOMBSTONE — no transitions allowed."""

    code = "skill.terminal_state"
    default_status = 409


class PackPinnedAutoSkipped(Exception):
    """Auto-flow tried to transition a pinned pack and was skipped.

    Raised in the service path; never surfaces as an HTTP response —
    background jobs (curator, evolver) catch this and proceed to the
    next pack. Manual actions pass ``bypass_pinned=True`` and never
    raise it.
    """

    def __init__(self, pack_id: uuid.UUID) -> None:
        super().__init__(f"pack {pack_id} is pinned; auto transition skipped")
        self.pack_id = pack_id


# ── Helpers ──────────────────────────────────────────────────
def _content_hash_or_blank(pack: SkillPack) -> str:
    """Return the pack's persisted content_hash or a stable fallback.

    A pack created before M1.2 has no version snapshot yet — fall back
    to ``sha256(pack.id)[:64]`` so the tombstone row always carries a
    non-null hash. The fallback is deterministic + workspace-scoped
    via the unique pack id, which is sufficient for the audit-only
    semantics of the column.
    """
    if pack.content_hash:
        return pack.content_hash
    return hashlib.sha256(str(pack.id).encode("utf-8")).hexdigest()


async def _get_pack_in_workspace(
    db: AsyncSession,
    *,
    pack_id: uuid.UUID,
    workspace_id: uuid.UUID,
) -> SkillPack:
    pack = await SkillPackRepository(db).get(pack_id, include_deleted=True)
    if pack is None or pack.workspace_id != workspace_id:
        raise NotFound("skill_pack_not_found", code="skill_pack.not_found")
    return pack


def _check_edge(current: SkillPackState, target: SkillPackState) -> None:
    if current == SkillPackState.TOMBSTONE:
        raise TerminalStateError(
            "pack already tombstoned",
            code="skill.terminal_state",
            extras={"current_state": current.value},
        )
    allowed = ALLOWED_TRANSITIONS.get(current, set())
    if target not in allowed:
        raise InvalidStateTransition(
            f"cannot transition {current.value} -> {target.value}",
            code="skill.invalid_transition",
            extras={
                "from": current.value,
                "to": target.value,
                "allowed": sorted(s.value for s in allowed),
            },
        )


# ── Transition ───────────────────────────────────────────────
async def transition(
    db: AsyncSession,
    *,
    pack_id: uuid.UUID,
    workspace_id: uuid.UUID,
    target_state: SkillPackState,
    actor_identity_id: uuid.UUID | None,
    reason: str,
    bypass_pinned: bool = False,
    actor_kind: ActorKind = "user",
    request: Any = None,
) -> SkillPack:
    """Move ``pack_id`` to ``target_state``.

    ``bypass_pinned=False`` (default) raises :class:`PackPinnedAutoSkipped`
    when the pack is currently pinned; auto sweeps catch and continue.
    ``bypass_pinned=True`` is reserved for explicit user / admin
    actions and proceeds even if the pack is pinned.

    Writes one ``skill.transitioned`` audit row; when target is
    ``TOMBSTONE`` an additional ``skill.tombstoned`` row is written
    *before* the state change so the slug-tombstone insert is
    visible in the audit feed even if the state update later rolls
    back. The caller is responsible for ``await db.commit()``.
    """
    pack = await _get_pack_in_workspace(db, pack_id=pack_id, workspace_id=workspace_id)
    current_state: SkillPackState = pack.state
    _check_edge(current_state, target_state)

    if pack.pinned and not bypass_pinned:
        # Auto-flow tried to move a pinned pack: write a single audit
        # row before raising so the curator / evolver feeds show the
        # skip without each caller having to audit on its own. The
        # caller's transaction is responsible for the commit; if it
        # rolls back this row goes with it (the count still survives
        # in the cron summary).
        await audit_svc.record(
            db,
            action="skill.transition_skipped_pinned",
            actor_identity_id=actor_identity_id,
            workspace_id=workspace_id,
            resource_type="skill_pack",
            resource_id=pack.id,
            summary=(
                f"auto transition skipped: pack {pack.slug!r} is pinned "
                f"({current_state.value} -> {target_state.value} requested by "
                f"{actor_kind})"
            ),
            metadata={
                "from": current_state.value,
                "to": target_state.value,
                "reason": reason,
                "actor_kind": actor_kind,
                "pack_id": str(pack.id),
                "slug": pack.slug,
            },
            request=request,
        )
        raise PackPinnedAutoSkipped(pack.id)

    metadata: dict[str, Any] = {
        "from": current_state.value,
        "to": target_state.value,
        "reason": reason,
        "actor_kind": actor_kind,
        "pack_id": str(pack.id),
        "slug": pack.slug,
    }

    if target_state == SkillPackState.TOMBSTONE:
        last_hash = _content_hash_or_blank(pack)
        # Tombstone first so the slug becomes unavailable atomically with
        # the state flip. The unique constraint on (workspace_id, slug)
        # gives us idempotency: a re-run of the transition (e.g. retry
        # after partial failure) collapses to the existing row.
        existing = (
            await db.execute(
                select(TombstoneSlug).where(
                    TombstoneSlug.workspace_id == workspace_id,
                    TombstoneSlug.slug == pack.slug,
                )
            )
        ).scalar_one_or_none()
        if existing is None:
            row = TombstoneSlug(
                workspace_id=workspace_id,
                slug=pack.slug,
                original_pack_id=pack.id,
                last_content_hash=last_hash,
            )
            db.add(row)
            await db.flush([row])
        await audit_svc.record(
            db,
            action="skill.tombstoned",
            actor_identity_id=actor_identity_id,
            workspace_id=workspace_id,
            resource_type="skill_pack",
            resource_id=pack.id,
            summary=f"tombstoned skill pack {pack.slug!r}",
            metadata={
                **metadata,
                "last_content_hash": last_hash,
            },
            request=request,
        )

    now = utcnow_naive()
    pack.state = target_state
    pack.state_changed_at = now
    pack.state_changed_by = actor_identity_id
    # Restore from ARCHIVED also clears the legacy ``deleted_at`` so the
    # repository ``list_for_workspace`` filter (still soft-delete-aware
    # until M1.7) doesn't keep the pack hidden after a successful
    # ``/restore``. The grandfather migration set ``state = 'archived'``
    # for every previously soft-deleted row; without this clear, those
    # rows would stay invisible after restore.
    if (
        current_state == SkillPackState.ARCHIVED
        and target_state == SkillPackState.ACTIVE
        and getattr(pack, "deleted_at", None) is not None
    ):
        pack.deleted_at = None
    await db.flush([pack])

    await audit_svc.record(
        db,
        action="skill.transitioned",
        actor_identity_id=actor_identity_id,
        workspace_id=workspace_id,
        resource_type="skill_pack",
        resource_id=pack.id,
        summary=(f"skill pack {pack.slug!r} {current_state.value} -> {target_state.value}"),
        metadata=metadata,
        request=request,
    )
    return pack


# ── Pin / unpin ──────────────────────────────────────────────
async def pin_pack(
    db: AsyncSession,
    *,
    pack_id: uuid.UUID,
    workspace_id: uuid.UUID,
    actor_identity_id: uuid.UUID | None,
    reason: str = "user pinned",
    request: Any = None,
) -> SkillPack:
    """Set ``pack.pinned=True``. Idempotent; does not change ``state``."""
    pack = await _get_pack_in_workspace(db, pack_id=pack_id, workspace_id=workspace_id)
    if pack.state == SkillPackState.TOMBSTONE:
        raise TerminalStateError(
            "pack already tombstoned",
            code="skill.terminal_state",
        )
    if pack.pinned:
        return pack
    pack.pinned = True
    await db.flush([pack])
    await audit_svc.record(
        db,
        action="skill.pinned",
        actor_identity_id=actor_identity_id,
        workspace_id=workspace_id,
        resource_type="skill_pack",
        resource_id=pack.id,
        summary=f"pinned skill pack {pack.slug!r}",
        metadata={"reason": reason, "pack_id": str(pack.id), "slug": pack.slug},
        request=request,
    )
    return pack


async def unpin_pack(
    db: AsyncSession,
    *,
    pack_id: uuid.UUID,
    workspace_id: uuid.UUID,
    actor_identity_id: uuid.UUID | None,
    reason: str = "user unpinned",
    request: Any = None,
) -> SkillPack:
    """Set ``pack.pinned=False``. Idempotent; does not change ``state``."""
    pack = await _get_pack_in_workspace(db, pack_id=pack_id, workspace_id=workspace_id)
    if not pack.pinned:
        return pack
    pack.pinned = False
    await db.flush([pack])
    await audit_svc.record(
        db,
        action="skill.unpinned",
        actor_identity_id=actor_identity_id,
        workspace_id=workspace_id,
        resource_type="skill_pack",
        resource_id=pack.id,
        summary=f"unpinned skill pack {pack.slug!r}",
        metadata={"reason": reason, "pack_id": str(pack.id), "slug": pack.slug},
        request=request,
    )
    return pack


# ── Read paths ───────────────────────────────────────────────
async def is_slug_tombstoned(db: AsyncSession, *, workspace_id: uuid.UUID, slug: str) -> bool:
    """Whether ``slug`` was previously tombstoned in ``workspace_id``."""
    stmt = select(TombstoneSlug.id).where(
        TombstoneSlug.workspace_id == workspace_id,
        TombstoneSlug.slug == slug,
    )
    return (await db.execute(stmt)).first() is not None


async def list_transitions(
    db: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    pack_id: uuid.UUID,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """Return ``skill.transitioned`` audit rows for ``pack_id``, newest first.

    Reads straight from ``audit_events`` so the history is whatever
    was recorded — no separate transition table to drift away from
    audit. Each row is shaped as
    ``{from_state, to_state, reason, actor_identity_id, actor_kind,
    occurred_at}`` for the API.
    """
    stmt = (
        select(AuditEvent)
        .where(
            AuditEvent.workspace_id == workspace_id,
            AuditEvent.action == "skill.transitioned",
            AuditEvent.resource_type == "skill_pack",
            AuditEvent.resource_id == pack_id,
        )
        .order_by(AuditEvent.created_at.desc())
        .limit(limit)
    )
    rows = (await db.execute(stmt)).scalars().all()
    out: list[dict[str, Any]] = []
    for ev in rows:
        meta: Mapping[str, Any] = ev.metadata_json or {}
        out.append(
            {
                "from_state": meta.get("from"),
                "to_state": meta.get("to"),
                "reason": meta.get("reason"),
                "actor_identity_id": ev.actor_identity_id,
                "actor_kind": meta.get("actor_kind"),
                "occurred_at": ev.created_at,
            }
        )
    return out
