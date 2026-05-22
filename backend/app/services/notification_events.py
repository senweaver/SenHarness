"""Event registry + fan-out for the notification pipeline (M0.10).

The audit log already captures every state transition machine-readably.
This module turns the **user-visible** subset of those transitions into
something a human will see — an in-app bell badge, an email when the
event is operationally critical, eventually IM messages — without
forcing each call site to know about user preferences, cooldowns, or
target-audience resolution.

Architecture choice: synchronous fan-out at the audit point, no audit
feed consumer. Lower latency, simpler reasoning, and at our M0 scale
the cost of resolving the target identity list is dominated by the DB
round-trips already happening in the request. A future Redis
pub/sub-backed dispatcher can drop in behind :func:`emit_event`
without touching the call sites.

The 13 keys in :data:`EVENT_REGISTRY` cover the six categories listed
in the M0.10 design (goal alignment, goal lock/unlock, judge negative,
channel sender blocked, register-provision welcome, channel signature
failed) plus the operational events that fell out of M0.3 (judge
breaker degraded), M0.12 (quota exceeded / spike / increased), the
ARQ permanent-failure backstop, and the M2.5 ``approval.expiring``
placeholder so the schema is ready when approvals land.
"""

from __future__ import annotations

import hashlib
import logging
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Literal, cast

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings as app_settings
from app.db.models.identity import Identity, IdentityStatus, PlatformRole
from app.db.models.membership import Membership, MembershipStatus
from app.db.models.notification import NotificationLevel
from app.db.models.role import BuiltinRole
from app.db.models.workspace import Workspace
from app.services import audit as audit_svc
from app.services import notifications as notif_svc
from app.services.system_settings import (
    SystemSettingKey,
    get_system_setting,
)

log = logging.getLogger(__name__)


# ── Channel + urgency enums ─────────────────────────────────
class NotificationChannel(StrEnum):
    """Delivery channel a single fan-out can hit.

    IM channels (Slack/Discord/Lark/Feishu) are deliberately out of
    scope until M2.5 — adding them would require per-workspace IM
    routing config that doesn't exist yet.
    """

    IN_APP = "in_app"
    EMAIL = "email"


class NotificationUrgency(StrEnum):
    """Severity hint mapped onto :class:`NotificationLevel` for the bell row."""

    INFO = "info"
    WARN = "warn"
    CRITICAL = "critical"


_URGENCY_TO_LEVEL: dict[NotificationUrgency, NotificationLevel] = {
    NotificationUrgency.INFO: NotificationLevel.INFO,
    NotificationUrgency.WARN: NotificationLevel.WARNING,
    NotificationUrgency.CRITICAL: NotificationLevel.ERROR,
}


# ── Registry data class ─────────────────────────────────────
TargetAudience = Literal[
    "actor",
    "owner",
    "workspace_admins",
    "platform_admins",
    "broadcast",
]


@dataclass(frozen=True, slots=True)
class EventDescriptor:
    """Static metadata for one notification event key.

    ``cooldown_seconds`` of zero disables dedup entirely (rare events
    that admins must always see). ``requires_email`` is the platform
    override: when True, end users cannot turn off the email channel
    via personal preferences — only platform admins can suppress these
    by editing :data:`SystemSettingKey.NOTIFICATION_DEFAULTS`.
    """

    key: str
    default_channels: tuple[NotificationChannel, ...]
    default_urgency: NotificationUrgency
    cooldown_seconds: int
    target_audience: TargetAudience
    message_key: str
    title_key: str
    requires_email: bool = False


EVENT_REGISTRY: dict[str, EventDescriptor] = {
    "goal.alignment_low": EventDescriptor(
        key="goal.alignment_low",
        default_channels=(NotificationChannel.IN_APP,),
        default_urgency=NotificationUrgency.WARN,
        cooldown_seconds=600,
        target_audience="owner",
        message_key="notification.goalAlignmentLow.message",
        title_key="notification.goalAlignmentLow.title",
    ),
    "goal.locked": EventDescriptor(
        key="goal.locked",
        default_channels=(NotificationChannel.IN_APP,),
        default_urgency=NotificationUrgency.INFO,
        cooldown_seconds=0,
        target_audience="owner",
        message_key="notification.goalLocked.message",
        title_key="notification.goalLocked.title",
    ),
    "goal.unlocked": EventDescriptor(
        key="goal.unlocked",
        default_channels=(NotificationChannel.IN_APP,),
        default_urgency=NotificationUrgency.INFO,
        cooldown_seconds=0,
        target_audience="owner",
        message_key="notification.goalUnlocked.message",
        title_key="notification.goalUnlocked.title",
    ),
    "judge.score_negative": EventDescriptor(
        key="judge.score_negative",
        default_channels=(NotificationChannel.IN_APP,),
        default_urgency=NotificationUrgency.WARN,
        cooldown_seconds=600,
        target_audience="owner",
        message_key="notification.judgeScoreNegative.message",
        title_key="notification.judgeScoreNegative.title",
    ),
    "judge.degraded": EventDescriptor(
        key="judge.degraded",
        default_channels=(NotificationChannel.IN_APP,),
        default_urgency=NotificationUrgency.WARN,
        cooldown_seconds=3600,
        target_audience="workspace_admins",
        message_key="notification.judgeDegraded.message",
        title_key="notification.judgeDegraded.title",
    ),
    "channel.sender_blocked": EventDescriptor(
        key="channel.sender_blocked",
        default_channels=(NotificationChannel.IN_APP, NotificationChannel.EMAIL),
        default_urgency=NotificationUrgency.WARN,
        cooldown_seconds=600,
        target_audience="workspace_admins",
        message_key="notification.channelSenderBlocked.message",
        title_key="notification.channelSenderBlocked.title",
        requires_email=True,
    ),
    "security.signature_failed": EventDescriptor(
        key="security.signature_failed",
        default_channels=(NotificationChannel.IN_APP, NotificationChannel.EMAIL),
        default_urgency=NotificationUrgency.CRITICAL,
        cooldown_seconds=0,
        target_audience="workspace_admins",
        message_key="notification.securitySignatureFailed.message",
        title_key="notification.securitySignatureFailed.title",
        requires_email=True,
    ),
    "auth.workspace_provisioned": EventDescriptor(
        key="auth.workspace_provisioned",
        default_channels=(NotificationChannel.IN_APP, NotificationChannel.EMAIL),
        default_urgency=NotificationUrgency.INFO,
        cooldown_seconds=0,
        target_audience="actor",
        message_key="notification.workspaceProvisioned.message",
        title_key="notification.workspaceProvisioned.title",
        requires_email=True,
    ),
    "workspace.quota_exceeded": EventDescriptor(
        key="workspace.quota_exceeded",
        default_channels=(NotificationChannel.IN_APP,),
        default_urgency=NotificationUrgency.WARN,
        cooldown_seconds=600,
        target_audience="actor",
        message_key="notification.quotaExceeded.message",
        title_key="notification.quotaExceeded.title",
    ),
    "workspace.spike_detected": EventDescriptor(
        key="workspace.spike_detected",
        default_channels=(NotificationChannel.IN_APP,),
        default_urgency=NotificationUrgency.WARN,
        cooldown_seconds=1800,
        target_audience="platform_admins",
        message_key="notification.spikeDetected.message",
        title_key="notification.spikeDetected.title",
    ),
    "workspace.quota_increased": EventDescriptor(
        key="workspace.quota_increased",
        default_channels=(NotificationChannel.IN_APP, NotificationChannel.EMAIL),
        default_urgency=NotificationUrgency.INFO,
        cooldown_seconds=0,
        target_audience="actor",
        message_key="notification.quotaIncreased.message",
        title_key="notification.quotaIncreased.title",
        requires_email=False,
    ),
    "job.failed_permanent": EventDescriptor(
        key="job.failed_permanent",
        default_channels=(NotificationChannel.IN_APP, NotificationChannel.EMAIL),
        default_urgency=NotificationUrgency.CRITICAL,
        cooldown_seconds=300,
        target_audience="workspace_admins",
        message_key="notification.jobFailedPermanent.message",
        title_key="notification.jobFailedPermanent.title",
        requires_email=True,
    ),
    "approval.expiring": EventDescriptor(
        key="approval.expiring",
        default_channels=(NotificationChannel.IN_APP, NotificationChannel.EMAIL),
        default_urgency=NotificationUrgency.WARN,
        cooldown_seconds=3600,
        target_audience="workspace_admins",
        message_key="notification.approvalExpiring.message",
        title_key="notification.approvalExpiring.title",
        requires_email=True,
    ),
    "platform_settings.changed": EventDescriptor(
        key="platform_settings.changed",
        default_channels=(NotificationChannel.IN_APP,),
        default_urgency=NotificationUrgency.INFO,
        cooldown_seconds=0,
        target_audience="platform_admins",
        message_key="notification.platformSettingsChanged.message",
        title_key="notification.platformSettingsChanged.title",
        requires_email=False,
    ),
    "subagent.zombie_detected": EventDescriptor(
        key="subagent.zombie_detected",
        default_channels=(NotificationChannel.IN_APP,),
        default_urgency=NotificationUrgency.WARN,
        # 5 minutes — the reaper itself fires every 60s, dedup keeps a
        # workspace from being spammed when several siblings die in a
        # single tick (the cooldown_resource_id is the spine row id so
        # different children still notify independently).
        cooldown_seconds=300,
        target_audience="workspace_admins",
        message_key="notification.subagentZombieDetected.message",
        title_key="notification.subagentZombieDetected.title",
    ),
    # M2.5.3 — provider entered cooldown. Workspace admins want to see
    # this so they can adjust the chain or the upstream contract;
    # operators want it in the bell rather than email so a transient
    # provider blip doesn't spam inboxes (``requires_email=False``).
    "provider.cooldown_admin_alert": EventDescriptor(
        key="provider.cooldown_admin_alert",
        default_channels=(NotificationChannel.IN_APP,),
        default_urgency=NotificationUrgency.WARN,
        # 1h cooldown matches the M0.3 judge breaker pattern: a single
        # incident page per provider per hour, regardless of how many
        # turns hit the cooldown threshold inside that window.
        cooldown_seconds=3600,
        target_audience="workspace_admins",
        message_key="notification.providerCooldownStarted.message",
        title_key="notification.providerCooldownStarted.title",
        requires_email=False,
    ),
    "inflight_run.lost_detected": EventDescriptor(
        key="inflight_run.lost_detected",
        default_channels=(NotificationChannel.IN_APP,),
        default_urgency=NotificationUrgency.WARN,
        # cooldown 0 — the run already failed; the user needs to know
        # immediately and decide whether to /retry. Each event carries
        # the inflight_run row id as the cooldown_resource_id so two
        # genuinely distinct losses can't collide even without dedup.
        cooldown_seconds=0,
        target_audience="actor",
        message_key="notification.inflightRunLostDetected.message",
        title_key="notification.inflightRunLostDetected.title",
    ),
    # M4.1 — admin force-recycled a live run from the runtime console.
    # Fires to the actor (the admin who clicked) so they get a bell
    # confirmation; cooldown 0 because every recycle is a deliberate
    # one-shot action whose target row id keeps the dedup key unique.
    "inflight_run.force_recycled": EventDescriptor(
        key="inflight_run.force_recycled",
        default_channels=(NotificationChannel.IN_APP,),
        default_urgency=NotificationUrgency.INFO,
        cooldown_seconds=0,
        target_audience="actor",
        message_key="notification.inflightRunForceRecycled.message",
        title_key="notification.inflightRunForceRecycled.title",
    ),
    # M2.5.9 — provider repeatedly missed our cache markers and the
    # adaptive tracker tripped the per-(workspace, provider) disable
    # window. Surfaced to workspace admins so they can investigate
    # whether the workspace's persona / system prompt drifted off the
    # previously cached prefix; in-app only because a transient blip
    # shouldn't fill the inbox. 1 h cooldown matches the disable
    # window's recovery cadence.
    "cache.adaptive_disabled": EventDescriptor(
        key="cache.adaptive_disabled",
        default_channels=(NotificationChannel.IN_APP,),
        default_urgency=NotificationUrgency.WARN,
        cooldown_seconds=3600,
        target_audience="workspace_admins",
        message_key="notification.cacheAdaptiveDisabled.message",
        title_key="notification.cacheAdaptiveDisabled.title",
        requires_email=False,
    ),
}


# ── Settings helper ─────────────────────────────────────────
async def get_notification_settings(db: AsyncSession) -> dict[str, Any]:
    """Return the merged platform notification settings dict.

    Falls back to the static defaults registered in
    :mod:`app.services.system_settings` when the row is missing — any
    key the operator hasn't set yet still resolves to the safe default.
    """
    raw = await get_system_setting(db, SystemSettingKey.NOTIFICATION_DEFAULTS, default=None)
    if isinstance(raw, dict):
        return raw
    return {}


# ── Cooldown (Redis) ────────────────────────────────────────
def _dedup_key(
    *,
    event_key: str,
    workspace_id: uuid.UUID | None,
    target_label: str,
    cooldown_resource_id: str | None,
) -> str:
    ws_part = str(workspace_id) if workspace_id is not None else "global"
    res_part = cooldown_resource_id or ""
    return f"notif_dedup:{event_key}:{ws_part}:{target_label}:{res_part}"


async def _claim_cooldown(*, key: str, ttl_seconds: int) -> bool:
    """Best-effort Redis dedup. Returns True when this caller wins.

    Uses ``SET NX EX`` so concurrent callers race for the slot. When
    Redis is unreachable we fail-open (allow the notification) so an
    outage in the cache doesn't silently swallow user-visible events.
    """
    if ttl_seconds <= 0:
        return True
    try:
        from app.core.rate_limit import get_redis

        r = get_redis()
        result = await r.set(key, "1", nx=True, ex=int(ttl_seconds))
        return bool(result)
    except Exception as exc:  # pragma: no cover - fail-open
        log.warning("notification cooldown redis unavailable (%s)", exc)
        return True


# ── Target resolution ───────────────────────────────────────
async def _resolve_targets(
    db: AsyncSession,
    *,
    descriptor: EventDescriptor,
    workspace_id: uuid.UUID | None,
    actor_identity_id: uuid.UUID | None,
    explicit_targets: list[uuid.UUID] | None,
) -> list[uuid.UUID]:
    if explicit_targets:
        return list(dict.fromkeys(explicit_targets))

    audience = descriptor.target_audience

    if audience == "actor":
        return [actor_identity_id] if actor_identity_id is not None else []

    if audience == "owner":
        if workspace_id is None:
            return []
        stmt = (
            select(Membership.identity_id)
            .where(Membership.workspace_id == workspace_id)
            .where(Membership.role == BuiltinRole.OWNER.value)
            .where(Membership.status == MembershipStatus.ACTIVE)
            .where(Membership.deleted_at.is_(None))
        )
        return list((await db.execute(stmt)).scalars().all())

    if audience == "workspace_admins":
        if workspace_id is None:
            return []
        stmt = (
            select(Membership.identity_id)
            .where(Membership.workspace_id == workspace_id)
            .where(Membership.role.in_([BuiltinRole.OWNER.value, BuiltinRole.ADMIN.value]))
            .where(Membership.status == MembershipStatus.ACTIVE)
            .where(Membership.deleted_at.is_(None))
        )
        return list((await db.execute(stmt)).scalars().all())

    if audience == "platform_admins":
        stmt = (
            select(Identity.id)
            .where(Identity.platform_role == PlatformRole.PLATFORM_ADMIN)
            .where(Identity.status == IdentityStatus.ACTIVE)
            .where(Identity.deleted_at.is_(None))
        )
        return list((await db.execute(stmt)).scalars().all())

    if audience == "broadcast":
        if workspace_id is None:
            return []
        stmt = (
            select(Membership.identity_id)
            .where(Membership.workspace_id == workspace_id)
            .where(Membership.status == MembershipStatus.ACTIVE)
            .where(Membership.deleted_at.is_(None))
        )
        return list((await db.execute(stmt)).scalars().all())

    return []


# ── Preference merging ──────────────────────────────────────
def _read_identity_prefs(identity: Identity, event_key: str) -> dict[str, Any]:
    raw = getattr(identity, "notification_prefs_json", None) or {}
    if not isinstance(raw, dict):
        return {}
    entry = raw.get(event_key)
    if not isinstance(entry, dict):
        return {}
    return entry


def _is_globally_muted(identity: Identity) -> bool:
    from datetime import UTC
    from datetime import datetime as _dt

    raw = getattr(identity, "notification_prefs_json", None) or {}
    if not isinstance(raw, dict):
        return False
    glob = raw.get("_global")
    if not isinstance(glob, dict):
        return False
    muted_until = glob.get("muted_until")
    if not muted_until:
        return False
    try:
        when = _dt.fromisoformat(str(muted_until).replace("Z", "+00:00"))
    except Exception:
        return False
    return when > _dt.now(UTC)


def _effective_channels(
    descriptor: EventDescriptor,
    identity: Identity,
    *,
    platform_email_critical_only: bool,
) -> set[NotificationChannel]:
    """Merge platform default + identity prefs into a final channel set.

    Hard rules (in order):

    1. ``requires_email=True`` events ALWAYS include EMAIL — the user
       cannot opt out at the personal-preference layer because these
       are operationally critical (security, welcome, permanent job
       failure). The platform admin can still suppress them
       wholesale via the system setting.
    2. ``platform_email_critical_only=True`` (default) means non-
       ``requires_email`` events must drop EMAIL even if the user
       opted in — the platform doesn't run a marketing-grade mail
       outbox in M0.
    3. Per-identity ``muted=True`` returns an empty set (skip).
    4. Per-identity ``channels=[...]`` intersects with the
       descriptor's defaults plus the ``requires_email`` floor.

    Falls through to the descriptor defaults when the user has no
    preference set for the key.
    """
    channels = set(descriptor.default_channels)
    prefs = _read_identity_prefs(identity, descriptor.key)
    if prefs.get("muted"):
        if descriptor.requires_email:
            return {NotificationChannel.EMAIL}
        return set()
    user_channels = prefs.get("channels")
    if isinstance(user_channels, list):
        try:
            wanted = {NotificationChannel(c) for c in user_channels}
        except ValueError:
            wanted = set(descriptor.default_channels)
        channels = wanted & set(descriptor.default_channels)

    if descriptor.requires_email:
        channels.add(NotificationChannel.EMAIL)
    elif platform_email_critical_only:
        channels.discard(NotificationChannel.EMAIL)

    return channels


# ── Public emit ─────────────────────────────────────────────
async def emit_event(
    db: AsyncSession,
    *,
    event_key: str,
    workspace_id: uuid.UUID | None,
    actor_identity_id: uuid.UUID | None = None,
    target_identity_ids: list[uuid.UUID] | None = None,
    cooldown_resource_id: str | None = None,
    payload: dict[str, Any] | None = None,
    request: Any | None = None,
) -> dict[str, int]:
    """Fan one logical event out across the cartesian product of channels and identities.

    Always best-effort. Caller owns the surrounding transaction;
    ``emit_event`` neither commits nor rolls back so a failed
    notification cannot break the audit row that triggered it. When
    fan-out itself crashes we audit ``notification.emit_failed`` on a
    fresh session so the failure is visible without re-raising.

    Returns counters useful for tests + admin diagnostics:

    - ``in_app_sent`` — Notification rows inserted
    - ``email_sent`` — email job enqueues
    - ``cooldown_skipped`` — recipients filtered by Redis dedup
    - ``pref_skipped`` — recipients filtered by personal preferences
    """
    descriptor = EVENT_REGISTRY.get(event_key)
    if descriptor is None:
        log.warning("emit_event: unknown event_key=%s", event_key)
        return _empty_counters()

    payload = payload or {}
    counters = _empty_counters()
    try:
        targets = await _resolve_targets(
            db,
            descriptor=descriptor,
            workspace_id=workspace_id,
            actor_identity_id=actor_identity_id,
            explicit_targets=target_identity_ids,
        )
        if not targets:
            return counters

        identities = await _load_identities(db, identity_ids=targets)
        platform_settings = await get_notification_settings(db)
        critical_only = bool(platform_settings.get("platform_email_critical_only", True))

        for identity in identities:
            if _is_globally_muted(identity):
                counters["pref_skipped"] += 1
                continue
            channels = _effective_channels(
                descriptor,
                identity,
                platform_email_critical_only=critical_only,
            )
            if not channels:
                counters["pref_skipped"] += 1
                continue

            target_label = str(identity.id)
            cooldown_key = _dedup_key(
                event_key=event_key,
                workspace_id=workspace_id,
                target_label=target_label,
                cooldown_resource_id=cooldown_resource_id,
            )
            if not await _claim_cooldown(key=cooldown_key, ttl_seconds=descriptor.cooldown_seconds):
                counters["cooldown_skipped"] += 1
                continue

            await _dispatch(
                db,
                descriptor=descriptor,
                identity=identity,
                channels=channels,
                actor_identity_id=actor_identity_id,
                workspace_id=workspace_id,
                payload=payload,
                counters=counters,
            )

        await audit_svc.record(
            db,
            action="notification.emitted",
            actor_identity_id=actor_identity_id,
            workspace_id=workspace_id,
            resource_type="notification_event",
            resource_id=None,
            summary=f"emitted {event_key}",
            metadata={
                "event_key": event_key,
                "target_count": len(targets),
                "in_app_sent": counters["in_app_sent"],
                "email_sent": counters["email_sent"],
                "cooldown_skipped": counters["cooldown_skipped"],
                "pref_skipped": counters["pref_skipped"],
                "default_channels": [c.value for c in descriptor.default_channels],
                "cooldown_resource_id": cooldown_resource_id,
            },
            request=request,
        )
        if counters["cooldown_skipped"]:
            await audit_svc.record(
                db,
                action="notification.cooldown_skipped",
                actor_identity_id=None,
                workspace_id=workspace_id,
                resource_type="notification_event",
                resource_id=None,
                summary=(f"{counters['cooldown_skipped']} dedup hits for {event_key}"),
                metadata={
                    "event_key": event_key,
                    "count_in_window": counters["cooldown_skipped"],
                    "cooldown_seconds": descriptor.cooldown_seconds,
                },
                request=request,
            )
    except Exception as exc:  # pragma: no cover - safety net
        log.exception("emit_event %s failed", event_key)
        await _audit_emit_failure(
            event_key=event_key,
            workspace_id=workspace_id,
            actor_identity_id=actor_identity_id,
            exception=exc,
            request=request,
        )
    return counters


def _empty_counters() -> dict[str, int]:
    return {
        "in_app_sent": 0,
        "email_sent": 0,
        "cooldown_skipped": 0,
        "pref_skipped": 0,
    }


async def _load_identities(
    db: AsyncSession, *, identity_ids: Iterable[uuid.UUID]
) -> list[Identity]:
    ids = list(identity_ids)
    if not ids:
        return []
    stmt = (
        select(Identity)
        .where(Identity.id.in_(ids))
        .where(Identity.deleted_at.is_(None))
        .where(Identity.status != IdentityStatus.SUSPENDED)
    )
    return list((await db.execute(stmt)).scalars().all())


async def _dispatch(
    db: AsyncSession,
    *,
    descriptor: EventDescriptor,
    identity: Identity,
    channels: set[NotificationChannel],
    actor_identity_id: uuid.UUID | None,
    workspace_id: uuid.UUID | None,
    payload: dict[str, Any],
    counters: dict[str, int],
) -> None:
    """Drive one event into every effective delivery channel."""
    title = _render_template(descriptor.title_key, payload)
    body = _render_template(descriptor.message_key, payload)
    level = _URGENCY_TO_LEVEL[descriptor.default_urgency]

    if NotificationChannel.IN_APP in channels:
        ws_for_row = workspace_id or _fallback_workspace_for_identity(db_identity=identity)
        if ws_for_row is None:
            ws_for_row = await _resolve_any_workspace_for(db, identity.id)
        if ws_for_row is not None:
            await notif_svc.create_notification(
                db,
                workspace_id=ws_for_row,
                recipient_identity_id=identity.id,
                actor_identity_id=actor_identity_id,
                kind=descriptor.key,
                title=title,
                body=body,
                level=level.value,
                resource_type=payload.get("resource_type"),
                resource_id=payload.get("resource_id"),
                action_url=payload.get("action_url"),
                metadata_json={
                    "event_key": descriptor.key,
                    "urgency": descriptor.default_urgency.value,
                    "title_key": descriptor.title_key,
                    "message_key": descriptor.message_key,
                    "payload": payload,
                },
            )
            counters["in_app_sent"] += 1

    if NotificationChannel.EMAIL in channels:
        try:
            from app.worker import queue as queue_svc

            await queue_svc.enqueue(
                "send_email_notification",
                {
                    "event_key": descriptor.key,
                    "to_email": identity.email,
                    "to_identity_id": str(identity.id),
                    "title_key": descriptor.title_key,
                    "message_key": descriptor.message_key,
                    "payload": payload,
                    "urgency": descriptor.default_urgency.value,
                    "workspace_id": (str(workspace_id) if workspace_id else None),
                    "idempotency_key": _email_idempotency_key(
                        event_key=descriptor.key,
                        identity_id=identity.id,
                        cooldown_seed=payload.get("cooldown_resource_id") or "",
                    ),
                    "subject_fallback": title,
                    "body_fallback": body,
                },
            )
            counters["email_sent"] += 1
        except Exception as exc:  # pragma: no cover - enqueue best-effort
            log.warning(
                "email enqueue failed for %s/%s: %s",
                descriptor.key,
                identity.id,
                exc,
            )


def _fallback_workspace_for_identity(*, db_identity: Identity) -> uuid.UUID | None:
    """Hook for future per-identity preferred workspace.

    Today the in-app notification table requires a workspace_id, so
    when the event is platform-scoped we resolve a best-effort one in
    :func:`_resolve_any_workspace_for`. Kept as a separate stub so
    M0.13 can plug in a "preferred workspace" identity column without
    rewriting the dispatcher.
    """
    return None


async def _resolve_any_workspace_for(db: AsyncSession, identity_id: uuid.UUID) -> uuid.UUID | None:
    stmt = (
        select(Membership.workspace_id)
        .where(Membership.identity_id == identity_id)
        .where(Membership.status == MembershipStatus.ACTIVE)
        .where(Membership.deleted_at.is_(None))
        .limit(1)
    )
    return (await db.execute(stmt)).scalar_one_or_none()


def _render_template(key: str, payload: dict[str, Any]) -> str:
    """Bare-bones placeholder substitution.

    The real i18n catalog lives on the frontend; the backend stores
    the i18n key in ``metadata_json`` so the bell renderer can switch
    locales at read time. This helper only produces a usable
    fallback string for non-localized contexts (email body in M0.13,
    server-side log lines, audit summaries).
    """
    template_text = key
    try:
        if not isinstance(payload, dict):
            return template_text
        return template_text.format(**payload)
    except (KeyError, IndexError, ValueError):
        return template_text


def _email_idempotency_key(*, event_key: str, identity_id: uuid.UUID, cooldown_seed: str) -> str:
    raw = f"{event_key}:{identity_id}:{cooldown_seed}".encode()
    return hashlib.sha256(raw).hexdigest()[:32]


async def _audit_emit_failure(
    *,
    event_key: str,
    workspace_id: uuid.UUID | None,
    actor_identity_id: uuid.UUID | None,
    exception: BaseException,
    request: Any | None,
) -> None:
    """Audit a fan-out crash on a fresh session so the row survives rollback."""
    try:
        from app.db.session import get_session_factory

        factory = get_session_factory()
        async with factory() as fresh:
            await audit_svc.record(
                fresh,
                action="notification.emit_failed",
                actor_identity_id=actor_identity_id,
                workspace_id=workspace_id,
                resource_type="notification_event",
                resource_id=None,
                summary=f"emit_event {event_key} crashed",
                metadata={
                    "event_key": event_key,
                    "error_class": type(exception).__name__,
                    "error_repr": repr(exception)[:500],
                },
                request=request,
            )
            await fresh.commit()
    except Exception:  # pragma: no cover - audit best-effort
        log.exception("notification.emit_failed audit also crashed")


def get_user_visible_event_keys() -> list[str]:
    """Frontend hook: which keys to render on the prefs page.

    ``platform_admins`` audience events are filtered out for non-admin
    callers — the API layer applies the user's role on top of this
    static list. Keeping the curation here means the registry stays
    the single source of truth.
    """
    return [
        descriptor.key
        for descriptor in EVENT_REGISTRY.values()
        if descriptor.target_audience != "platform_admins"
    ]


_ = app_settings  # silence unused import (reserved for future config gate)
_ = cast  # silence unused import (kept for type narrowing helpers)
_ = Workspace  # silence unused import

__all__ = [
    "EVENT_REGISTRY",
    "EventDescriptor",
    "NotificationChannel",
    "NotificationUrgency",
    "TargetAudience",
    "emit_event",
    "get_notification_settings",
    "get_user_visible_event_keys",
]
