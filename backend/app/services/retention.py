"""GDPR retention cascade + physical purge (M0.11).

Two responsibilities:

1. **Cascade soft-delete** — when an identity or workspace is soft-deleted,
   propagate ``deleted_at = now()`` (or hard-delete, when the target table
   has no soft-delete column) to every row in the configured
   :data:`CASCADE_TARGETS` table set that the deleted scope owns. The
   propagation is *idempotent*: re-running for the same scope short-circuits
   because rows already carry a ``deleted_at`` value (or were physically
   removed on the previous pass).

2. **Physical purge** — for every soft-delete table whose retention window
   has elapsed, ``DELETE`` the row. Default ``physical_purge_enabled = False``
   keeps the cron in dry-run mode so the operator can compare candidate
   counts to expectations before flipping the toggle.

Design notes:

* The targets are a static whitelist (:data:`CASCADE_TARGETS`); table
  names never come from runtime input, which closes a SQL injection vector
  by construction. All other interpolations bind via parameters.
* Tables that are still on the roadmap (M0.12 ``workspace_creation_logs``)
  are listed but guarded by :func:`sqlalchemy.inspect`.has_table — the sweep
  silently skips a missing table so this module ships before its targets do.
* Audit metadata never embeds raw identity / workspace UUIDs. Use
  :func:`scope_id_hash` to write a 16-hex prefix that is stable across
  sweep ticks but unlinkable without the original UUID.
"""

from __future__ import annotations

import hashlib
import re
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.core.security import utcnow_naive
from app.services.system_settings import (
    RetentionSettings,
    SystemSettingKey,
    get_system_setting,
)

__all__ = [
    "CASCADE_TARGETS",
    "SCOPE_IDENTITY",
    "SCOPE_WORKSPACE",
    "CascadeTarget",
    "IdentityJoin",
    "PurgeReport",
    "cascade_for_identity",
    "cascade_for_workspace",
    "get_retention_settings",
    "physically_purge_expired",
    "scope_id_hash",
    "select_pending_identities",
    "select_pending_workspaces",
]


# Whitelist regex applied to every identifier we splice into a SQL
# string (table name, column name, FK column). The whitelist is
# defence-in-depth — every value already comes from a frozen dataclass
# in this module — but it stops a future contributor from accidentally
# wiring user input into one of these constants.
_IDENT_OK = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")


def _safe_ident(value: str) -> str:
    if not _IDENT_OK.match(value):  # pragma: no cover - defence in depth
        raise ValueError(f"unsafe identifier: {value!r}")
    return value


# ── CASCADE_TARGETS ───────────────────────────────────────────
@dataclass(frozen=True)
class IdentityJoin:
    """How to walk from a target row back to its owning identity.

    Used when the target table doesn't carry ``identity_id`` directly
    (e.g. ``goal_alignment_scores`` is owned via ``session_goals``).
    Both the parent table and column names are validated against the
    :data:`_IDENT_OK` whitelist before being interpolated into SQL.
    """

    parent_table: str
    target_fk_column: str
    parent_identity_column: str = "identity_id"


@dataclass(frozen=True)
class CascadeTarget:
    """One table the retention sweep knows how to cascade.

    ``soft_delete=False`` means the table has no ``deleted_at`` column;
    cascade therefore physically deletes the row on the spot (these are
    typically short-lived audit rows where soft-delete brings no value).

    ``identity_join`` is set when the cascade has to walk a parent row
    to find the right identity scope. Workspace cascade always uses
    the column ``workspace_id`` so no equivalent join descriptor exists.

    ``retention_days_override_key`` is the key under
    :class:`~app.services.system_settings.RetentionSettings.per_table_days`
    that overrides :class:`~app.services.system_settings.RetentionSettings.default_days`
    for this table. ``None`` means use the default verbatim.
    """

    table_name: str
    soft_delete: bool
    workspace_scoped: bool
    identity_scoped: bool
    identity_join: IdentityJoin | None = None
    retention_days_override_key: str | None = None


CASCADE_TARGETS: tuple[CascadeTarget, ...] = (
    # M0.1 — locked goals + per-message alignment scores.
    # ``session_goals.locked_by`` is the identity FK, not ``identity_id``;
    # treat the goal as identity-scoped via that column using a self-join
    # descriptor so the executor falls into the join path.
    CascadeTarget(
        table_name="session_goals",
        soft_delete=True,
        workspace_scoped=True,
        identity_scoped=True,
        identity_join=IdentityJoin(
            parent_table="session_goals",
            target_fk_column="id",
            parent_identity_column="locked_by",
        ),
    ),
    CascadeTarget(
        table_name="goal_alignment_scores",
        soft_delete=False,
        workspace_scoped=True,
        identity_scoped=True,
        identity_join=IdentityJoin(
            parent_table="session_goals",
            target_fk_column="session_goal_id",
            parent_identity_column="locked_by",
        ),
    ),
    # M0.2 — folded run artifacts.
    CascadeTarget(
        table_name="session_artifacts",
        soft_delete=True,
        workspace_scoped=True,
        identity_scoped=True,
    ),
    # M0.3 — judge verdicts. Table may not exist yet (subagent in-flight).
    CascadeTarget(
        table_name="judge_verdicts",
        soft_delete=True,
        workspace_scoped=True,
        identity_scoped=True,
        identity_join=IdentityJoin(
            parent_table="session_artifacts",
            target_fk_column="artifact_id",
        ),
    ),
    # M0.7 — pending memory candidates. Carries ``deleted_at`` (mirror of
    # the SoftDeleteMixin shape), so the cascade soft-deletes the row and
    # the daily purge eventually hard-deletes it. Per-table retention
    # defaults to 30 days but the override key lets compliance shorten
    # this aggressively (the queue is short-lived by design).
    CascadeTarget(
        table_name="pending_memories",
        soft_delete=True,
        workspace_scoped=True,
        identity_scoped=True,
        retention_days_override_key="pending_memories",
    ),
    # M0.9 — opaque email-verification tokens (identity-only).
    CascadeTarget(
        table_name="email_verification_tokens",
        soft_delete=False,
        workspace_scoped=False,
        identity_scoped=True,
        retention_days_override_key="email_verification_tokens",
    ),
    # M0.12 — workspace creation log (identity-only). Table may not exist.
    CascadeTarget(
        table_name="workspace_creation_logs",
        soft_delete=False,
        workspace_scoped=False,
        identity_scoped=True,
        retention_days_override_key="workspace_creation_logs",
    ),
    # M1.3 — per-event skill usage telemetry. ``identity_id`` is direct
    # so no parent join is required. The table has no soft-delete
    # column; cascade hard-deletes the matching rows. Pre-M1.3
    # deployments are protected by the ``_table_exists`` guard upstream
    # so the entry stays inert until 0045 lands.
    CascadeTarget(
        table_name="skill_usage",
        soft_delete=False,
        workspace_scoped=True,
        identity_scoped=True,
        retention_days_override_key="skill_usage",
    ),
    # M3.6 — cross-platform logical thread + binding. ``identity_id`` is
    # direct on ``logical_threads`` so no join is required. Bindings
    # are anchored on the parent thread for the identity cascade
    # (workspace cascade walks ``workspace_id`` directly). Both tables
    # are guarded by ``_table_exists`` so deployments older than 0054
    # observe zero behaviour change.
    CascadeTarget(
        table_name="logical_threads",
        soft_delete=True,
        workspace_scoped=True,
        identity_scoped=True,
    ),
    CascadeTarget(
        table_name="thread_channel_bindings",
        soft_delete=True,
        workspace_scoped=True,
        identity_scoped=True,
        identity_join=IdentityJoin(
            parent_table="logical_threads",
            target_fk_column="thread_id",
        ),
    ),
    # M3.4 — per-agent profile (strengths / failure modes / cross-
    # workspace stats). Workspace cascade only — the row anchors on
    # the agent rather than any specific identity. ``has_table``
    # guard keeps the entry inert on pre-0055 deployments.
    CascadeTarget(
        table_name="agent_profiles",
        soft_delete=True,
        workspace_scoped=True,
        identity_scoped=False,
    ),
    # M3.7 — Honcho-style 12-dim dialectic user model. Both axes
    # cascade (workspace delete wipes every fact in the tenant;
    # identity delete wipes the per-user model across workspaces).
    # ``identity_id`` is direct so no parent join is required.
    # ``has_table`` guard keeps the entry inert on pre-0056
    # deployments.
    CascadeTarget(
        table_name="user_profile_facts",
        soft_delete=True,
        workspace_scoped=True,
        identity_scoped=True,
    ),
    # M3.3 — workspace ↔ hub-pack subscription. Workspace cascade
    # only — the row is keyed on ``workspace_id`` directly. No
    # ``deleted_at`` column on the subscription (M3.1 keeps it
    # minimal) so the cascade hard-deletes the matching rows.
    # ``has_table`` guard keeps the entry inert on pre-0053
    # deployments.
    CascadeTarget(
        table_name="workspace_hub_subscriptions",
        soft_delete=False,
        workspace_scoped=True,
        identity_scoped=False,
    ),
    # M4.2 — skill lineage edges. Workspace-scoped only (the row is
    # truth-of-record for *that* workspace's graph; an identity delete
    # leaves the lineage intact because the edge belongs to the pack,
    # not the user). No ``deleted_at`` column — cascade hard-deletes.
    # ``has_table`` guard keeps the entry inert on pre-0058 deployments.
    CascadeTarget(
        table_name="skill_lineage_edges",
        soft_delete=False,
        workspace_scoped=True,
        identity_scoped=False,
    ),
    # M4.4 — project kanban: boards live at workspace (or squad) scope
    # and don't anchor on any single identity. Cards inherit the same
    # workspace-only cascade — an identity soft-delete leaves the card
    # in place with ``assignee_identity_id`` already nulled by the FK
    # ``ON DELETE SET NULL``. Both tables carry ``deleted_at`` so the
    # daily physical purge eventually hard-deletes archived rows past
    # their retention window. ``has_table`` guards keep the entries
    # inert on pre-0061 deployments.
    CascadeTarget(
        table_name="project_boards",
        soft_delete=True,
        workspace_scoped=True,
        identity_scoped=False,
    ),
    CascadeTarget(
        table_name="board_cards",
        soft_delete=True,
        workspace_scoped=True,
        identity_scoped=False,
    ),
)


# Public scope kinds — string constants kept in sync with the
# :class:`~app.db.models.retention_watermark.RetentionScopeKind` enum
# so callers can spell them without importing the model.
SCOPE_IDENTITY = "identity"
SCOPE_WORKSPACE = "workspace"


# ── Helpers ───────────────────────────────────────────────────
def scope_id_hash(uid: uuid.UUID | str) -> str:
    """SHA-256 prefix used in audit metadata.

    The hash is stable per UUID so an operator can correlate two audit
    rows ("cascade started" + "cascade finished") that touched the same
    scope without ever leaking the original identity / workspace UUID
    into the audit feed.
    """
    return hashlib.sha256(str(uid).encode("utf-8")).hexdigest()[:16]


async def get_retention_settings(db: AsyncSession) -> RetentionSettings:
    """Read the current platform retention policy.

    Returns the model defaults whenever the row is absent so a fresh
    deployment behaves identically to the documented contract.
    """
    raw = await get_system_setting(
        db, SystemSettingKey.RETENTION, default=None
    )
    if raw is None:
        return RetentionSettings()
    if isinstance(raw, RetentionSettings):
        return raw
    if isinstance(raw, dict):
        try:
            return RetentionSettings.model_validate(raw)
        except Exception:
            return RetentionSettings()
    return RetentionSettings()


def _retention_days_for(
    target: CascadeTarget, settings: RetentionSettings
) -> int:
    if target.retention_days_override_key:
        explicit = settings.per_table_days.get(
            target.retention_days_override_key
        )
        if explicit is not None:
            return int(explicit)
    explicit = settings.per_table_days.get(target.table_name)
    if explicit is not None:
        return int(explicit)
    return settings.default_days


def _has_table(sync_session: Session, table_name: str) -> bool:
    return inspect(sync_session.connection()).has_table(table_name)


async def _table_exists(db: AsyncSession, table_name: str) -> bool:
    return bool(
        await db.run_sync(lambda sync_session: _has_table(sync_session, table_name))
    )


# ── Cascade ───────────────────────────────────────────────────
async def cascade_for_identity(
    db: AsyncSession, *, identity_id: uuid.UUID
) -> dict[str, int]:
    """Soft-delete (or physically delete) every identity-scoped row.

    Idempotent: running twice for the same identity yields zero rows on
    the second pass because the soft-delete predicate already excludes
    rows whose ``deleted_at`` is not NULL.

    Returns a ``{table_name: rows_affected}`` dict. Tables whose target
    is not present (M0.3 / M0.7 / M0.12 not yet migrated) are omitted
    entirely from the output rather than reported as zero so the
    operator can tell "skipped" apart from "swept clean".
    """
    affected: dict[str, int] = {}
    for target in CASCADE_TARGETS:
        if not target.identity_scoped:
            continue
        if not await _table_exists(db, target.table_name):
            continue
        # The identity_join target may also be missing on an older
        # deployment (e.g. ``thread_channel_bindings`` shipping before
        # ``logical_threads`` would never happen, but the guard is
        # cheap and matches the existing contract).
        if target.identity_join is not None:
            if not await _table_exists(db, target.identity_join.parent_table):
                continue
        affected[target.table_name] = await _cascade_one(
            db,
            target=target,
            scope=SCOPE_IDENTITY,
            scope_id=identity_id,
        )
    return affected


async def cascade_for_workspace(
    db: AsyncSession, *, workspace_id: uuid.UUID
) -> dict[str, int]:
    """Soft-delete (or physically delete) every workspace-scoped row.

    Identity-only tables (``email_verification_tokens``,
    ``workspace_creation_logs``) are intentionally skipped here — those
    rows survive a workspace deletion because the issuing identity may
    still be active in another tenant.
    """
    affected: dict[str, int] = {}
    for target in CASCADE_TARGETS:
        if not target.workspace_scoped:
            continue
        if not await _table_exists(db, target.table_name):
            continue
        affected[target.table_name] = await _cascade_one(
            db,
            target=target,
            scope=SCOPE_WORKSPACE,
            scope_id=workspace_id,
        )
    return affected


async def _cascade_one(
    db: AsyncSession,
    *,
    target: CascadeTarget,
    scope: str,
    scope_id: uuid.UUID,
) -> int:
    """Execute the cascade for exactly one ``(target, scope)`` pair."""
    table = _safe_ident(target.table_name)
    bind: dict[str, Any] = {"now": utcnow_naive(), "scope_id": str(scope_id)}

    if scope == SCOPE_WORKSPACE:
        if target.soft_delete:
            sql = text(
                f"UPDATE {table} SET deleted_at = :now "
                "WHERE workspace_id = CAST(:scope_id AS UUID) "
                "AND deleted_at IS NULL"
            )
        else:
            sql = text(
                f"DELETE FROM {table} "
                "WHERE workspace_id = CAST(:scope_id AS UUID)"
            )
        result = await db.execute(sql, bind)
        return int(result.rowcount or 0)

    # Identity scope.
    if target.identity_join is not None:
        join = target.identity_join
        parent = _safe_ident(join.parent_table)
        fk_col = _safe_ident(join.target_fk_column)
        parent_col = _safe_ident(join.parent_identity_column)
        if target.soft_delete:
            # ``parent IS target`` short-circuit: the goal table is its
            # own parent; we still go through the join path so the FK
            # column may legitimately equal ``id`` and the SQL stays
            # symmetric across targets.
            sql = text(
                f"UPDATE {table} SET deleted_at = :now "
                f"WHERE {fk_col} IN ("
                f"  SELECT p.id FROM {parent} p "
                f"  WHERE p.{parent_col} = CAST(:scope_id AS UUID)"
                f") AND deleted_at IS NULL"
            )
        else:
            sql = text(
                f"DELETE FROM {table} "
                f"WHERE {fk_col} IN ("
                f"  SELECT p.id FROM {parent} p "
                f"  WHERE p.{parent_col} = CAST(:scope_id AS UUID)"
                f")"
            )
    else:
        if target.soft_delete:
            sql = text(
                f"UPDATE {table} SET deleted_at = :now "
                "WHERE identity_id = CAST(:scope_id AS UUID) "
                "AND deleted_at IS NULL"
            )
        else:
            sql = text(
                f"DELETE FROM {table} "
                "WHERE identity_id = CAST(:scope_id AS UUID)"
            )
    result = await db.execute(sql, bind)
    return int(result.rowcount or 0)


# ── Physical purge ────────────────────────────────────────────
@dataclass
class PurgeReport:
    """Outcome of one physical-purge pass per table."""

    table_name: str
    candidates: int
    deleted: int
    cutoff: datetime | None
    skipped_reason: str | None = None


async def physically_purge_expired(
    db: AsyncSession, *, dry_run: bool
) -> dict[str, PurgeReport]:
    """For every soft-delete cascade target, drop expired rows.

    The cutoff is ``now() - retention_days`` where ``retention_days`` is
    pulled from :class:`RetentionSettings` (per-table override first,
    then ``default_days``).

    ``dry_run=True`` only counts candidates and never issues a DELETE.
    """
    settings = await get_retention_settings(db)
    out: dict[str, PurgeReport] = {}
    now = utcnow_naive()
    for target in CASCADE_TARGETS:
        table = _safe_ident(target.table_name)
        if not target.soft_delete:
            out[table] = PurgeReport(
                table_name=table,
                candidates=0,
                deleted=0,
                cutoff=None,
                skipped_reason="no_soft_delete_column",
            )
            continue
        if not await _table_exists(db, table):
            out[table] = PurgeReport(
                table_name=table,
                candidates=0,
                deleted=0,
                cutoff=None,
                skipped_reason="table_missing",
            )
            continue
        days = _retention_days_for(target, settings)
        cutoff = now - timedelta(days=days)

        cnt_sql = text(
            f"SELECT COUNT(*) FROM {table} "
            "WHERE deleted_at IS NOT NULL AND deleted_at < :cutoff"
        )
        candidates = int(
            (await db.execute(cnt_sql, {"cutoff": cutoff})).scalar() or 0
        )
        deleted = 0
        if not dry_run and candidates > 0:
            del_sql = text(
                f"DELETE FROM {table} "
                "WHERE deleted_at IS NOT NULL AND deleted_at < :cutoff"
            )
            res = await db.execute(del_sql, {"cutoff": cutoff})
            deleted = int(res.rowcount or 0)
        out[table] = PurgeReport(
            table_name=table,
            candidates=candidates,
            deleted=deleted,
            cutoff=cutoff,
        )
    return out


# ── Watermark sweep helpers ───────────────────────────────────
async def select_pending_identities(
    db: AsyncSession,
    *,
    after: datetime,
    limit: int,
) -> Sequence[tuple[uuid.UUID, datetime]]:
    """Return identity ids whose ``deleted_at > after``, ASC by ``deleted_at``."""
    rows = (
        await db.execute(
            text(
                "SELECT id, deleted_at FROM identities "
                "WHERE deleted_at IS NOT NULL AND deleted_at > :after "
                "ORDER BY deleted_at ASC, id ASC LIMIT :limit"
            ),
            {"after": after, "limit": limit},
        )
    ).all()
    return [(row[0], row[1]) for row in rows]


async def select_pending_workspaces(
    db: AsyncSession,
    *,
    after: datetime,
    limit: int,
) -> Sequence[tuple[uuid.UUID, datetime]]:
    rows = (
        await db.execute(
            text(
                "SELECT id, deleted_at FROM workspaces "
                "WHERE deleted_at IS NOT NULL AND deleted_at > :after "
                "ORDER BY deleted_at ASC, id ASC LIMIT :limit"
            ),
            {"after": after, "limit": limit},
        )
    ).all()
    return [(row[0], row[1]) for row in rows]
