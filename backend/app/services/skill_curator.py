"""Skill Curator service — candidate selection + archive proposals (M1.4).

The Curator runs as a daily ARQ cron (:func:`app.jobs.curator.curator_tick`)
and is **enabled by default** on every workspace (path Q4 of the
roadmap design decisions). It never directly archives a pack: stale
sweeps move ACTIVE → STALE through
:func:`app.services.skill_lifecycle.transition` (and naturally skip
pinned packs via :class:`PackPinnedAutoSkipped`); over-aged STALE packs
get an :class:`Approval` row with
``resource_type='skill_pack_archive'`` filed for admin review. Only
when an admin approves does
:func:`app.jobs.curator.curator_apply_approved` flip the pack to
ARCHIVED.

This module is the service layer for that flow. It owns:

* :func:`get_workspace_curator_config` — merges the platform default
  with ``workspace.home_config_json["curator"]``.
* :func:`find_stale_candidates` / :func:`find_archive_candidates` —
  pure read helpers; no side effects, idempotent.
* :func:`propose_archive` — builds the Approval row + audit
  ``curator.archive_proposed``.
* :func:`trigger_curator_now` — sync helper for the M1.9 admin
  "Force run curator now" button (defers to the ARQ task body).

The transition itself stays in :mod:`app.services.skill_lifecycle`;
this module is intentionally thin so M1.9 (admin UI) and the ARQ task
share one canonical knob source.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import utcnow_naive
from app.db.models.approval import (
    Approval,
    ApprovalResourceType,
    ApprovalStatus,
)
from app.db.models.skills import SkillPack, SkillPackState
from app.db.models.workspace import Workspace
from app.repositories.approval import ApprovalRepository
from app.services import audit as audit_svc
from app.services.system_settings import (
    CuratorDefaults,
    SystemSettingKey,
    get_system_setting,
)

log = logging.getLogger(__name__)

__all__ = [
    "ARCHIVE_PROPOSAL_TTL_DAYS",
    "CURATOR_ARCHIVE_PROPOSED",
    "CURATOR_NON_TOOL_NAME",
    "CURATOR_SWEPT",
    "CuratorConfig",
    "find_archive_candidates",
    "find_stale_candidates",
    "get_workspace_curator_config",
    "propose_archive",
    "trigger_curator_now",
]


# ── Constants ────────────────────────────────────────────────
# Approval TTL for ``skill_pack_archive`` rows; matches the
# Approval-TTL strategy table (7 days, default action = auto-archive).
# The TTL processor itself ships in M2.5; M1.4 only writes the column
# so admins can see "this proposal will auto-archive on YYYY-MM-DD".
ARCHIVE_PROPOSAL_TTL_DAYS = 7

# Non-tool approvals reuse the tool-call schema for transport but the
# legacy ``tool_name`` column is NOT NULL — write a stable sentinel
# instead of NULL so existing readers don't choke. The routing key for
# Curator approvals is :attr:`Approval.resource_type` (M1.4).
CURATOR_NON_TOOL_NAME = "_skill_pack_archive"

# Audit action keys (one-string-each — agreed with M1.4 brief).
CURATOR_SWEPT = "curator.swept"
CURATOR_ARCHIVE_PROPOSED = "curator.archive_proposed"


# ── Config ───────────────────────────────────────────────────
@dataclass(slots=True)
class CuratorConfig:
    """Resolved Curator knobs for a single workspace."""

    enabled: bool = True
    stale_after_days: int = 30
    archive_after_days: int = 90
    min_idle_hours: int = 24
    active_skills_soft_cap: int = 50

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> CuratorConfig:
        """Best-effort builder. Accepts a partial dict + tolerates noise.

        Unknown keys are dropped silently; out-of-range integers are
        clamped to the schema defaults so a malformed override can
        never make the sweep run unbounded queries.
        """
        defaults = cls()
        return cls(
            enabled=bool(raw.get("enabled", defaults.enabled)),
            stale_after_days=_coerce_int(
                raw.get("stale_after_days"),
                default=defaults.stale_after_days,
                lo=1,
                hi=3650,
            ),
            archive_after_days=_coerce_int(
                raw.get("archive_after_days"),
                default=defaults.archive_after_days,
                lo=1,
                hi=3650,
            ),
            min_idle_hours=_coerce_int(
                raw.get("min_idle_hours"),
                default=defaults.min_idle_hours,
                lo=0,
                hi=720,
            ),
            active_skills_soft_cap=_coerce_int(
                raw.get("active_skills_soft_cap"),
                default=defaults.active_skills_soft_cap,
                lo=1,
                hi=10000,
            ),
        )


def _coerce_int(value: Any, *, default: int, lo: int, hi: int) -> int:
    try:
        n = int(value)
    except (TypeError, ValueError):
        return default
    if n < lo:
        return lo
    if n > hi:
        return hi
    return n


async def get_workspace_curator_config(
    db: AsyncSession,
    *,
    workspace_id: uuid.UUID,
) -> CuratorConfig:
    """Resolve the effective Curator config for a workspace.

    Precedence: ``workspace.home_config_json["curator"]`` overrides the
    platform default in ``system_settings.curator_defaults`` (M1.4) +
    its :class:`CuratorDefaults` schema. Fields not present at either
    level fall back to :class:`CuratorConfig` defaults.
    """
    platform_raw = await get_system_setting(
        db,
        SystemSettingKey.CURATOR_DEFAULTS,
        default=None,
    )
    if isinstance(platform_raw, dict):
        merged = dict(platform_raw)
    else:
        merged = CuratorDefaults().model_dump()

    ws_row = (
        await db.execute(select(Workspace).where(Workspace.id == workspace_id))
    ).scalar_one_or_none()
    if ws_row is not None and isinstance(ws_row.home_config_json, dict):
        ws_overrides = ws_row.home_config_json.get("curator")
        if isinstance(ws_overrides, dict):
            merged.update({k: v for k, v in ws_overrides.items() if v is not None})

    return CuratorConfig.from_dict(merged)


# ── Candidate selection ─────────────────────────────────────
async def find_stale_candidates(
    db: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    stale_after_days: int,
    min_idle_hours: int,
    now: datetime | None = None,
    limit: int = 200,
) -> list[SkillPack]:
    """ACTIVE packs eligible to transition to STALE.

    Eligibility:

    * ``state == ACTIVE``
    * ``deleted_at IS NULL``
    * ``last_used_at < now - stale_after_days``
      (NULL ``last_used_at`` is treated as "never used" and counts as
      stale provided the pack itself is older than the same threshold —
      we use ``state_changed_at`` as the proxy when ``last_used_at`` is
      NULL)
    * ``last_used_at < now - min_idle_hours`` (or NULL — see above; the
      idle guard exists to dodge a freshly-used pack whose rollup
      hasn't landed yet, so NULL-last-used trivially satisfies it)

    Pinned packs are returned here unchanged because the pin filter
    sits on :func:`app.services.skill_lifecycle.transition` — keeping
    the read query simple makes the eligibility criteria easy to test
    in isolation; the curator then catches
    :class:`PackPinnedAutoSkipped` per pack.
    """
    cur = now or utcnow_naive()
    stale_cutoff = cur - timedelta(days=stale_after_days)
    idle_cutoff = cur - timedelta(hours=min_idle_hours)

    # When ``last_used_at`` is NULL we use ``state_changed_at`` as the
    # provenance timestamp so a never-touched pack still gets a
    # bounded "age" in the eligibility check. This avoids dragging in
    # ``created_at`` (M1.1 packs that were never used after being
    # ARCHIVED → ACTIVE restored should reset).
    age_proxy = SkillPack.state_changed_at
    stmt = (
        select(SkillPack)
        .where(
            SkillPack.workspace_id == workspace_id,
            SkillPack.deleted_at.is_(None),
            SkillPack.state == SkillPackState.ACTIVE,
            or_(
                and_(
                    SkillPack.last_used_at.is_not(None),
                    SkillPack.last_used_at < stale_cutoff,
                    SkillPack.last_used_at < idle_cutoff,
                ),
                and_(
                    SkillPack.last_used_at.is_(None),
                    or_(
                        age_proxy.is_(None),
                        age_proxy < stale_cutoff,
                    ),
                ),
            ),
        )
        .order_by(SkillPack.last_used_at.asc().nulls_first())
        .limit(limit)
    )
    return list((await db.execute(stmt)).scalars().all())


async def find_archive_candidates(
    db: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    archive_after_days: int,
    now: datetime | None = None,
    limit: int = 100,
) -> list[SkillPack]:
    """STALE packs eligible for archive proposal.

    Eligibility:

    * ``state == STALE``
    * ``deleted_at IS NULL``
    * ``state_changed_at < now - archive_after_days`` (the moment the
      pack was flipped into STALE — usually by a previous Curator
      tick; pinning a pack does not move it through STALE so this
      timestamp is a faithful "stale since" proxy)

    The function never *files* the proposal; it only returns
    candidates. The caller (:func:`app.jobs.curator.curator_tick`)
    enforces "no duplicate pending proposal exists" by passing each
    candidate to :func:`propose_archive`, which short-circuits on
    duplicates.
    """
    cur = now or utcnow_naive()
    stale_cutoff = cur - timedelta(days=archive_after_days)
    stmt = (
        select(SkillPack)
        .where(
            SkillPack.workspace_id == workspace_id,
            SkillPack.deleted_at.is_(None),
            SkillPack.state == SkillPackState.STALE,
            or_(
                SkillPack.state_changed_at.is_(None),
                SkillPack.state_changed_at < stale_cutoff,
            ),
        )
        .order_by(SkillPack.state_changed_at.asc().nulls_first())
        .limit(limit)
    )
    return list((await db.execute(stmt)).scalars().all())


# ── Approval proposal ───────────────────────────────────────
async def _has_pending_archive_approval(
    db: AsyncSession, *, workspace_id: uuid.UUID, pack_id: uuid.UUID
) -> bool:
    stmt = (
        select(Approval.id)
        .where(
            Approval.workspace_id == workspace_id,
            Approval.resource_type == ApprovalResourceType.SKILL_PACK_ARCHIVE.value,
            Approval.resource_id == pack_id,
            Approval.status == ApprovalStatus.PENDING,
        )
        .limit(1)
    )
    return (await db.execute(stmt)).first() is not None


async def propose_archive(
    db: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    pack: SkillPack,
    reason: str,
    use_count_30d: int = 0,
    actor_identity_id: uuid.UUID | None = None,
    request: Any = None,
    now: datetime | None = None,
) -> Approval | None:
    """File an archive proposal for ``pack`` and write an audit row.

    Idempotent: if a PENDING ``skill_pack_archive`` Approval already
    exists for this pack the function returns ``None`` and writes no
    new audit row. Otherwise it inserts an :class:`Approval` row with:

    * ``resource_type='skill_pack_archive'``,
      ``resource_id=pack.id``
    * ``tool_name=CURATOR_NON_TOOL_NAME`` (sentinel — see module
      docstring)
    * ``tool_args`` carries the structured proposal body (kind,
      pack_id, reason, stale_since, last_used_at, use_count_30d)
    * ``expires_at = now + 7 days`` (ARCHIVE_PROPOSAL_TTL_DAYS) so the
      M2.5 TTL processor can later auto-archive on expiry
    * ``session_id`` is left ``NULL`` — the M1.4 migration relaxed
      the column to nullable for non-tool approvals; legacy
      session-scoped queries simply miss this row.

    Caller commits the transaction.
    """
    if await _has_pending_archive_approval(db, workspace_id=workspace_id, pack_id=pack.id):
        return None

    cur = now or utcnow_naive()
    expires_at = cur + timedelta(days=ARCHIVE_PROPOSAL_TTL_DAYS)
    body: dict[str, Any] = {
        "kind": ApprovalResourceType.SKILL_PACK_ARCHIVE.value,
        "pack_id": str(pack.id),
        "slug": pack.slug,
        "reason": reason,
        "stale_since": (
            pack.state_changed_at.isoformat() if pack.state_changed_at is not None else None
        ),
        "last_used_at": (pack.last_used_at.isoformat() if pack.last_used_at is not None else None),
        "use_count_30d": int(use_count_30d),
    }

    summary = f"Curator proposes archiving stale skill pack {pack.slug!r} ({reason})"

    repo = ApprovalRepository(db)
    approval = await repo.create(
        workspace_id=workspace_id,
        session_id=None,
        agent_id=None,
        run_id=None,
        tool_name=CURATOR_NON_TOOL_NAME,
        tool_args=body,
        summary=summary,
        requested_by_identity_id=actor_identity_id,
        expires_at=expires_at,
        resource_type=ApprovalResourceType.SKILL_PACK_ARCHIVE.value,
        resource_id=pack.id,
    )

    await audit_svc.record(
        db,
        action=CURATOR_ARCHIVE_PROPOSED,
        actor_identity_id=actor_identity_id,
        workspace_id=workspace_id,
        resource_type="skill_pack",
        resource_id=pack.id,
        summary=summary,
        metadata={
            "approval_id": str(approval.id),
            "pack_id": str(pack.id),
            "slug": pack.slug,
            "reason": reason,
            "stale_after_days": None,
            "expires_at": expires_at.isoformat(),
            "ttl_days": ARCHIVE_PROPOSAL_TTL_DAYS,
            "use_count_30d": int(use_count_30d),
        },
        request=request,
    )

    return approval


# ── Public entry point reused by the M1.9 admin "Run now" button ───
async def trigger_curator_now(
    *,
    workspace_id: uuid.UUID,
) -> dict[str, Any]:
    """Run the Curator sweep for one workspace synchronously.

    Returns the same shape as one workspace's slice of the
    :func:`app.jobs.curator.curator_tick` summary so the M1.9 admin
    UI can render "Last run: handled N packs / M proposals" without a
    second round-trip. The implementation defers to the ARQ task body
    (kept in :mod:`app.jobs.curator` to avoid an import cycle: the
    job imports the service for its helpers, the service imports the
    job for the runtime sweep — the import sits inside the function
    so the cycle is broken at module load).
    """
    from app.jobs.curator import _curator_sweep_one_workspace

    return await _curator_sweep_one_workspace(workspace_id=workspace_id)
