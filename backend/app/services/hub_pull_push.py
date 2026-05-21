"""Skill Hub promote / subscribe / pull pipeline (M3.3).

Composes the M3.1 catalog + M3.2 sanitizer preview into the four
mutations that complete the federation surface:

* :func:`initiate_promotion` — admin clicks *Promote to Hub*. Re-runs
  the M3.2 preview, refuses on blockers, and files an :class:`Approval`
  row with ``resource_type='hub_promotion'`` and the 30-day TTL from
  the roadmap Approval-TTL strategy table. Nothing lands on the hub
  yet — the human gate sits between propose and apply.
* :func:`apply_promotion` — invoked by the M2.5 dispatch handler when
  the admin approves the row. Re-runs the preview (sanitizer state may
  have moved between propose and apply), creates / reuses the
  :class:`HubSkillPack`, drops the new
  :class:`HubSkillPackVersion` (or attaches to the dedup target), and
  back-subscribes the source workspace with ``auto_pull=True``.
* :func:`subscribe` / :func:`unsubscribe` — soft toggle of the
  :class:`WorkspaceHubSubscription` row. ``unsubscribe`` performs a
  soft delete (the row stays so the audit feed still references the
  cursor for forensic queries).
* :func:`pull_now` — drafts a local
  :class:`~app.db.models.skill_pack_version.SkillPackVersion(state=PROPOSED)`
  on a local :class:`~app.db.models.skills.SkillPack(state=DRAFT)` from
  the hub's currently active version. Crucially the candidate is
  PROPOSED, not ACTIVE — the M2.4 verifier must clear it before the
  workspace's runtime injection picks it up. Auto-pull rides the same
  function so the M3.3 ARQ sweep cannot bypass approval.

Stable audit action keys (consumed by the admin feed and the M0.10
notification dispatcher):

* ``hub.promotion_proposed``
* ``hub.promotion_applied``
* ``hub.promotion_rejected``
* ``hub.subscription.created``
* ``hub.subscription.deleted``
* ``hub.pulled``
* ``hub.pulled_skipped_up_to_date``
* ``hub.auto_pull_failed_permanent``

Caller commits in every mutation. Helpers ``flush`` so an admin
batch endpoint can stack multiple verbs inside one outer transaction.
"""

from __future__ import annotations

import logging
import uuid
from datetime import timedelta
from typing import Any, cast

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import (
    AppError,
    HubScopePermissionDenied,
    HubSlugTombstoned,
    NotFound,
    PermissionDenied,
)
from app.core.security import utcnow_naive
from app.db.models.approval import Approval, ApprovalStatus
from app.db.models.hub_skill_pack import HubScope, HubSkillPack, HubSkillPackState
from app.db.models.hub_skill_pack_version import HubSkillPackVersion
from app.db.models.identity import Identity, PlatformRole
from app.db.models.skill_pack_version import SkillPackVersionState
from app.db.models.skills import SkillPack, SkillPackSource, SkillPackState
from app.db.models.workspace_hub_subscription import WorkspaceHubSubscription
from app.repositories.approval import ApprovalRepository
from app.repositories.hub_skill_pack import (
    HubSkillPackRepository,
    HubSkillPackVersionRepository,
    WorkspaceHubSubscriptionRepository,
)
from app.repositories.skill_pack_version import SkillPackVersionRepository
from app.repositories.skills import SkillPackRepository
from app.services import audit as audit_svc
from app.services import hub_promote_pipeline as preview_svc
from app.services import hub_skill as hub_svc

log = logging.getLogger(__name__)

__all__ = [
    "AUDIT_AUTO_PULL_FAILED_PERMANENT",
    "AUDIT_PROMOTION_APPLIED",
    "AUDIT_PROMOTION_PROPOSED",
    "AUDIT_PROMOTION_REJECTED",
    "AUDIT_PULLED",
    "AUDIT_PULLED_UP_TO_DATE",
    "AUDIT_SUBSCRIPTION_CREATED",
    "AUDIT_SUBSCRIPTION_DELETED",
    "HUB_PROMOTION_RESOURCE_TYPE",
    "HUB_PROMOTION_TTL_DAYS",
    "HubPromotionBlocked",
    "HubPullResult",
    "PullStatus",
    "apply_promotion",
    "initiate_promotion",
    "pull_now",
    "subscribe",
    "unsubscribe",
]


# ── Constants ────────────────────────────────────────────────
HUB_PROMOTION_RESOURCE_TYPE = "hub_promotion"
HUB_PROMOTION_TTL_DAYS = 30  # Roadmap Approval-TTL strategy table.

AUDIT_PROMOTION_PROPOSED = "hub.promotion_proposed"
AUDIT_PROMOTION_APPLIED = "hub.promotion_applied"
AUDIT_PROMOTION_REJECTED = "hub.promotion_rejected"
AUDIT_SUBSCRIPTION_CREATED = "hub.subscription.created"
AUDIT_SUBSCRIPTION_DELETED = "hub.subscription.deleted"
AUDIT_PULLED = "hub.pulled"
AUDIT_PULLED_UP_TO_DATE = "hub.pulled_skipped_up_to_date"
AUDIT_AUTO_PULL_FAILED_PERMANENT = "hub.auto_pull_failed_permanent"

# Pull statuses returned by :func:`pull_now`.
PullStatus = str
_PULL_STATUS_PULLED = "pulled"
_PULL_STATUS_UP_TO_DATE = "up_to_date"
_PULL_STATUS_NO_ACTIVE_VERSION = "no_active_version"


# ── Errors ───────────────────────────────────────────────────
class HubPromotionBlocked(AppError):
    """Preview returned non-empty blockers — caller surfaces 409.

    The blocker list lives in ``extras['blockers']``; callers can
    map each stable key to a localized string via the existing
    ``hub.*`` error catalogue (M3.1) plus the M3.2 sanitizer codes.
    """

    code = "hub.promotion_blocked"
    default_status = 409


class HubSubscriptionNotFound(NotFound):
    code = "hub.subscription_not_found"


# ── DTO ──────────────────────────────────────────────────────
class HubPullResult:
    """Result envelope returned by :func:`pull_now`.

    Attribute-only (no dataclass) so adding fields downstream cannot
    break ``__dict__`` serialization in callers / tests.
    """

    __slots__ = (
        "status",
        "hub_pack_id",
        "hub_version_no",
        "local_pack_id",
        "local_version_id",
        "local_version_no",
    )

    def __init__(
        self,
        *,
        status: PullStatus,
        hub_pack_id: uuid.UUID,
        hub_version_no: int | None = None,
        local_pack_id: uuid.UUID | None = None,
        local_version_id: uuid.UUID | None = None,
        local_version_no: int | None = None,
    ) -> None:
        self.status = status
        self.hub_pack_id = hub_pack_id
        self.hub_version_no = hub_version_no
        self.local_pack_id = local_pack_id
        self.local_version_id = local_version_id
        self.local_version_no = local_version_no


# ── Initiate promotion ───────────────────────────────────────
async def initiate_promotion(
    db: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    pack_id: uuid.UUID,
    target_scope: HubScope,
    actor: Identity,
    target_slug: str | None = None,
    version_id: uuid.UUID | None = None,
    reason: str | None = None,
    request: Any = None,
) -> Approval:
    """File a hub-promotion approval. Caller commits.

    Order of operations:

    1. ``require_hub_enabled`` — short-circuit when the platform admin
       has flipped the federation surface off.
    2. ``preview_promotion`` (M3.2) — sanitize + scope eligibility +
       dedup lookup + audit. Any blocker → :class:`HubPromotionBlocked`.
    3. PLATFORM-scope guard — even when M3.2 returns no blocker we
       defensively recheck the platform-admin role here so an out-of-
       date preview cannot be racey.
    4. Create the :class:`Approval` row with ``resource_type='hub_promotion'``
       and a 30-day TTL.
    5. Audit ``hub.promotion_proposed``.
    """
    await hub_svc.require_hub_enabled(db)

    preview = await preview_svc.preview_promotion(
        db,
        request=preview_svc.HubPromotionInput(
            workspace_id=workspace_id,
            pack_id=pack_id,
            target_scope=target_scope,
            version_id=version_id,
            target_slug=target_slug,
        ),
        actor_identity=actor,
        audit_request=request,
    )

    if not preview.is_promotable:
        # Map specific blocker keys to typed errors so the API
        # response carries the historic 409 / 403 shape callers
        # already handle (HubScopePermissionDenied / HubSlugTombstoned).
        # Anything else falls through to the generic
        # :class:`HubPromotionBlocked`.
        if preview_svc.BLOCKER_SCOPE_PERMISSION_DENIED in preview.blockers:
            raise HubScopePermissionDenied(
                "platform_admin_required",
                code="hub.scope_permission_denied",
                extras={"scope": target_scope.value, "blockers": list(preview.blockers)},
            )
        if preview_svc.BLOCKER_SLUG_TOMBSTONED in preview.blockers:
            raise HubSlugTombstoned(
                "hub_slug_tombstoned",
                code="hub.slug_tombstoned",
                extras={
                    "scope": target_scope.value,
                    "slug": preview.target_slug,
                    "blockers": list(preview.blockers),
                },
            )
        raise HubPromotionBlocked(
            "hub_promotion_blocked",
            code="hub.promotion_blocked",
            extras={"blockers": list(preview.blockers)},
        )

    if target_scope == HubScope.PLATFORM and (
        actor.platform_role != PlatformRole.PLATFORM_ADMIN
    ):
        raise HubScopePermissionDenied(
            "platform_admin_required",
            code="hub.scope_permission_denied",
            extras={"scope": target_scope.value},
        )

    expires_at = utcnow_naive() + timedelta(days=HUB_PROMOTION_TTL_DAYS)
    sanitized_stats = preview.sanitized.stats
    approval_body: dict[str, Any] = {
        "pack_id": str(pack_id),
        "version_id": str(version_id) if version_id else None,
        "target_scope": target_scope.value,
        "target_slug": preview.target_slug,
        "target_tenant_id": (
            str(preview.target_tenant_id)
            if preview.target_tenant_id is not None
            else None
        ),
        "sanitized_content_hash": preview.sanitized_content_hash,
        "sanitization_stats": {
            "redacted_emails": sanitized_stats.redacted_emails,
            "redacted_urls": sanitized_stats.redacted_urls,
            "redacted_paths": sanitized_stats.redacted_paths,
            "redacted_pii": sanitized_stats.redacted_pii,
            "redacted_extra": sanitized_stats.redacted_extra,
            "run_id_hashed_count": sanitized_stats.run_id_hashed_count,
            "failure_reason": sanitized_stats.failure_reason,
        },
        "will_dedup_against_version_id": (
            str(preview.will_dedup_against.id)
            if preview.will_dedup_against is not None
            else None
        ),
        "will_dedup_against_pack_id": (
            str(preview.will_dedup_against.hub_pack_id)
            if preview.will_dedup_against is not None
            else None
        ),
        "reason": reason,
    }

    approval = await ApprovalRepository(db).create(
        workspace_id=workspace_id,
        session_id=None,
        agent_id=None,
        run_id=None,
        tool_name="none",
        tool_args=approval_body,
        summary=(
            f"hub promote: pack {pack_id} → "
            f"{target_scope.value}/{preview.target_slug}"
        ),
        requested_by_identity_id=actor.id,
        expires_at=expires_at,
        resource_type=HUB_PROMOTION_RESOURCE_TYPE,
        resource_id=pack_id,
    )

    await audit_svc.record(
        db,
        action=AUDIT_PROMOTION_PROPOSED,
        actor_identity_id=actor.id,
        workspace_id=workspace_id,
        resource_type="skill_pack",
        resource_id=pack_id,
        summary=(
            f"hub promote proposed: pack {pack_id} → "
            f"{target_scope.value}/{preview.target_slug}"
        ),
        metadata={
            "approval_id": str(approval.id),
            "pack_id": str(pack_id),
            "target_scope": target_scope.value,
            "target_slug": preview.target_slug,
            "target_tenant_id": (
                str(preview.target_tenant_id)
                if preview.target_tenant_id is not None
                else None
            ),
            "sanitized_content_hash": preview.sanitized_content_hash,
            "will_dedup_against_version_id": (
                str(preview.will_dedup_against.id)
                if preview.will_dedup_against is not None
                else None
            ),
            "expires_at": expires_at.isoformat(),
            "sanitization_stats": approval_body["sanitization_stats"],
        },
        request=request,
    )
    return approval


# ── Apply promotion (called by approval dispatch handler) ────
async def apply_promotion(
    db: AsyncSession,
    *,
    approval_id: uuid.UUID,
    actor_identity_id: uuid.UUID | None,
) -> dict[str, Any]:
    """Promote the source pack version into the hub catalog.

    Re-runs :func:`preview_promotion` so a sanitizer regression /
    workspace settings change between propose and apply still blocks
    the apply (the M3.2 brief documents this as the canonical entry
    point for the M3.3 commit wrapper).

    Returns ``{hub_pack_id, hub_version_id, hub_version_no, deduped,
    subscription_id}`` for the dispatch handler's ``DispatchResult``.
    """
    approval_row = await ApprovalRepository(db).get(approval_id)
    if approval_row is None:
        raise NotFound(
            "approval_not_found", code="approval.not_found"
        )
    if approval_row.resource_type != HUB_PROMOTION_RESOURCE_TYPE:
        raise NotFound(
            "approval_not_hub_promotion",
            code="approval.not_hub_promotion",
            extras={"resource_type": approval_row.resource_type},
        )

    body = approval_row.tool_args or {}
    pack_id_raw = body.get("pack_id") or approval_row.resource_id
    if not pack_id_raw:
        raise NotFound(
            "approval body missing pack_id",
            code="approval.dispatch_invalid_body",
        )
    try:
        pack_id = uuid.UUID(str(pack_id_raw))
    except (TypeError, ValueError) as exc:
        raise NotFound(
            f"invalid pack_id in approval body: {pack_id_raw!r}",
            code="approval.dispatch_invalid_body",
        ) from exc

    target_scope = HubScope(body.get("target_scope") or HubScope.TENANT.value)
    version_id_raw = body.get("version_id")
    version_id = (
        uuid.UUID(str(version_id_raw)) if version_id_raw else None
    )
    target_slug = body.get("target_slug")

    # Resolve actor identity for the audit / subscription back-ref.
    actor: Identity | None = None
    actor_id_for_audit = (
        actor_identity_id or approval_row.requested_by_identity_id
    )
    if actor_id_for_audit is not None:
        actor = await db.get(Identity, actor_id_for_audit)
    if actor is None:
        # Synthesize a minimal identity stand-in for the eligibility
        # check — hub_promotion approvals should always carry a
        # requester, but we degrade gracefully so a deleted human
        # doesn't block an otherwise valid apply. The PLATFORM-scope
        # gate falls back to the approval body's stored decision.
        actor = await _synthesize_actor(db, approval_row=approval_row)

    workspace_id = approval_row.workspace_id

    # Re-run the preview inside the same transaction. If sanitize
    # blockers reappeared we refuse and let the dispatch handler
    # surface the failure as ``approval.dispatch_failed``.
    preview = await preview_svc.preview_promotion(
        db,
        request=preview_svc.HubPromotionInput(
            workspace_id=workspace_id,
            pack_id=pack_id,
            target_scope=target_scope,
            version_id=version_id,
            target_slug=target_slug,
        ),
        actor_identity=actor,
    )

    if not preview.is_promotable:
        raise HubPromotionBlocked(
            "hub_promotion_blocked_at_apply",
            code="hub.promotion_blocked",
            extras={
                "approval_id": str(approval_id),
                "blockers": list(preview.blockers),
            },
        )

    target_tenant_id = preview.target_tenant_id
    final_slug = preview.target_slug
    sanitized_content_hash = preview.sanitized_content_hash

    pack_repo = HubSkillPackRepository(db)
    version_repo = HubSkillPackVersionRepository(db)
    sub_repo = WorkspaceHubSubscriptionRepository(db)

    # Find or create the hub pack row.
    hub_pack = await pack_repo.get_by_slug(
        scope=target_scope, tenant_id=target_tenant_id, slug=final_slug
    )
    pack_created = False
    source_pack = await SkillPackRepository(db).get(pack_id, include_deleted=True)
    if hub_pack is None:
        hub_pack = await pack_repo.create(
            scope=target_scope,
            tenant_id=target_tenant_id,
            slug=final_slug,
            name=(source_pack.name if source_pack else final_slug)[:200],
            description=source_pack.description if source_pack else None,
            state=HubSkillPackState.ACTIVE,
            promoted_from_pack_id=pack_id,
            promoted_from_workspace_id=workspace_id,
            promoted_by_identity_id=actor_id_for_audit,
            tags=[],
        )
        pack_created = True
        await db.flush([hub_pack])

    # Dedup path: existing hub version with the same sanitized hash
    # → activate it (if it isn't already) instead of inserting a new
    # row. The subscription back-ref still lands so the source
    # workspace tracks updates against the deduped row.
    deduped_against: HubSkillPackVersion | None = None
    if preview.will_dedup_against is not None:
        deduped_against = preview.will_dedup_against
        # Sanity: ensure it sits on the same hub_pack we resolved
        # above (the slug + hash combo could theoretically collide
        # against a different pack if the catalog has multiple).
        if deduped_against.hub_pack_id != hub_pack.id:
            deduped_against = await version_repo.find_by_hash(
                hub_pack_id=hub_pack.id,
                content_hash=sanitized_content_hash,
            )

    if deduped_against is None:
        # Re-check by hash now that we know the resolved hub_pack
        # (preview lookup may have returned None when the hub_pack
        # didn't exist yet at preview time).
        deduped_against = await version_repo.find_by_hash(
            hub_pack_id=hub_pack.id,
            content_hash=sanitized_content_hash,
        )

    hub_version: HubSkillPackVersion
    deduped: bool
    if deduped_against is not None:
        hub_version = deduped_against
        deduped = True
        # Ensure exactly one is_active=True per hub_pack.
        if not hub_version.is_active:
            await _retire_active(version_repo, hub_pack_id=hub_pack.id)
            hub_version.is_active = True
            await db.flush([hub_version])
    else:
        deduped = False
        next_no = await version_repo.next_version_no(hub_pack_id=hub_pack.id)
        hub_version = await version_repo.create(
            hub_pack_id=hub_pack.id,
            version_no=next_no,
            content_hash=sanitized_content_hash,
            content_md=preview.sanitized.content_md,
            files_json=dict(preview.sanitized_files or {}),
            promoted_from_workspace_version_id=version_id,
            is_active=False,
        )
        await db.flush([hub_version])
        await _retire_active(version_repo, hub_pack_id=hub_pack.id)
        hub_version.is_active = True
        await db.flush([hub_version])

    # Back-subscribe the source workspace with auto_pull=True so a
    # later improvement on the same slug surfaces as an update for
    # them too.
    subscription = await _ensure_subscription(
        sub_repo,
        workspace_id=workspace_id,
        hub_pack=hub_pack,
        identity_id=actor_id_for_audit,
        auto_pull=True,
        last_pulled_version_no=hub_version.version_no,
    )

    await audit_svc.record(
        db,
        action=AUDIT_PROMOTION_APPLIED,
        actor_identity_id=actor_id_for_audit,
        workspace_id=workspace_id,
        resource_type="hub_skill_pack_version",
        resource_id=hub_version.id,
        summary=(
            f"hub promote applied: pack {pack_id} → hub {hub_pack.slug!r} "
            f"v{hub_version.version_no} ({'deduped' if deduped else 'new'})"
        ),
        metadata={
            "approval_id": str(approval_id),
            "pack_id": str(pack_id),
            "hub_pack_id": str(hub_pack.id),
            "hub_pack_slug": hub_pack.slug,
            "hub_pack_created": pack_created,
            "hub_version_id": str(hub_version.id),
            "hub_version_no": hub_version.version_no,
            "deduped": deduped,
            "scope": target_scope.value,
            "target_tenant_id": (
                str(target_tenant_id) if target_tenant_id is not None else None
            ),
            "sanitized_content_hash": sanitized_content_hash,
            "subscription_id": str(subscription.id),
        },
    )

    return {
        "approval_id": str(approval_id),
        "hub_pack_id": hub_pack.id,
        "hub_version_id": hub_version.id,
        "hub_version_no": hub_version.version_no,
        "deduped": deduped,
        "subscription_id": subscription.id,
    }


async def _retire_active(
    repo: HubSkillPackVersionRepository, *, hub_pack_id: uuid.UUID
) -> None:
    """Flip the current ``is_active=true`` row off so the new winner
    can take the slot inside the same transaction. M3.1 promised
    "at most one is_active per hub_pack" — this enforces it.
    """
    current = await repo.get_active(hub_pack_id=hub_pack_id)
    if current is None:
        return
    current.is_active = False
    await repo.session.flush([current])


async def _ensure_subscription(
    repo: WorkspaceHubSubscriptionRepository,
    *,
    workspace_id: uuid.UUID,
    hub_pack: HubSkillPack,
    identity_id: uuid.UUID | None,
    auto_pull: bool,
    last_pulled_version_no: int | None,
) -> WorkspaceHubSubscription:
    existing = await repo.get_by_pack(
        workspace_id=workspace_id, hub_pack_id=hub_pack.id
    )
    if existing is not None:
        existing.auto_pull = auto_pull
        if last_pulled_version_no is not None:
            existing.last_pulled_version_no = last_pulled_version_no
            existing.last_pulled_at = utcnow_naive()
        await repo.session.flush([existing])
        return existing
    sub = await repo.create(
        workspace_id=workspace_id,
        hub_pack_id=hub_pack.id,
        auto_pull=auto_pull,
        last_pulled_version_no=last_pulled_version_no,
        last_pulled_at=utcnow_naive() if last_pulled_version_no else None,
        subscribed_by_identity_id=identity_id,
    )
    await repo.session.flush([sub])
    return sub


async def _synthesize_actor(
    db: AsyncSession, *, approval_row: Approval
) -> Identity:
    """Return a placeholder identity when the requester is gone.

    The eligibility checks inside :func:`preview_promotion` only read
    ``actor.platform_role``, and for an apply-time race we treat the
    original approval body as the source of truth — the role check
    already passed once at propose time. We synthesise a USER-role
    stand-in so a TENANT-scope apply still works; a PLATFORM-scope
    apply would fail the recheck which is the safe default.
    """
    placeholder = Identity(
        email=f"deleted+{approval_row.id}@local",
        name="(deleted)",
        platform_role=PlatformRole.USER,
    )
    placeholder.id = approval_row.requested_by_identity_id or uuid.uuid4()
    _ = db
    return placeholder


# ── Subscribe / unsubscribe ──────────────────────────────────
async def subscribe(
    db: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    hub_pack_id: uuid.UUID,
    auto_pull: bool,
    actor_identity_id: uuid.UUID | None,
    request: Any = None,
) -> WorkspaceHubSubscription:
    """Idempotent subscribe.

    Re-clicking *Subscribe* with a different ``auto_pull`` value
    toggles the existing row rather than creating a duplicate
    (the unique ``(workspace_id, hub_pack_id)`` constraint enforces
    this at the DB layer too). Caller commits.
    """
    await hub_svc.require_hub_enabled(db)
    pack = await hub_svc.get_hub_pack_visible(
        db, hub_pack_id=hub_pack_id, workspace_id=workspace_id
    )

    repo = WorkspaceHubSubscriptionRepository(db)
    sub = await repo.get_by_pack(
        workspace_id=workspace_id, hub_pack_id=hub_pack_id
    )
    created: bool
    if sub is None:
        sub = await repo.create(
            workspace_id=workspace_id,
            hub_pack_id=hub_pack_id,
            auto_pull=auto_pull,
            last_pulled_version_no=None,
            last_pulled_at=None,
            subscribed_by_identity_id=actor_identity_id,
        )
        created = True
    else:
        sub.auto_pull = auto_pull
        if sub.subscribed_by_identity_id is None and actor_identity_id is not None:
            sub.subscribed_by_identity_id = actor_identity_id
        created = False
    await db.flush([sub])

    await audit_svc.record(
        db,
        action=AUDIT_SUBSCRIPTION_CREATED,
        actor_identity_id=actor_identity_id,
        workspace_id=workspace_id,
        resource_type="hub_skill_pack",
        resource_id=hub_pack_id,
        summary=(
            f"hub subscription {'created' if created else 'updated'}: "
            f"{pack.slug!r} (auto_pull={auto_pull})"
        ),
        metadata={
            "subscription_id": str(sub.id),
            "hub_pack_id": str(hub_pack_id),
            "hub_pack_slug": pack.slug,
            "auto_pull": auto_pull,
            "created": created,
        },
        request=request,
    )
    return sub


async def unsubscribe(
    db: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    hub_pack_id: uuid.UUID,
    actor_identity_id: uuid.UUID | None,
    request: Any = None,
) -> None:
    """Hard-delete the subscription row.

    The subscription has no soft-delete column (M3.1 keeps it
    minimal) so unsubscribe physically removes the row. The audit
    feed retains the cursor + slug for forensic queries. Caller
    commits.
    """
    await hub_svc.require_hub_enabled(db)
    repo = WorkspaceHubSubscriptionRepository(db)
    sub = await repo.get_by_pack(
        workspace_id=workspace_id, hub_pack_id=hub_pack_id
    )
    if sub is None:
        raise HubSubscriptionNotFound(
            "hub_subscription_not_found",
            code="hub.subscription_not_found",
        )
    metadata = {
        "subscription_id": str(sub.id),
        "hub_pack_id": str(hub_pack_id),
        "auto_pull": sub.auto_pull,
        "last_pulled_version_no": sub.last_pulled_version_no,
        "last_pulled_at": (
            sub.last_pulled_at.isoformat() if sub.last_pulled_at else None
        ),
    }
    await repo.hard_delete(sub)

    await audit_svc.record(
        db,
        action=AUDIT_SUBSCRIPTION_DELETED,
        actor_identity_id=actor_identity_id,
        workspace_id=workspace_id,
        resource_type="hub_skill_pack",
        resource_id=hub_pack_id,
        summary=f"hub subscription deleted: pack {hub_pack_id}",
        metadata=metadata,
        request=request,
    )


# ── Pull (manual + auto) ─────────────────────────────────────
async def pull_now(
    db: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    hub_pack_id: uuid.UUID,
    actor_identity_id: uuid.UUID | None,
    request: Any = None,
) -> HubPullResult:
    """Materialise the hub's active version into a local DRAFT pack.

    Behaviour:

    1. Subscription must exist (404 otherwise — the M3.3 brief makes
       subscribe a prerequisite for both manual and auto pull).
    2. Visibility on the hub pack is enforced via
       :func:`hub_svc.get_hub_pack_visible` so a tenant can't pull a
       sibling-tenant pack even if the subscription row leaked.
    3. ``last_pulled_version_no >= hub.active.version_no`` →
       ``up_to_date`` no-op.
    4. Otherwise create / reuse the local
       :class:`SkillPack(state=DRAFT)` (slug = hub pack slug) and
       insert a :class:`SkillPackVersion(state=PROPOSED)` carrying
       the hub body. Update the subscription cursor + audit
       ``hub.pulled``. The version stays PROPOSED — the M2.4
       verifier still has to clear it before the runtime injection
       picks it up.

    Caller commits.
    """
    await hub_svc.require_hub_enabled(db)
    pack = await hub_svc.get_hub_pack_visible(
        db, hub_pack_id=hub_pack_id, workspace_id=workspace_id
    )

    sub_repo = WorkspaceHubSubscriptionRepository(db)
    sub = await sub_repo.get_by_pack(
        workspace_id=workspace_id, hub_pack_id=hub_pack_id
    )
    if sub is None:
        raise HubSubscriptionNotFound(
            "hub_subscription_not_found",
            code="hub.subscription_not_found",
        )

    version_repo = HubSkillPackVersionRepository(db)
    active = await version_repo.get_active(hub_pack_id=hub_pack_id)
    if active is None:
        return HubPullResult(
            status=_PULL_STATUS_NO_ACTIVE_VERSION,
            hub_pack_id=hub_pack_id,
        )

    if (
        sub.last_pulled_version_no is not None
        and sub.last_pulled_version_no >= active.version_no
    ):
        await audit_svc.record(
            db,
            action=AUDIT_PULLED_UP_TO_DATE,
            actor_identity_id=actor_identity_id,
            workspace_id=workspace_id,
            resource_type="hub_skill_pack",
            resource_id=hub_pack_id,
            summary=(
                f"hub pull skipped (up to date): pack {pack.slug!r} "
                f"v{active.version_no}"
            ),
            metadata={
                "hub_pack_id": str(hub_pack_id),
                "hub_pack_slug": pack.slug,
                "hub_active_version_no": active.version_no,
                "last_pulled_version_no": sub.last_pulled_version_no,
            },
            request=request,
        )
        return HubPullResult(
            status=_PULL_STATUS_UP_TO_DATE,
            hub_pack_id=hub_pack_id,
            hub_version_no=active.version_no,
        )

    local_pack = await _ensure_local_pack_for_pull(
        db,
        workspace_id=workspace_id,
        hub_pack=pack,
        actor_identity_id=actor_identity_id,
    )
    local_version = await _create_local_pulled_version(
        db,
        workspace_id=workspace_id,
        local_pack=local_pack,
        hub_active=active,
        actor_identity_id=actor_identity_id,
    )

    sub.last_pulled_version_no = active.version_no
    sub.last_pulled_at = utcnow_naive()
    await db.flush([sub])

    await audit_svc.record(
        db,
        action=AUDIT_PULLED,
        actor_identity_id=actor_identity_id,
        workspace_id=workspace_id,
        resource_type="skill_pack_version",
        resource_id=local_version.id,
        summary=(
            f"hub pull: {pack.slug!r} v{active.version_no} → "
            f"local pack {local_pack.id} v{local_version.version_no} (PROPOSED)"
        ),
        metadata={
            "hub_pack_id": str(hub_pack_id),
            "hub_pack_slug": pack.slug,
            "hub_version_id": str(active.id),
            "hub_version_no": active.version_no,
            "local_pack_id": str(local_pack.id),
            "local_pack_state": local_pack.state.value,
            "local_version_id": str(local_version.id),
            "local_version_no": local_version.version_no,
            "subscription_id": str(sub.id),
        },
        request=request,
    )

    return HubPullResult(
        status=_PULL_STATUS_PULLED,
        hub_pack_id=hub_pack_id,
        hub_version_no=active.version_no,
        local_pack_id=local_pack.id,
        local_version_id=local_version.id,
        local_version_no=local_version.version_no,
    )


async def _ensure_local_pack_for_pull(
    db: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    hub_pack: HubSkillPack,
    actor_identity_id: uuid.UUID | None,
) -> SkillPack:
    """Find or create the local SkillPack mirror for a hub pull.

    Reuses an existing local row when one with the same slug is
    present (stays workspace-scoped) so a workspace that promoted
    pack X gets future hub updates merged into the same row instead
    of acquiring an unbounded number of `X-1`, `X-2`, … duplicates.
    """
    repo = SkillPackRepository(db)
    existing = await repo.get_by_slug(workspace_id=workspace_id, slug=hub_pack.slug)
    if existing is not None:
        return existing

    pack = await repo.create(
        workspace_id=workspace_id,
        slug=hub_pack.slug,
        name=hub_pack.name,
        description=hub_pack.description,
        version="0.0.0",
        publisher=None,
        signature=None,
        source=SkillPackSource.IMPORTED,
        manifest_json={},
        enabled=False,
        metadata_json={
            "hub": {
                "hub_pack_id": str(hub_pack.id),
                "scope": hub_pack.scope.value,
                "tenant_id": (
                    str(hub_pack.tenant_id) if hub_pack.tenant_id else None
                ),
            }
        },
        created_by=actor_identity_id,
        state=SkillPackState.DRAFT,
    )
    await db.flush([pack])
    return pack


async def _create_local_pulled_version(
    db: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    local_pack: SkillPack,
    hub_active: HubSkillPackVersion,
    actor_identity_id: uuid.UUID | None,
) -> Any:
    """Insert the SkillPackVersion(state=PROPOSED) for a hub pull.

    Reuses an existing version with the same content_hash when one
    is present so a re-pull of the same body is a no-op for storage.
    Source provenance is empty by design (``source_run_ids=[]``) —
    hub-pulled bodies have no local run history.
    """
    repo = SkillPackVersionRepository(db)
    existing = await repo.find_by_hash(
        workspace_id=workspace_id,
        pack_id=local_pack.id,
        content_hash=hub_active.content_hash,
    )
    if existing is not None:
        return existing
    next_no = await repo.next_version_no(
        workspace_id=workspace_id, pack_id=local_pack.id
    )
    version = await repo.create(
        workspace_id=workspace_id,
        pack_id=local_pack.id,
        version_no=next_no,
        content_hash=hub_active.content_hash,
        content_md=hub_active.content_md or "",
        files_json=dict(hub_active.files_json or {}),
        state=SkillPackVersionState.PROPOSED,
        created_by="hub_pull",
        creator_identity_id=actor_identity_id,
        source_run_ids=[],
        validation_results={
            "hub_provenance": {
                "hub_pack_id": str(hub_active.hub_pack_id),
                "hub_version_id": str(hub_active.id),
                "hub_version_no": hub_active.version_no,
            }
        },
    )
    await db.flush([version])
    return version


# Re-export PermissionDenied / NotFound on the module surface so
# callers don't have to know which submodule the typed error lives
# in.
_ = (PermissionDenied, NotFound, cast)
