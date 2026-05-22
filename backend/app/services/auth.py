"""Authentication service: register / login / refresh / logout."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import (
    Conflict,
    CreationNotPermitted,
    InvitationRequired,
    QuotaExceeded,
    RegistrationClosed,
    Unauthorized,
)
from app.core.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    utcnow_naive,
    verify_password,
)
from app.db.models.auth_session import AuthSession
from app.db.models.identity import Identity, IdentityStatus
from app.db.models.token_blacklist import TokenBlacklist
from app.db.models.workspace import Workspace
from app.db.models.workspace_creation_log import CreationKind
from app.db.repository import AsyncRepository
from app.repositories.identity import IdentityRepository
from app.repositories.workspace import MembershipRepository
from app.services import audit as audit_svc
from app.services import email_verification as email_verify_svc
from app.services import workspace as workspace_svc
from app.services import workspace_quota as quota_svc
from app.services.personal_workspace import reserve_personal_workspace_slug
from app.services.system_settings import SystemSettingKey, get_system_setting


class RegistrationMode(StrEnum):
    OPEN_PERSONAL = "open_personal"
    OPEN_INVITE_ONLY = "open_invite_only"
    CLOSED = "closed"


_VALID_REGISTRATION_MODES = {m.value for m in RegistrationMode}


async def get_registration_mode(session: AsyncSession) -> RegistrationMode:
    raw = await get_system_setting(
        session, SystemSettingKey.REGISTRATION_MODE, default=RegistrationMode.OPEN_PERSONAL.value
    )
    if isinstance(raw, str) and raw in _VALID_REGISTRATION_MODES:
        return RegistrationMode(raw)
    return RegistrationMode.OPEN_PERSONAL


async def email_verification_required(session: AsyncSession) -> bool:
    raw = await get_system_setting(
        session, SystemSettingKey.AUTH_REQUIRE_EMAIL_VERIFICATION, default=False
    )
    return bool(raw)


@dataclass(slots=True)
class TokenPair:
    access_token: str
    access_expires_at: datetime
    refresh_token: str
    refresh_expires_at: datetime


@dataclass(slots=True)
class RegistrationResult:
    identity: Identity
    workspace: Workspace | None
    workspace_slug_warning: bool
    auto_login_tokens: TokenPair | None
    requires_email_verification: bool
    registration_mode: RegistrationMode
    verification_token: str | None


async def register(
    session: AsyncSession,
    *,
    email: str,
    name: str,
    password: str,
    invitation_code: str | None = None,
    create_personal_workspace: bool = True,
    request: Request | None = None,
) -> RegistrationResult:
    mode = await get_registration_mode(session)
    if mode == RegistrationMode.CLOSED:
        raise RegistrationClosed("registration_closed", code="auth.registration_closed")
    if (
        mode == RegistrationMode.OPEN_INVITE_ONLY
        and not invitation_code
        and create_personal_workspace
    ):
        raise InvitationRequired("invitation_code_required", code="auth.invitation_required")

    require_verification = await email_verification_required(session)

    ident_repo = IdentityRepository(session)
    if await ident_repo.get_by_email(email):
        raise Conflict("email_taken", code="auth.email_taken")

    initial_status = IdentityStatus.PENDING if require_verification else IdentityStatus.ACTIVE
    identity = await ident_repo.create(
        email=email.lower(),
        name=name,
        password_hash=hash_password(password),
        status=initial_status,
    )

    workspace: Workspace | None = None
    workspace_slug_warning = False
    workspace_provision_source: str | None = None

    if invitation_code:
        membership = await workspace_svc.accept_invitation(
            session, code=invitation_code, identity_id=identity.id
        )
        ws_repo: AsyncRepository[Workspace] = AsyncRepository(session, Workspace)
        workspace = await ws_repo.get(membership.workspace_id)
        workspace_provision_source = "invitation"
        await quota_svc.record_creation(
            session,
            identity_id=identity.id,
            workspace_id=membership.workspace_id,
            creation_kind=CreationKind.INVITATION_REDEEM,
            request=request,
            creation_source="invitation",
        )
    elif create_personal_workspace:
        # M0.12 — per-identity quota + rate-limit check before
        # provisioning a personal workspace. When the platform setting
        # ``creation_allowed_for_self_registered`` is False, the
        # identity is still created (so the user can sign in and an
        # admin can decide whether to bump their override) but the
        # workspace is *not* — the route layer surfaces the
        # ``workspace=None`` branch and the frontend explains the
        # operator decision.
        creation_kind = CreationKind.SELF_REGISTER
        try:
            await quota_svc.check_can_create(
                session,
                identity_id=identity.id,
                creation_kind=creation_kind,
                request=request,
            )
            blocked_by_quota = False
        except (CreationNotPermitted, QuotaExceeded):
            blocked_by_quota = True

        if not blocked_by_quota:
            slug, used_random = await reserve_personal_workspace_slug(session, email=email)
            workspace = await workspace_svc.create_workspace(
                session,
                name=f"{name}'s Workspace",
                slug=slug,
                owner_identity_id=identity.id,
            )
            workspace_slug_warning = used_random
            workspace_provision_source = "register"
            await quota_svc.record_creation(
                session,
                identity_id=identity.id,
                workspace_id=workspace.id,
                creation_kind=creation_kind,
                request=request,
                creation_source="register",
            )

    auto_login_tokens: TokenPair | None = None
    if (
        not require_verification
        and mode == RegistrationMode.OPEN_PERSONAL
        and workspace is not None
    ):
        access, access_exp, refresh, refresh_exp = await issue_tokens(
            session,
            identity=identity,
            workspace_id=workspace.id,
            user_agent=(request.headers.get("user-agent") if request else None),
            ip=(request.client.host if request and request.client else None),
        )
        auto_login_tokens = TokenPair(
            access_token=access,
            access_expires_at=access_exp,
            refresh_token=refresh,
            refresh_expires_at=refresh_exp,
        )

    verification_token: str | None = None
    if require_verification:
        verification_token = await email_verify_svc.issue_token(session, identity_id=identity.id)
        await email_verify_svc.send_verification_email(
            session, identity=identity, token=verification_token
        )

    await audit_svc.record(
        session,
        action="auth.registered",
        actor_identity_id=identity.id,
        workspace_id=workspace.id if workspace else None,
        resource_type="identity",
        resource_id=identity.id,
        summary=f"register {identity.email}",
        metadata={
            "registration_mode": mode.value,
            "workspace_slug_warning": workspace_slug_warning,
            "requires_email_verification": require_verification,
            "had_invitation_code": invitation_code is not None,
        },
        request=request,
    )
    if workspace is not None:
        await audit_svc.record(
            session,
            action="auth.workspace_provisioned",
            actor_identity_id=identity.id,
            workspace_id=workspace.id,
            resource_type="workspace",
            resource_id=workspace.id,
            summary=f"workspace {workspace.slug} provisioned at {workspace_provision_source}",
            metadata={
                "slug": workspace.slug,
                "source": workspace_provision_source,
            },
            request=request,
        )
        try:
            from app.services import notification_events as notif_events

            await notif_events.emit_event(
                session,
                event_key="auth.workspace_provisioned",
                workspace_id=workspace.id,
                actor_identity_id=identity.id,
                target_identity_ids=[identity.id],
                cooldown_resource_id=str(workspace.id),
                payload={
                    "workspace_id": str(workspace.id),
                    "workspace_name": workspace.name,
                    "workspace_slug": workspace.slug,
                    "source": workspace_provision_source or "register",
                    "user_name": identity.name,
                },
                request=request,
            )
        except Exception:  # pragma: no cover - notification best-effort
            import logging as _logging

            _logging.getLogger(__name__).exception(
                "notify auth.workspace_provisioned failed for ws=%s",
                workspace.id,
            )

    return RegistrationResult(
        identity=identity,
        workspace=workspace,
        workspace_slug_warning=workspace_slug_warning,
        auto_login_tokens=auto_login_tokens,
        requires_email_verification=require_verification,
        registration_mode=mode,
        verification_token=verification_token,
    )


async def authenticate(session: AsyncSession, *, email: str, password: str) -> Identity:
    ident_repo = IdentityRepository(session)
    identity = await ident_repo.get_by_email(email.lower())
    if not identity or not identity.password_hash:
        raise Unauthorized("invalid_credentials", code="auth.invalid_credentials")
    if not verify_password(password, identity.password_hash):
        raise Unauthorized("invalid_credentials", code="auth.invalid_credentials")
    if identity.status != IdentityStatus.ACTIVE:
        # PENDING vs SUSPENDED carries different remediation copy on the
        # frontend; surfacing the status as a stable code preserves that.
        if identity.status == IdentityStatus.PENDING:
            raise Unauthorized("email_unverified", code="auth.email_unverified")
        raise Unauthorized("identity_inactive", code="auth.identity_inactive")
    return identity


async def issue_tokens(
    session: AsyncSession,
    *,
    identity: Identity,
    workspace_id: uuid.UUID | None = None,
    roles: list[str] | None = None,
    user_agent: str | None = None,
    ip: str | None = None,
) -> tuple[str, datetime, str, datetime]:
    """Return `(access_token, access_expires_at, refresh_token, refresh_expires_at)`.

    Persists an `AuthSession` row for the refresh jti.
    """
    if workspace_id is None:
        memberships = await MembershipRepository(session).list_with_workspace_for_identity(
            identity.id
        )
        if memberships:
            workspace_id = memberships[0][1].id
            if roles is None:
                roles = [memberships[0][0].role]

    access_token, access_exp, _ = create_access_token(
        identity_id=str(identity.id),
        workspace_id=str(workspace_id) if workspace_id else None,
        roles=roles,
    )
    refresh_token, refresh_exp, refresh_jti = create_refresh_token(identity_id=str(identity.id))

    auth_repo: AsyncRepository[AuthSession] = AsyncRepository(session, AuthSession)
    await auth_repo.create(
        identity_id=identity.id,
        refresh_jti=refresh_jti,
        user_agent=user_agent,
        ip_address=ip,
        expires_at=refresh_exp,
    )

    return access_token, access_exp, refresh_token, refresh_exp


async def refresh_access_token(
    session: AsyncSession, *, refresh_token: str
) -> tuple[str, datetime]:
    payload = decode_token(refresh_token, expected_kind="refresh")
    jti = payload.get("jti")
    if not jti:
        raise Unauthorized("missing_jti", code="auth.missing_jti")

    bl_repo: AsyncRepository[TokenBlacklist] = AsyncRepository(session, TokenBlacklist)
    if await bl_repo.exists(jti=jti):
        raise Unauthorized("token_revoked", code="auth.token_revoked")

    auth_repo: AsyncRepository[AuthSession] = AsyncRepository(session, AuthSession)
    auth_sess = await auth_repo.get_by(refresh_jti=jti)
    if not auth_sess or auth_sess.revoked_at is not None:
        raise Unauthorized("session_revoked", code="auth.session_revoked")

    # CRITICAL: preserve the workspace + roles claims from the refresh token so
    # the newly-minted access token can still authorize workspace-scoped APIs.
    # Without this the new access token has no `ws` claim, every subsequent
    # API call 401s, triggering another refresh → user is kicked to /login.
    workspace_id: str | None = payload.get("ws")
    roles = payload.get("roles") or []
    if workspace_id is None:
        mems = await MembershipRepository(session).list_with_workspace_for_identity(
            uuid.UUID(payload["sub"])
        )
        if mems:
            workspace_id = str(mems[0][1].id)
            if not roles:
                roles = [mems[0][0].role]

    access_token, access_exp, _ = create_access_token(
        identity_id=payload["sub"],
        workspace_id=workspace_id,
        roles=roles or None,
    )
    return access_token, access_exp


async def logout(session: AsyncSession, *, refresh_token: str) -> None:
    try:
        payload = decode_token(refresh_token, expected_kind="refresh")
    except Unauthorized:
        return
    jti = payload.get("jti")
    if not jti:
        return
    bl_repo: AsyncRepository[TokenBlacklist] = AsyncRepository(session, TokenBlacklist)
    if not await bl_repo.exists(jti=jti):
        exp = (
            datetime.fromtimestamp(payload["exp"], tz=UTC).replace(tzinfo=None)
            if "exp" in payload
            else utcnow_naive() + timedelta(days=1)
        )
        await bl_repo.create(jti=jti, expires_at=exp, reason="logout")

    auth_repo: AsyncRepository[AuthSession] = AsyncRepository(session, AuthSession)
    auth_sess = await auth_repo.get_by(refresh_jti=jti)
    if auth_sess and auth_sess.revoked_at is None:
        await auth_repo.update(auth_sess, revoked_at=utcnow_naive())


async def change_password(
    session: AsyncSession, *, identity: Identity, old_password: str, new_password: str
) -> None:
    if not identity.password_hash or not verify_password(old_password, identity.password_hash):
        raise Unauthorized("invalid_credentials", code="auth.invalid_credentials")
    ident_repo = IdentityRepository(session)
    await ident_repo.update(identity, password_hash=hash_password(new_password))


async def get_first_workspace_for(
    session: AsyncSession, identity_id: uuid.UUID
) -> Workspace | None:
    pairs = await MembershipRepository(session).list_with_workspace_for_identity(identity_id)
    return pairs[0][1] if pairs else None
