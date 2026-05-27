"""Current identity endpoints."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel, Field

from app.api.deps import CurrentIdentityId, CurrentWorkspaceId, DBSession
from app.core.errors import NotFound, PermissionDenied
from app.core.rate_limit import rate_limit
from app.db.models.user_profile import UserProfileDimension, UserProfileFact
from app.db.models.workspace_creation_log import CreationKind
from app.repositories.identity import IdentityRepository
from app.repositories.user_profile import UserProfileFactRepository
from app.repositories.workspace import MembershipRepository
from app.schemas.identity import (
    IdentityRead,
    IdentityUpdate,
    MembershipBrief,
    MeOut,
    PasswordChangeIn,
)
from app.schemas.user_profile import (
    UserProfileBundle,
    UserProfileDimensionView,
    UserProfileExtractNowResult,
    UserProfileFactRead,
)
from app.services import audit as audit_svc
from app.services import auth as auth_svc
from app.services import notification_events as notif_events
from app.services import permissions as perm
from app.services import session_user_prefs as user_prefs_svc
from app.services import user_profile as user_profile_svc
from app.services import workspace_quota as quota_svc

router = APIRouter()


class WorkspaceQuotaOut(BaseModel):
    """Per-identity quota snapshot served to the active session.

    Drives the WorkspaceSwitcher "+ New Workspace" disable/tooltip
    decision and the ``/settings/workspace`` quota card.
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


@router.get("", response_model=MeOut)
async def read_me(
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
) -> MeOut:
    identity = await IdentityRepository(db).get(identity_id)
    if identity is None:
        raise NotFound("identity_not_found", code="identity.not_found")

    pairs = await MembershipRepository(db).list_with_workspace_for_identity(identity_id)
    memberships = [
        MembershipBrief(
            workspace_id=ws.id,
            workspace_name=ws.name,
            workspace_slug=ws.slug,
            role=mem.role,
            department_id=mem.department_id,
        )
        for mem, ws in pairs
    ]

    out = MeOut.model_validate(identity)
    out.workspaces = memberships
    out.current_workspace_id = workspace_id or (
        memberships[0].workspace_id if memberships else None
    )
    raw_locale = (identity.profile_json or {}).get("locale")
    out.preferred_locale = raw_locale if isinstance(raw_locale, str) and raw_locale else None

    # Surface role + permissions of the active workspace. Falls back to the
    # first membership when no specific workspace is requested.
    active_ws = out.current_workspace_id
    if active_ws is not None:
        active_mem = next((m for m, _ws in pairs if m.workspace_id == active_ws), None)
        if active_mem is not None:
            out.current_role = active_mem.role
            out.current_department_id = active_mem.department_id
            out.permissions = sorted(perm.capabilities_for(active_mem.role))
    return out


# Locales the frontend's next-intl bundle ships. Kept in sync with
# ``frontend/src/lib/i18n.ts`` ``locales``; any new locale needs both
# sides flipped on.
_SUPPORTED_LOCALES: frozenset[str] = frozenset({"en-US", "zh-CN"})


@router.patch("", response_model=IdentityRead)
async def update_me(
    body: IdentityUpdate,
    db: DBSession,
    identity_id: CurrentIdentityId,
) -> IdentityRead:
    repo = IdentityRepository(db)
    identity = await repo.get(identity_id)
    if identity is None:
        raise NotFound("identity_not_found", code="identity.not_found")

    payload = body.model_dump(exclude_none=True)
    preferred_locale = payload.pop("preferred_locale", None)
    if preferred_locale is not None:
        # Merge the locale onto ``profile_json`` so the existing JSONB
        # column stays the source of truth — keeps schema migrations off
        # the critical path.
        cleaned = preferred_locale.strip()
        if cleaned and cleaned not in _SUPPORTED_LOCALES:
            from app.core.errors import ValidationFailed

            raise ValidationFailed(
                f"unsupported locale: {preferred_locale!r}",
                code="identity.unsupported_locale",
            )
        merged_profile = dict(identity.profile_json or {})
        if cleaned:
            merged_profile["locale"] = cleaned
        else:
            merged_profile.pop("locale", None)
        # If the caller also sent ``profile_json`` directly, the
        # convenience field wins for the ``locale`` key only.
        if "profile_json" in payload and isinstance(payload["profile_json"], dict):
            payload["profile_json"] = {
                **payload["profile_json"],
                **{k: v for k, v in merged_profile.items() if k == "locale"},
            }
            if not cleaned:
                payload["profile_json"].pop("locale", None)
        else:
            payload["profile_json"] = merged_profile

    updated = await repo.update(identity, **payload)
    await db.commit()
    return IdentityRead.model_validate(updated)


@router.post("/password", status_code=status.HTTP_204_NO_CONTENT)
async def change_password(
    body: PasswordChangeIn,
    db: DBSession,
    identity_id: CurrentIdentityId,
) -> None:
    identity = await IdentityRepository(db).get(identity_id)
    if identity is None:
        raise NotFound("identity_not_found", code="identity.not_found")
    await auth_svc.change_password(
        db, identity=identity, old_password=body.old_password, new_password=body.new_password
    )
    await db.commit()


# ─── Chat preferences (per-agent model picks) ─────────────
class ChatModelPrefRead(BaseModel):
    """Map of agent_id (or ``"default"``) → ``"provider:model"`` selector.

    The frontend uses this to remember the user's last picked model per
    agent so the ``ModelSelector`` in the composer pre-selects it on the
    next visit.
    """

    prefs: dict[str, str] = Field(default_factory=dict)


class ChatModelPrefUpdate(BaseModel):
    """Set / clear the caller's preferred model for one agent.

    ``agent_id=None`` writes the **global default** that applies whenever no
    agent-specific entry is set. ``model=None`` clears the entry entirely.
    """

    agent_id: uuid.UUID | None = None
    model: str | None = Field(
        default=None,
        max_length=256,
        description=(
            "``provider:model`` selector (e.g. ``openai:gpt-4o-mini``). "
            "``null`` clears the saved pref for this agent."
        ),
    )


@router.get(
    "/workspace-quota",
    response_model=WorkspaceQuotaOut,
    dependencies=[
        Depends(rate_limit("workspace_quota_read", limit=30, period_seconds=60)),
    ],
)
async def read_workspace_quota(db: DBSession, identity_id: CurrentIdentityId) -> WorkspaceQuotaOut:
    """Return the caller's effective workspace creation budget."""
    snapshot = await quota_svc.get_quota(db, identity_id=identity_id)
    return WorkspaceQuotaOut(
        used=snapshot.used,
        limit=snapshot.limit,
        remaining=snapshot.remaining,
        creation_kind_allowed=snapshot.creation_kind_allowed,
        rate_window_used=snapshot.rate_window_used,
        rate_window_limit=snapshot.rate_window_limit,
        rate_window_seconds=snapshot.rate_window_seconds,
        source_kind=snapshot.source_kind,
        override_active=snapshot.override_active,
        grandfathered=snapshot.grandfathered,
    )


@router.get("/preferences/models", response_model=ChatModelPrefRead)
async def read_model_prefs(
    identity_id: CurrentIdentityId,
) -> ChatModelPrefRead:
    """Every saved ``chat_model_prefs`` entry for the caller."""
    prefs = await user_prefs_svc.list_model_prefs(identity_id=identity_id)
    return ChatModelPrefRead(prefs=prefs)


@router.put("/preferences/models", response_model=ChatModelPrefRead)
async def update_model_pref(
    body: ChatModelPrefUpdate,
    identity_id: CurrentIdentityId,
) -> ChatModelPrefRead:
    """Persist one preference (or clear it) and return the full updated map.

    The WS handler picks this up on the next ``user_message`` frame: when no
    explicit per-turn ``model`` is sent, ``RunRequest.model_override`` falls
    back to the value stored here.
    """
    prefs = await user_prefs_svc.set_model_pref(
        identity_id=identity_id,
        agent_id=body.agent_id,
        model=body.model,
    )
    return ChatModelPrefRead(prefs=prefs)


# ─── Notification preferences (M0.10) ─────────────────────
class NotificationEventDescriptorOut(BaseModel):
    """Static descriptor surfaced so the prefs UI can render labels.

    Mirrors :class:`app.services.notification_events.EventDescriptor`
    but ships only the fields the frontend cares about. The full
    registry lives on the backend.
    """

    key: str
    title_key: str
    message_key: str
    default_channels: list[str]
    default_urgency: str
    cooldown_seconds: int
    target_audience: str
    requires_email: bool


class NotificationPrefEntry(BaseModel):
    """Single per-identity preference row (one event_key)."""

    channels: list[str] = Field(default_factory=list)
    muted: bool = False


class NotificationGlobalPref(BaseModel):
    """Vacation / quiet-hours mute window."""

    muted_until: str | None = None


class NotificationPrefsRead(BaseModel):
    """Effective preferences plus the catalog the UI renders against."""

    prefs: dict[str, NotificationPrefEntry] = Field(default_factory=dict)
    global_: NotificationGlobalPref = Field(default_factory=NotificationGlobalPref, alias="_global")
    catalog: list[NotificationEventDescriptorOut] = Field(default_factory=list)

    model_config = {"populate_by_name": True}


class NotificationPrefsUpdate(BaseModel):
    """Replace-style write — merge happens server-side, validated here."""

    prefs: dict[str, NotificationPrefEntry] = Field(default_factory=dict)
    global_: NotificationGlobalPref = Field(default_factory=NotificationGlobalPref, alias="_global")

    model_config = {"populate_by_name": True}


def _filter_catalog_for(identity) -> list[NotificationEventDescriptorOut]:
    """Hide platform-only events from non-platform-admin callers."""
    is_platform_admin = (
        getattr(identity, "platform_role", None) == "platform_admin"
        or str(getattr(identity, "platform_role", "")) == "platform_admin"
    )
    visible: list[NotificationEventDescriptorOut] = []
    for descriptor in notif_events.EVENT_REGISTRY.values():
        if descriptor.target_audience == "platform_admins" and not is_platform_admin:
            continue
        visible.append(
            NotificationEventDescriptorOut(
                key=descriptor.key,
                title_key=descriptor.title_key,
                message_key=descriptor.message_key,
                default_channels=[c.value for c in descriptor.default_channels],
                default_urgency=descriptor.default_urgency.value,
                cooldown_seconds=descriptor.cooldown_seconds,
                target_audience=descriptor.target_audience,
                requires_email=descriptor.requires_email,
            )
        )
    return visible


@router.get(
    "/notification-prefs",
    response_model=NotificationPrefsRead,
    dependencies=[Depends(rate_limit("notification_prefs_read", limit=30, period_seconds=60))],
)
async def read_notification_prefs(
    db: DBSession, identity_id: CurrentIdentityId
) -> NotificationPrefsRead:
    """Return current prefs + the catalog for rendering."""
    identity = await IdentityRepository(db).get(identity_id)
    if identity is None:
        raise NotFound("identity_not_found", code="identity.not_found")

    raw = identity.notification_prefs_json or {}
    prefs: dict[str, NotificationPrefEntry] = {}
    glob = NotificationGlobalPref()
    if isinstance(raw, dict):
        for key, value in raw.items():
            if key == "_global" and isinstance(value, dict):
                glob = NotificationGlobalPref(**value)
                continue
            if not isinstance(value, dict):
                continue
            try:
                prefs[key] = NotificationPrefEntry(**value)
            except Exception:
                continue

    return NotificationPrefsRead(
        prefs=prefs,
        _global=glob,
        catalog=_filter_catalog_for(identity),
    )


@router.put(
    "/notification-prefs",
    response_model=NotificationPrefsRead,
    dependencies=[Depends(rate_limit("notification_prefs_write", limit=10, period_seconds=60))],
)
async def update_notification_prefs(
    body: NotificationPrefsUpdate,
    db: DBSession,
    identity_id: CurrentIdentityId,
) -> NotificationPrefsRead:
    """Replace per-identity prefs. ``requires_email`` events validate the
    EMAIL channel cannot be removed (security guarantee)."""
    identity = await IdentityRepository(db).get(identity_id)
    if identity is None:
        raise NotFound("identity_not_found", code="identity.not_found")

    is_platform_admin = str(getattr(identity, "platform_role", "")) == "platform_admin"
    valid_channels = {"in_app", "email"}
    cleaned: dict[str, dict] = {}
    fields_changed: list[str] = []
    for key, entry in body.prefs.items():
        descriptor = notif_events.EVENT_REGISTRY.get(key)
        if descriptor is None:
            continue
        if descriptor.target_audience == "platform_admins" and not is_platform_admin:
            continue
        chans: list[str] = []
        for c in entry.channels:
            if c not in valid_channels:
                continue
            chans.append(c)
        if descriptor.requires_email and "email" not in chans and not entry.muted:
            chans.append("email")
        cleaned[key] = {"channels": sorted(set(chans)), "muted": bool(entry.muted)}
        fields_changed.append(key)

    if body.global_.muted_until:
        cleaned["_global"] = {"muted_until": body.global_.muted_until}
        fields_changed.append("_global")

    identity.notification_prefs_json = cleaned
    db.add(identity)
    await db.flush()
    await audit_svc.record(
        db,
        action="notification.preferences_updated",
        actor_identity_id=identity_id,
        workspace_id=None,
        resource_type="identity",
        resource_id=identity_id,
        summary="notification preferences updated",
        metadata={
            "identity_id_hash": uuid.uuid5(uuid.NAMESPACE_OID, str(identity_id)).hex[:16],
            "fields_changed": fields_changed,
        },
    )
    await db.commit()
    await db.refresh(identity)

    return await read_notification_prefs(db=db, identity_id=identity_id)


# ─── User profile (M3.7 Honcho-style 12-dim dialectic model) ───
def _serialize_fact(row: UserProfileFact) -> UserProfileFactRead:
    return UserProfileFactRead.model_validate(row)


def _build_dimension_view(
    *,
    dimension: UserProfileDimension,
    rows: list[UserProfileFact],
) -> UserProfileDimensionView:
    """Pick the active row + collect the trailing history.

    Mirrors the renderer's filter so the UI shows the same "would
    inject?" verdict the runtime would produce. The history list
    keeps the most recent ``MAX_HISTORY`` candidates including the
    superseded chain so the user can replay the dialectic process
    that led to the current bullet.
    """
    MAX_HISTORY = 10
    eligible = [
        r
        for r in rows
        if r.deleted_at is None
        and r.dimension == dimension
        and r.superseded_by_id is None
        and not r.user_rejected
        and (
            r.user_confirmed
            or float(r.confidence) >= user_profile_svc.AUTO_INJECT_CONFIDENCE_THRESHOLD
        )
    ]
    eligible.sort(
        key=lambda r: (
            bool(r.user_confirmed),
            float(r.confidence),
            r.created_at,
        ),
        reverse=True,
    )
    active = eligible[0] if eligible else None

    history = [r for r in rows if r.dimension == dimension][:MAX_HISTORY]
    pending = sum(
        1
        for r in history
        if not r.user_confirmed
        and not r.user_rejected
        and float(r.confidence) < user_profile_svc.AUTO_INJECT_CONFIDENCE_THRESHOLD
    )
    rejected = sum(1 for r in history if r.user_rejected)

    return UserProfileDimensionView(
        dimension=dimension,
        active=_serialize_fact(active) if active else None,
        history=[_serialize_fact(r) for r in history],
        pending_count=pending,
        rejected_count=rejected,
    )


@router.get(
    "/profile",
    response_model=UserProfileBundle,
    dependencies=[
        Depends(rate_limit("me_profile_read", limit=60, period_seconds=60)),
    ],
)
async def read_user_profile(
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
) -> UserProfileBundle:
    """Active fact + history for every dimension in the active workspace."""
    if workspace_id is None:
        raise NotFound("workspace_required", code="workspace.required")
    repo = UserProfileFactRepository(db)
    rows = list(
        await repo.list_for_identity(
            workspace_id=workspace_id,
            identity_id=identity_id,
            limit=500,
        )
    )

    views: list[UserProfileDimensionView] = []
    for dim in UserProfileDimension:
        views.append(_build_dimension_view(dimension=dim, rows=rows))

    rendered = await user_profile_svc.render_facts_for_injection(
        db,
        workspace_id=workspace_id,
        identity_id=identity_id,
    )
    last_extracted = max((r.created_at for r in rows), default=None) if rows else None

    return UserProfileBundle(
        workspace_id=workspace_id,
        identity_id=identity_id,
        dimensions=views,
        rendered_chars=len(rendered or ""),
        last_extracted_at=last_extracted,
    )


@router.post(
    "/profile/{fact_id}/confirm",
    response_model=UserProfileFactRead,
    dependencies=[
        Depends(rate_limit("me_profile_action", limit=30, period_seconds=60)),
    ],
)
async def confirm_user_profile_fact(
    fact_id: uuid.UUID,
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
) -> UserProfileFactRead:
    """Promote one row to ``user_confirmed=True`` (always-inject)."""
    if workspace_id is None:
        raise NotFound("workspace_required", code="workspace.required")
    fact = await user_profile_svc.confirm_fact(
        db,
        workspace_id=workspace_id,
        fact_id=fact_id,
        identity_id=identity_id,
    )
    await db.commit()
    return _serialize_fact(fact)


@router.post(
    "/profile/{fact_id}/reject",
    response_model=UserProfileFactRead,
    dependencies=[
        Depends(rate_limit("me_profile_action", limit=30, period_seconds=60)),
    ],
)
async def reject_user_profile_fact(
    fact_id: uuid.UUID,
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
) -> UserProfileFactRead:
    """Permanently mark one row as never-inject (``user_rejected=True``)."""
    if workspace_id is None:
        raise NotFound("workspace_required", code="workspace.required")
    fact = await user_profile_svc.reject_fact(
        db,
        workspace_id=workspace_id,
        fact_id=fact_id,
        identity_id=identity_id,
    )
    await db.commit()
    return _serialize_fact(fact)


@router.post(
    "/profile/extract-now",
    response_model=UserProfileExtractNowResult,
    dependencies=[
        Depends(rate_limit("me_profile_extract", limit=3, period_seconds=300)),
    ],
)
async def extract_user_profile_now(
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
) -> UserProfileExtractNowResult:
    """User-driven re-extract from the most recent runs.

    Tightly rate-limited (3 per 5 minutes) because each call burns
    one aux-LLM round-trip. The endpoint enforces workspace
    membership defensively even though the JWT already restricts
    the active workspace — a token refresh that re-pinned the
    identity to a different workspace can't reach an old workspace's
    rows here.
    """
    if workspace_id is None:
        raise NotFound("workspace_required", code="workspace.required")
    member = await user_profile_svc.identity_belongs_to_workspace(
        db, workspace_id=workspace_id, identity_id=identity_id
    )
    if not member:
        raise PermissionDenied(
            "workspace_membership_required",
            code="workspace.membership_required",
        )
    outcome = await user_profile_svc.extract_facts_from_runs(
        db,
        workspace_id=workspace_id,
        identity_id=identity_id,
        invocation_kind="manual",
        actor_identity_id=identity_id,
    )
    await db.commit()
    return UserProfileExtractNowResult(
        workspace_id=workspace_id,
        identity_id=identity_id,
        facts_created=int(outcome.facts_created),
        facts_superseded=int(outcome.facts_superseded),
        facts_unchanged=int(outcome.facts_unchanged),
        artifacts_examined=int(outcome.artifacts_examined),
        aux_skipped=bool(outcome.aux_skipped),
        aux_skip_reason=outcome.aux_skip_reason,
        duration_ms=int(outcome.duration_ms),
    )
