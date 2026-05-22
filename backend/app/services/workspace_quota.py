"""Per-identity workspace creation quota + rate limit + tombstone (M0.12).

Three responsibilities, all behind one service module so the
``POST /workspaces`` / ``POST /auth/register`` / admin override paths
share the same source of truth:

1. **Effective limit resolution.** Each identity has a *source kind*
   (self-register, OAuth, admin-provisioned, invitation redeem). The
   platform setting ``workspace_quota`` carries one default per kind;
   ``Identity.workspace_quota_override`` overrides the default for a
   single identity. ``get_quota`` returns the merged
   :class:`QuotaStatus` snapshot the API surfaces to the frontend.

2. **Pre-flight checks.** ``check_can_create`` is the single entry
   point used by every workspace creation path. It honours the
   ``creation_allowed_for_self_registered`` toggle, the per-identity
   limit, and the rate window (creations + failed attempts inside
   ``creation_rate_period_seconds`` ≤ ``creation_rate_per_period``).
   Failures raise typed ``AppError`` subclasses so the route layer
   can return stable ``workspace.*`` codes and the frontend can map
   them to localized copy.

3. **Lifecycle hooks.** ``record_creation`` writes the audit log row
   that backs both quota counting and forensic review;
   ``release_on_delete`` marks the row(s) so the deleted workspace
   stops occupying a slot (when ``count_soft_deleted == False``);
   ``is_slug_tombstoned`` lets the slug allocator skip slugs whose
   workspace has since been deleted.

The service deliberately does **not** mutate
:class:`~app.db.models.workspace.Workspace` or call the workspace
service. ``DELETE /workspaces/{id}`` runs the slug-tombstone +
soft-delete update inline, then asks this module to flag the matching
``workspace_creation_logs`` row(s).
"""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from fastapi import Request
from sqlalchemy import and_, exists, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import (
    CreationNotPermitted,
    CreationRateLimited,
    PermissionDenied,
    QuotaExceeded,
)
from app.db.models.identity import Identity, IdentityStatus, PlatformRole
from app.db.models.membership import Membership, MembershipStatus
from app.db.models.role import BuiltinRole
from app.db.models.workspace import Workspace
from app.db.models.workspace_creation_log import (
    CreationKind,
    WorkspaceCreationLog,
)
from app.db.session import get_session_factory
from app.services import audit as audit_svc
from app.services.system_settings import (
    SystemSettingKey,
    WorkspaceQuotaSettings,
    get_system_setting,
)

log = logging.getLogger(__name__)


@dataclass(slots=True)
class QuotaStatus:
    """Snapshot of a single identity's workspace creation budget.

    ``used`` and ``remaining`` follow the platform settings ``count_only_owned_role``
    + ``count_soft_deleted``: by default we count *active workspaces* the
    identity owns and a deletion frees a slot immediately. ``creation_kind_allowed``
    is False for self-registered identities when the deployment opts not
    to let them create beyond their auto-provisioned personal workspace.
    """

    used: int
    limit: int
    remaining: int
    creation_kind_allowed: bool
    rate_window_used: int
    rate_window_limit: int
    rate_window_seconds: int
    source_kind: CreationKind
    override_active: bool
    grandfathered: bool


# ── Settings helper ──────────────────────────────────────────
async def _load_settings(db: AsyncSession) -> WorkspaceQuotaSettings:
    raw = await get_system_setting(db, SystemSettingKey.WORKSPACE_QUOTA, default={})
    if not isinstance(raw, dict):
        raw = {}
    try:
        return WorkspaceQuotaSettings(**raw)
    except Exception as exc:  # pragma: no cover - bad operator override
        log.warning("workspace_quota settings malformed (%s) — using defaults", exc)
        return WorkspaceQuotaSettings()


# ── Source kind inference ────────────────────────────────────
async def infer_source_kind(db: AsyncSession, identity: Identity) -> CreationKind:
    """Best-effort guess at how this identity originally entered the platform.

    Order of preference:

    1. Identity carries ``oauth_provider`` → :attr:`CreationKind.OAUTH_REGISTER`.
       This is durable; even if the user later sets a password the
       account remains "originally OAuth".
    2. Identity is a platform admin → :attr:`CreationKind.ADMIN_PROVISION`.
       Platform admins use the higher ``default_per_admin_created``
       budget regardless of how they signed up.
    3. Earliest ``workspace_creation_logs`` row for this identity wins
       — covers the case where an invitee redeemed a code first and
       was later promoted.
    4. Fallback :attr:`CreationKind.SELF_REGISTER` (the most
       conservative default, smallest quota).
    """
    if identity.oauth_provider:
        return CreationKind.OAUTH_REGISTER
    if identity.platform_role == PlatformRole.PLATFORM_ADMIN:
        return CreationKind.ADMIN_PROVISION

    stmt = (
        select(WorkspaceCreationLog.creation_kind)
        .where(WorkspaceCreationLog.identity_id == identity.id)
        .order_by(WorkspaceCreationLog.created_at.asc())
        .limit(1)
    )
    earliest = (await db.execute(stmt)).scalar_one_or_none()
    if earliest is not None:
        return earliest
    return CreationKind.SELF_REGISTER


def _default_for_kind(settings: WorkspaceQuotaSettings, kind: CreationKind) -> int:
    if kind == CreationKind.OAUTH_REGISTER:
        return settings.default_per_oauth
    if kind == CreationKind.ADMIN_PROVISION:
        return settings.default_per_admin_created
    if kind == CreationKind.INVITATION_REDEEM:
        return settings.default_per_invitation_redeem
    return settings.default_per_self_registered


# ── Counting ─────────────────────────────────────────────────
async def _count_used(
    db: AsyncSession,
    *,
    identity_id: uuid.UUID,
    settings: WorkspaceQuotaSettings,
) -> int:
    """How many workspaces currently count against this identity's quota.

    With ``count_only_owned_role`` the count is owner memberships;
    otherwise any active membership counts. ``count_soft_deleted``
    decides whether deleted workspaces still occupy a slot — default
    False matches the principle of "delete frees the slot".
    """
    stmt = (
        select(func.count(Membership.id))
        .join(Workspace, Workspace.id == Membership.workspace_id)
        .where(Membership.identity_id == identity_id)
        .where(Membership.status == MembershipStatus.ACTIVE)
        .where(Membership.deleted_at.is_(None))
    )
    if settings.count_only_owned_role:
        stmt = stmt.where(Membership.role == BuiltinRole.OWNER.value)
    if not settings.count_soft_deleted:
        stmt = stmt.where(Workspace.deleted_at.is_(None))
    return int((await db.execute(stmt)).scalar() or 0)


async def _count_recent_attempts(
    db: AsyncSession,
    *,
    identity_id: uuid.UUID,
    period_seconds: int,
) -> int:
    """Distinct creation log rows inside the rolling rate window.

    The rolling window is approximate (we use ``created_at >=
    now() - period``), but matches the fixed-window semantics of the
    existing rate_limit dependency. Failed attempts are recorded by
    :func:`check_can_create` so this counter alone reflects the full
    pressure.
    """
    from app.core.security import utcnow_naive

    cutoff = utcnow_naive() - timedelta(seconds=period_seconds)
    stmt = (
        select(func.count(WorkspaceCreationLog.id))
        .where(WorkspaceCreationLog.identity_id == identity_id)
        .where(WorkspaceCreationLog.created_at >= cutoff)
    )
    return int((await db.execute(stmt)).scalar() or 0)


# ── In-process rate window for transactional safety ──────────
# ``check_can_create`` runs inside the same SQLAlchemy session as the
# subsequent ``create_workspace`` + ``record_creation``. Until the
# transaction commits, repeated attempts in the same session would not
# yet observe each other through the DB-backed counter. We keep a
# best-effort in-memory ledger keyed on identity_id so a hammering
# loop inside one request lifecycle still trips the limit, and so the
# unit tests that exercise the counter without committing observe the
# failed attempts.
_attempt_ledger: dict[str, list[float]] = {}


def _ledger_count(identity_id: uuid.UUID, period_seconds: int) -> int:
    cutoff = time.time() - period_seconds
    bucket = _attempt_ledger.get(str(identity_id), [])
    fresh = [t for t in bucket if t >= cutoff]
    if len(fresh) != len(bucket):
        _attempt_ledger[str(identity_id)] = fresh
    return len(fresh)


def _ledger_record(identity_id: uuid.UUID) -> None:
    bucket = _attempt_ledger.setdefault(str(identity_id), [])
    bucket.append(time.time())


def reset_attempt_ledger() -> None:
    """Test hook — wipe the in-memory rate ledger between cases."""
    _attempt_ledger.clear()


async def _audit_failure(
    *,
    action: str,
    actor_identity_id: uuid.UUID,
    summary: str,
    metadata: dict[str, Any],
    request: Request | None,
) -> None:
    """Record a denied-attempt audit on a fresh session.

    Mirrors the pattern from ``auth.py`` for ``auth.login_failed``:
    the calling route is about to raise so its session will be rolled
    back. Using a fresh session keeps the audit row independent of
    the failed transaction.
    """
    factory = get_session_factory()
    try:
        async with factory() as db:
            await audit_svc.record(
                db,
                action=action,
                actor_identity_id=actor_identity_id,
                workspace_id=None,
                resource_type="identity",
                resource_id=actor_identity_id,
                summary=summary,
                metadata=metadata,
                request=request,
            )
            await db.commit()
    except Exception as exc:  # pragma: no cover - audit best-effort
        log.warning("workspace_quota audit %s failed: %s", action, exc)


# ── Public API ───────────────────────────────────────────────
async def get_quota(db: AsyncSession, *, identity_id: uuid.UUID) -> QuotaStatus:
    """Return the snapshot the ``GET /me/workspace-quota`` route serves."""
    identity = await db.get(Identity, identity_id)
    if identity is None:
        raise PermissionDenied("identity_missing", code="auth.no_identity")

    settings = await _load_settings(db)
    source_kind = await infer_source_kind(db, identity)
    default_limit = _default_for_kind(settings, source_kind)
    override = identity.workspace_quota_override
    limit = override if override is not None else default_limit
    used = await _count_used(db, identity_id=identity_id, settings=settings)
    remaining = max(0, limit - used)

    creation_allowed = True
    if (
        source_kind == CreationKind.SELF_REGISTER
        and not settings.creation_allowed_for_self_registered
    ):
        creation_allowed = False

    db_attempts = await _count_recent_attempts(
        db,
        identity_id=identity_id,
        period_seconds=settings.creation_rate_period_seconds,
    )
    ledger_attempts = _ledger_count(identity_id, settings.creation_rate_period_seconds)
    rate_used = max(db_attempts, ledger_attempts)

    grandfathered = override is not None and override > default_limit

    return QuotaStatus(
        used=used,
        limit=limit,
        remaining=remaining,
        creation_kind_allowed=creation_allowed,
        rate_window_used=rate_used,
        rate_window_limit=settings.creation_rate_per_period,
        rate_window_seconds=settings.creation_rate_period_seconds,
        source_kind=source_kind,
        override_active=override is not None,
        grandfathered=grandfathered,
    )


async def check_can_create(
    db: AsyncSession,
    *,
    identity_id: uuid.UUID,
    creation_kind: CreationKind,
    request: Request | None = None,
) -> QuotaStatus:
    """Raise the appropriate typed error if creation must be blocked.

    Returns the post-check :class:`QuotaStatus` so callers that want
    to surface ``remaining`` in the response don't need to re-query.

    Failure modes:

    * :class:`CreationNotPermitted` — the identity's source kind is
      gated off (``creation_allowed_for_self_registered`` False);
      audited as ``workspace.creation_not_permitted``.
    * :class:`CreationRateLimited` — too many recent attempts;
      audited as ``workspace.creation_rate_limited`` and recorded
      against the rate ledger so subsequent tries in the same window
      keep tripping.
    * :class:`QuotaExceeded` — limit reached; audited as
      ``workspace.quota_exceeded`` with the source kind in metadata
      so a platform admin can decide whether to bump the override.

    The ``ADMIN_PROVISION`` and ``INVITATION_REDEEM`` kinds skip the
    "not permitted" gate because those flows are always operator-
    driven; the limit check still runs.
    """
    status = await get_quota(db, identity_id=identity_id)

    # The ``creation_allowed_for_self_registered`` toggle gates *manual*
    # ``POST /workspaces`` only — auto-provisioned personal workspaces
    # (SELF_REGISTER / OAUTH_REGISTER kinds), invitation redeem, and
    # admin-driven provisioning all bypass it. Otherwise a deployment
    # that flipped the toggle off would also lock the welcome-tour
    # personal workspace and break first-run UX.
    if creation_kind == CreationKind.MANUAL and not status.creation_kind_allowed:
        await _audit_failure(
            action="workspace.creation_not_permitted",
            actor_identity_id=identity_id,
            summary="self-registered creation gated off",
            metadata={
                "source_kind": status.source_kind.value,
                "creation_kind": creation_kind.value,
            },
            request=request,
        )
        raise CreationNotPermitted(
            "self_registered_creation_disabled",
            code="workspace.creation_not_permitted",
            extras={
                "source_kind": status.source_kind.value,
            },
        )

    # Rate window: include this attempt before checking the budget so
    # the third attempt inside an hour always trips at the third try
    # rather than the fourth.
    _ledger_record(identity_id)
    new_attempt_count = max(
        status.rate_window_used + 1,
        _ledger_count(identity_id, status.rate_window_seconds),
    )
    if new_attempt_count > status.rate_window_limit:
        await _audit_failure(
            action="workspace.creation_rate_limited",
            actor_identity_id=identity_id,
            summary="creation rate limit hit",
            metadata={
                "rate_used": new_attempt_count,
                "rate_limit": status.rate_window_limit,
                "period_seconds": status.rate_window_seconds,
                "creation_kind": creation_kind.value,
            },
            request=request,
        )
        raise CreationRateLimited(
            "creation_rate_limit",
            code="workspace.creation_rate_limit",
            extras={
                "rate_limit": status.rate_window_limit,
                "period_seconds": status.rate_window_seconds,
            },
        )

    if status.remaining <= 0:
        await _audit_failure(
            action="workspace.quota_exceeded",
            actor_identity_id=identity_id,
            summary=f"quota {status.used}/{status.limit} exhausted",
            metadata={
                "used": status.used,
                "limit": status.limit,
                "source_kind": status.source_kind.value,
                "creation_kind": creation_kind.value,
                "override_active": status.override_active,
            },
            request=request,
        )
        await _emit_notification_safely(
            event_key="workspace.quota_exceeded",
            workspace_id=None,
            actor_identity_id=identity_id,
            target_identity_ids=[identity_id],
            cooldown_resource_id=str(identity_id),
            payload={
                "used": status.used,
                "limit": status.limit,
                "source_kind": status.source_kind.value,
                "creation_kind": creation_kind.value,
            },
            request=request,
        )
        raise QuotaExceeded(
            "workspace_quota_exceeded",
            code="workspace.quota_exceeded",
            extras={
                "used": status.used,
                "limit": status.limit,
                "source_kind": status.source_kind.value,
            },
        )
    return status


def _client_meta(request: Request | None) -> tuple[str | None, str | None]:
    if request is None:
        return None, None
    ip: str | None = None
    try:
        xff = request.headers.get("x-forwarded-for")
        if xff:
            ip = xff.split(",")[0].strip()
        elif request.client is not None:
            ip = request.client.host
    except Exception:  # pragma: no cover
        pass
    ua = request.headers.get("user-agent")
    if ua and len(ua) > 200:
        ua = ua[:200]
    return ip, ua


async def record_creation(
    db: AsyncSession,
    *,
    identity_id: uuid.UUID,
    workspace_id: uuid.UUID,
    creation_kind: CreationKind,
    request: Request | None = None,
    creation_source: str | None = None,
) -> WorkspaceCreationLog:
    """Insert the audit log row and emit the ``workspace.created`` audit.

    The caller owns the surrounding transaction; this function does
    not commit. ``creation_source`` defaults to a stable string per
    kind so the abuse-review UI shows e.g. "register" or "admin_console".
    """
    ip, ua = _client_meta(request)
    source = creation_source or _default_source(creation_kind)
    row = WorkspaceCreationLog(
        identity_id=identity_id,
        workspace_id=workspace_id,
        creation_kind=creation_kind,
        creation_source=source,
        ip_address=ip,
        user_agent=ua,
        soft_deleted_workspace=False,
    )
    db.add(row)
    await db.flush()

    await audit_svc.record(
        db,
        action="workspace.created",
        actor_identity_id=identity_id,
        workspace_id=workspace_id,
        resource_type="workspace",
        resource_id=workspace_id,
        summary=f"workspace created via {creation_kind.value}",
        metadata={
            "creation_kind": creation_kind.value,
            "creation_source": source,
        },
        request=request,
    )

    await _maybe_emit_spike(
        db=db,
        identity_id=identity_id,
        workspace_id=workspace_id,
        creation_kind=creation_kind,
        request=request,
    )
    return row


async def _maybe_emit_spike(
    *,
    db: AsyncSession,
    identity_id: uuid.UUID,
    workspace_id: uuid.UUID,
    creation_kind: CreationKind,
    request: Request | None,
) -> None:
    """Notify platform admins when an identity approaches its rate budget.

    Uses the platform-level ``spike_quota_ratio`` (default 0.8) so the
    operator can tune in M0.13 without code changes. The cooldown key
    is the identity id so a single noisy actor only generates one
    notification per dedup window even if they keep creating
    workspaces.
    """
    try:
        snapshot = await get_quota(db, identity_id=identity_id)
        ratio_setting = await _spike_ratio(db)
        if snapshot.rate_window_limit <= 0:
            return
        used_ratio = snapshot.rate_window_used / snapshot.rate_window_limit
        if used_ratio < ratio_setting:
            return
        from app.services import notification_events as notif_events

        await notif_events.emit_event(
            db,
            event_key="workspace.spike_detected",
            workspace_id=workspace_id,
            actor_identity_id=identity_id,
            cooldown_resource_id=str(identity_id),
            payload={
                "identity_id": str(identity_id),
                "rate_window_used": snapshot.rate_window_used,
                "rate_window_limit": snapshot.rate_window_limit,
                "rate_window_seconds": snapshot.rate_window_seconds,
                "creation_kind": creation_kind.value,
                "ratio": round(used_ratio, 3),
            },
            request=request,
        )
    except Exception:  # pragma: no cover - notification best-effort
        log.exception(
            "spike notification failed for identity=%s",
            identity_id,
        )


async def _spike_ratio(db: AsyncSession) -> float:
    try:
        from app.services.notification_events import (
            get_notification_settings,
        )

        defaults = await get_notification_settings(db)
        raw = float(defaults.get("spike_quota_ratio", 0.8))
        if 0.0 < raw <= 1.0:
            return raw
    except Exception:  # pragma: no cover - settings fall-through
        pass
    return 0.8


async def _emit_notification_safely(
    *,
    event_key: str,
    workspace_id: uuid.UUID | None,
    actor_identity_id: uuid.UUID | None,
    target_identity_ids: list[uuid.UUID] | None,
    cooldown_resource_id: str | None,
    payload: dict[str, Any],
    request: Request | None,
) -> None:
    """Emit a notification on a fresh session.

    Quota-failure paths run inside the route's session which is about
    to be rolled back. We mirror :func:`_audit_failure` and use a
    detached session so the notification row survives.
    """
    factory = get_session_factory()
    try:
        from app.services import notification_events as notif_events

        async with factory() as fresh:
            await notif_events.emit_event(
                fresh,
                event_key=event_key,
                workspace_id=workspace_id,
                actor_identity_id=actor_identity_id,
                target_identity_ids=target_identity_ids,
                cooldown_resource_id=cooldown_resource_id,
                payload=payload,
                request=request,
            )
            await fresh.commit()
    except Exception as exc:  # pragma: no cover - notification best-effort
        log.warning(
            "notification %s failed for identity=%s: %s",
            event_key,
            actor_identity_id,
            exc,
        )


def _default_source(kind: CreationKind) -> str:
    if kind == CreationKind.SELF_REGISTER:
        return "register"
    if kind == CreationKind.OAUTH_REGISTER:
        return "oauth_register"
    if kind == CreationKind.MANUAL:
        return "manual"
    if kind == CreationKind.INVITATION_REDEEM:
        return "invitation"
    if kind == CreationKind.ADMIN_PROVISION:
        return "admin_console"
    return kind.value


async def release_on_delete(
    db: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    actor_identity_id: uuid.UUID | None = None,
    request: Request | None = None,
) -> int:
    """Mark every creation log row for ``workspace_id`` as deleted.

    Returns the number of rows updated. A workspace's quota slot is
    freed by setting ``soft_deleted_workspace = True`` on every
    matching log row + writing one ``workspace.quota_freed`` audit so
    the platform admin sees the release in the audit feed. The actual
    workspace soft-delete + ``slug_tombstoned = True`` flip is the
    caller's responsibility.
    """
    stmt = (
        update(WorkspaceCreationLog)
        .where(WorkspaceCreationLog.workspace_id == workspace_id)
        .where(WorkspaceCreationLog.soft_deleted_workspace.is_(False))
        .values(soft_deleted_workspace=True)
        .returning(WorkspaceCreationLog.identity_id)
    )
    rows = list((await db.execute(stmt)).scalars().all())
    if rows:
        await audit_svc.record(
            db,
            action="workspace.quota_freed",
            actor_identity_id=actor_identity_id,
            workspace_id=workspace_id,
            resource_type="workspace",
            resource_id=workspace_id,
            summary=f"quota slot freed for {len(rows)} log row(s)",
            metadata={"freed_log_rows": len(rows)},
            request=request,
        )
    return len(rows)


async def is_slug_tombstoned(db: AsyncSession, *, slug: str) -> bool:
    """True if any past or present workspace with this slug is tombstoned.

    Tombstone state survives a workspace's later hard-delete via the
    M0.11 retention sweep; any matching tombstone row at any time
    keeps the slug locked. The personal-workspace allocator and the
    manual create route both consult this before accepting a slug.
    """
    stmt = select(
        exists().where(
            and_(
                Workspace.slug == slug,
                or_(
                    Workspace.slug_tombstoned.is_(True),
                    Workspace.deleted_at.is_not(None),
                ),
            )
        )
    )
    return bool((await db.execute(stmt)).scalar())


# ── Admin override ───────────────────────────────────────────
async def set_quota_override(
    db: AsyncSession,
    *,
    target_identity_id: uuid.UUID,
    quota: int | None,
    actor_identity_id: uuid.UUID,
    request: Request | None = None,
) -> Identity:
    """Platform-admin write to ``identities.workspace_quota_override``.

    ``quota=None`` clears the override (the identity reverts to the
    platform default for its source kind). Caller owns the transaction
    and is responsible for the platform-admin gate before invoking.
    """
    target = await db.get(Identity, target_identity_id)
    if target is None:
        raise PermissionDenied("identity_missing", code="identity.not_found")

    previous = target.workspace_quota_override
    target.workspace_quota_override = quota
    db.add(target)
    await db.flush()

    await audit_svc.record(
        db,
        action="workspace.quota_override_set",
        actor_identity_id=actor_identity_id,
        workspace_id=None,
        resource_type="identity",
        resource_id=target_identity_id,
        summary=(
            f"quota override {previous} → {quota}"
            if previous != quota
            else "quota override unchanged"
        ),
        metadata={
            "previous": previous,
            "current": quota,
        },
        request=request,
    )

    if quota is not None and (previous is None or quota > previous):
        try:
            from app.services import notification_events as notif_events

            await notif_events.emit_event(
                db,
                event_key="workspace.quota_increased",
                workspace_id=None,
                actor_identity_id=actor_identity_id,
                target_identity_ids=[target_identity_id],
                cooldown_resource_id=str(target_identity_id),
                payload={
                    "previous": previous,
                    "current": quota,
                    "identity_id": str(target_identity_id),
                },
                request=request,
            )
        except Exception:  # pragma: no cover
            log.exception(
                "notify workspace.quota_increased failed for identity=%s",
                target_identity_id,
            )
    return target


@dataclass(slots=True)
class AdminQuotaRow:
    """Row shape for ``GET /admin/workspace-quotas``."""

    identity_id: uuid.UUID
    email: str
    name: str
    status: IdentityStatus
    platform_role: PlatformRole
    source_kind: CreationKind
    used: int
    limit: int
    override: int | None


async def list_admin_quotas(
    db: AsyncSession,
    *,
    limit: int = 100,
    offset: int = 0,
    sort_by_usage: bool = True,
) -> list[AdminQuotaRow]:
    """Build the admin-table rows. O(N) where N = identities returned.

    Pagination is intentionally simple — the M0.13 admin UI handles
    cursor pagination at the page layer when the deployment grows
    past a few thousand identities.
    """
    settings = await _load_settings(db)
    stmt = (
        select(Identity)
        .where(Identity.deleted_at.is_(None))
        .order_by(Identity.created_at.desc())
        .offset(offset)
        .limit(limit)
    )
    identities = list((await db.execute(stmt)).scalars().all())

    rows: list[AdminQuotaRow] = []
    for ident in identities:
        kind = await infer_source_kind(db, ident)
        default_limit = _default_for_kind(settings, kind)
        eff = (
            ident.workspace_quota_override
            if ident.workspace_quota_override is not None
            else default_limit
        )
        used = await _count_used(db, identity_id=ident.id, settings=settings)
        rows.append(
            AdminQuotaRow(
                identity_id=ident.id,
                email=ident.email,
                name=ident.name,
                status=ident.status,
                platform_role=ident.platform_role,
                source_kind=kind,
                used=used,
                limit=eff,
                override=ident.workspace_quota_override,
            )
        )

    if sort_by_usage:
        rows.sort(key=lambda r: (r.used, r.limit), reverse=True)
    return rows


async def admin_quota_for_identity(
    db: AsyncSession,
    *,
    identity_id: uuid.UUID,
) -> AdminQuotaRow:
    """Single-row admin detail. Reuses :func:`get_quota` semantics."""
    ident = await db.get(Identity, identity_id)
    if ident is None:
        raise PermissionDenied("identity_missing", code="identity.not_found")
    settings = await _load_settings(db)
    kind = await infer_source_kind(db, ident)
    default_limit = _default_for_kind(settings, kind)
    eff = (
        ident.workspace_quota_override
        if ident.workspace_quota_override is not None
        else default_limit
    )
    used = await _count_used(db, identity_id=identity_id, settings=settings)
    return AdminQuotaRow(
        identity_id=ident.id,
        email=ident.email,
        name=ident.name,
        status=ident.status,
        platform_role=ident.platform_role,
        source_kind=kind,
        used=used,
        limit=eff,
        override=ident.workspace_quota_override,
    )


__all__ = [
    "AdminQuotaRow",
    "QuotaStatus",
    "admin_quota_for_identity",
    "check_can_create",
    "get_quota",
    "infer_source_kind",
    "is_slug_tombstoned",
    "list_admin_quotas",
    "record_creation",
    "release_on_delete",
    "reset_attempt_ledger",
    "set_quota_override",
]
