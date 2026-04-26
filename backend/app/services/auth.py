"""Authentication service: register / login / refresh / logout."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import Conflict, Unauthorized
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
from app.db.repository import AsyncRepository
from app.repositories.identity import IdentityRepository
from app.repositories.workspace import MembershipRepository


async def register(
    session: AsyncSession, *, email: str, name: str, password: str
) -> Identity:
    ident_repo = IdentityRepository(session)
    if await ident_repo.get_by_email(email):
        raise Conflict("email_taken", code="auth.email_taken")
    return await ident_repo.create(
        email=email.lower(),
        name=name,
        password_hash=hash_password(password),
        status=IdentityStatus.ACTIVE,
    )


async def authenticate(
    session: AsyncSession, *, email: str, password: str
) -> Identity:
    ident_repo = IdentityRepository(session)
    identity = await ident_repo.get_by_email(email.lower())
    if not identity or not identity.password_hash:
        raise Unauthorized("invalid_credentials", code="auth.invalid_credentials")
    if not verify_password(password, identity.password_hash):
        raise Unauthorized("invalid_credentials", code="auth.invalid_credentials")
    if identity.status != IdentityStatus.ACTIVE:
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
    # Default the active workspace to the first membership if unspecified.
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

    # Check blacklist
    bl_repo: AsyncRepository[TokenBlacklist] = AsyncRepository(session, TokenBlacklist)
    if await bl_repo.exists(jti=jti):
        raise Unauthorized("token_revoked", code="auth.token_revoked")

    # Confirm auth_session still active
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
    # Fallback: if the refresh token predates this fix, reconstruct from DB.
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
        return  # idempotent
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


# Exposed for seeding in P0-5.
async def get_first_workspace_for(session: AsyncSession, identity_id: uuid.UUID) -> Workspace | None:
    pairs = await MembershipRepository(session).list_with_workspace_for_identity(identity_id)
    return pairs[0][1] if pairs else None
