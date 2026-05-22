"""Auth routes: register / login / refresh / logout."""

from __future__ import annotations

import logging
from urllib.parse import urlencode

from fastapi import APIRouter, Cookie, Depends, HTTPException, Request, Response, status
from fastapi.responses import RedirectResponse

from app.api.deps import DBSession
from app.api.helpers import sanitize_next_path
from app.core.config import settings
from app.core.errors import Conflict, Unauthorized
from app.core.oauth import oauth, registered_providers
from app.core.rate_limit import rate_limit
from app.db.models.identity import IdentityStatus
from app.schemas.auth import (
    LoginIn,
    RefreshOut,
    RegisterIn,
    RegistrationModeOut,
    RegistrationResponse,
    ResendVerificationIn,
    TokenOut,
    TokenPairOut,
    WorkspaceSummary,
)
from app.services import audit as audit_svc
from app.services import auth as svc
from app.services import email_verification as email_verify_svc
from app.services import mfa as mfa_svc
from app.services.auth import RegistrationMode

log = logging.getLogger(__name__)

router = APIRouter()


def _set_refresh_cookie(resp: Response, *, token: str) -> None:
    resp.set_cookie(
        key=settings.JWT_REFRESH_COOKIE_NAME,
        value=token,
        max_age=settings.JWT_REFRESH_TTL_SECONDS,
        httponly=True,
        secure=settings.JWT_REFRESH_COOKIE_SECURE,
        samesite="lax",
        path="/",
    )


def _clear_refresh_cookie(resp: Response) -> None:
    resp.delete_cookie(settings.JWT_REFRESH_COOKIE_NAME, path="/")


# Rate-limit tuning rationale (V1):
#   login     — 5 per minute per IP: enough for humans fat-fingering, too
#               low for credential-stuffing attacks on a single account.
#   register  — 3 per minute per IP: registration is rare; attackers
#               spamming accounts hit this fast.
#   refresh   — 30 per minute per IP: SPA tabs may refresh independently;
#               keep headroom for multi-tab users.
#   oauth_start — 10 per minute per IP: reasonable for actual use, blocks
#               spammy CSRF-state generation.
@router.post(
    "/register",
    response_model=RegistrationResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[
        Depends(
            rate_limit(
                "auth_register",
                limit=settings.AUTH_REGISTER_RATE_LIMIT,
                period_seconds=settings.AUTH_REGISTER_RATE_PERIOD,
            )
        )
    ],
)
async def register(
    body: RegisterIn, db: DBSession, request: Request, response: Response
) -> RegistrationResponse:
    result = await svc.register(
        db,
        email=body.email,
        name=body.name,
        password=body.password,
        invitation_code=body.invitation_code,
        request=request,
    )
    await db.commit()

    workspace = (
        WorkspaceSummary(
            id=result.workspace.id,
            name=result.workspace.name,
            slug=result.workspace.slug,
        )
        if result.workspace is not None
        else None
    )
    tokens: TokenPairOut | None = None
    if result.auto_login_tokens is not None:
        tokens = TokenPairOut(
            access_token=result.auto_login_tokens.access_token,
            refresh_token=result.auto_login_tokens.refresh_token,
            expires_at=result.auto_login_tokens.access_expires_at,
            refresh_expires_at=result.auto_login_tokens.refresh_expires_at,
        )
        _set_refresh_cookie(response, token=result.auto_login_tokens.refresh_token)

    return RegistrationResponse(
        identity_id=result.identity.id,
        email=result.identity.email,
        name=result.identity.name,
        status=result.identity.status,
        workspace=workspace,
        workspace_slug_warning=result.workspace_slug_warning,
        auto_login_tokens=tokens,
        requires_email_verification=result.requires_email_verification,
        registration_mode=result.registration_mode.value,
    )


@router.get(
    "/registration-mode",
    response_model=RegistrationModeOut,
    dependencies=[Depends(rate_limit("auth_meta_read", limit=60, period_seconds=60))],
)
async def get_registration_mode(db: DBSession) -> RegistrationModeOut:
    mode = await svc.get_registration_mode(db)
    require_verification = await svc.email_verification_required(db)
    return RegistrationModeOut(
        mode=mode.value,
        invitation_required=(mode == RegistrationMode.OPEN_INVITE_ONLY),
        requires_email_verification=require_verification,
    )


@router.post(
    "/verify-email/{token}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(rate_limit("auth_verify_email", limit=10, period_seconds=60))],
)
async def verify_email(token: str, db: DBSession, request: Request) -> Response:
    identity = await email_verify_svc.consume_token(db, token=token)
    await audit_svc.record(
        db,
        action="auth.email_verified",
        actor_identity_id=identity.id,
        resource_type="identity",
        resource_id=identity.id,
        summary=f"email verified for {identity.email}",
        request=request,
    )
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/resend-verification",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(rate_limit("auth_resend_verify", limit=3, period_seconds=300))],
)
async def resend_verification(
    body: ResendVerificationIn, db: DBSession, request: Request
) -> Response:
    from app.repositories.identity import IdentityRepository

    identity = await IdentityRepository(db).get_by_email(body.email.lower())
    if identity is None:
        # Indistinguishable from "ok" to outsiders so we don't leak which
        # emails exist in the platform.
        await db.commit()
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    if identity.status != IdentityStatus.PENDING:
        raise Conflict("identity_not_pending", code="auth.identity_not_pending")

    token = await email_verify_svc.issue_token(db, identity_id=identity.id)
    await email_verify_svc.send_verification_email(db, identity=identity, token=token)
    await audit_svc.record(
        db,
        action="auth.email_verification_resent",
        actor_identity_id=identity.id,
        resource_type="identity",
        resource_id=identity.id,
        summary=f"verification resent for {identity.email}",
        request=request,
    )
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/login",
    response_model=TokenOut,
    dependencies=[
        Depends(
            rate_limit(
                "auth_login",
                limit=settings.AUTH_LOGIN_RATE_LIMIT,
                period_seconds=settings.AUTH_LOGIN_RATE_PERIOD,
            )
        )
    ],
)
async def login(body: LoginIn, db: DBSession, request: Request, response: Response) -> TokenOut:
    try:
        identity = await svc.authenticate(db, email=body.email, password=body.password)
    except Unauthorized:
        # Log the failed attempt before re-raising so the UX error envelope
        # isn't affected.
        await audit_svc.record(
            db,
            action="auth.login_failed",
            actor_identity_id=None,
            resource_type="identity",
            summary=f"failed login for {body.email}",
            metadata={"email": body.email},
            request=request,
        )
        await db.commit()
        raise

    # Two-factor gate: if the identity has MFA enabled, the body must carry a
    # fresh TOTP code. We return a structured 401 so the UI knows to prompt
    # for the 6-digit code without wiping the password field.
    if mfa_svc.is_enabled(identity):
        code = (body.totp_code or "").strip()
        if not code:
            raise Unauthorized("mfa_required", code="auth.mfa_required")
        if not mfa_svc.verify_login_code(identity, code):
            await audit_svc.record(
                db,
                action="auth.mfa_failed",
                actor_identity_id=identity.id,
                resource_type="identity",
                resource_id=identity.id,
                summary=f"MFA failed for {identity.email}",
                request=request,
            )
            await db.commit()
            raise Unauthorized("mfa_invalid", code="auth.mfa_invalid")

    access, access_exp, refresh, _ = await svc.issue_tokens(
        db,
        identity=identity,
        user_agent=request.headers.get("user-agent"),
        ip=request.client.host if request.client else None,
    )
    # Tag the login with the user's primary workspace so workspace admins can
    # see "$user logged in" rows on their audit feed. Falls back to NULL for
    # users with no memberships (they'll only show up in platform-scope feeds).
    from app.repositories.workspace import MembershipRepository

    primary_ws = None
    mems = await MembershipRepository(db).list_with_workspace_for_identity(identity.id)
    if mems:
        primary_ws = mems[0][1].id

    await audit_svc.record(
        db,
        action="auth.login",
        actor_identity_id=identity.id,
        workspace_id=primary_ws,
        resource_type="identity",
        resource_id=identity.id,
        summary=f"login {identity.email}",
        request=request,
    )
    await db.commit()
    _set_refresh_cookie(response, token=refresh)
    return TokenOut(access_token=access, expires_at=access_exp)


@router.post(
    "/refresh",
    response_model=RefreshOut,
    dependencies=[Depends(rate_limit("auth_refresh", limit=30, period_seconds=60))],
)
async def refresh(
    db: DBSession,
    request: Request,
    response: Response,
    _cookie_refresh: str | None = Cookie(None, alias=settings.JWT_REFRESH_COOKIE_NAME),
) -> RefreshOut:
    refresh_token = _cookie_refresh or _bearer_from_header(request)
    if not refresh_token:
        raise Unauthorized("missing_refresh_token", code="auth.missing_refresh")
    access, access_exp = await svc.refresh_access_token(db, refresh_token=refresh_token)
    await db.commit()
    return RefreshOut(access_token=access, expires_at=access_exp)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    db: DBSession,
    request: Request,
    response: Response,
    _cookie_refresh: str | None = Cookie(None, alias=settings.JWT_REFRESH_COOKIE_NAME),
) -> Response:
    refresh_token = _cookie_refresh or _bearer_from_header(request)
    identity_id = None
    if refresh_token:
        # Best-effort: decode to capture the actor for audit; swallow failures
        # so logout always succeeds.
        try:
            import uuid as _uuid

            from app.core.security import decode_token

            payload = decode_token(refresh_token, expected_kind="refresh")
            identity_id = _uuid.UUID(payload["sub"])
        except Exception:  # pragma: no cover
            identity_id = None
        await svc.logout(db, refresh_token=refresh_token)
    await audit_svc.record(
        db,
        action="auth.logout",
        actor_identity_id=identity_id,
        resource_type="identity",
        resource_id=identity_id,
        summary="logout",
        request=request,
    )
    await db.commit()
    _clear_refresh_cookie(response)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


def _bearer_from_header(request: Request) -> str | None:
    header = request.headers.get("authorization", "")
    if header.lower().startswith("bearer "):
        return header.split(" ", 1)[1]
    return None


# ─── MFA (TOTP) ─────────────────────────────────────────────
from pydantic import BaseModel, Field

from app.api.deps import CurrentIdentityId
from app.repositories.identity import IdentityRepository


class MfaStatus(BaseModel):
    enabled: bool
    pending: bool


class MfaSetupOut(BaseModel):
    otpauth_uri: str
    secret: str


class MfaActivateIn(BaseModel):
    code: str = Field(min_length=6, max_length=8)


class MfaDisableIn(BaseModel):
    password: str = Field(min_length=1, max_length=128)


@router.get("/mfa/status", response_model=MfaStatus)
async def mfa_status(db: DBSession, identity_id: CurrentIdentityId) -> MfaStatus:
    ident = await IdentityRepository(db).get(identity_id)
    if ident is None:
        raise Unauthorized("no_identity", code="auth.no_identity")
    raw = ident.mfa_secret_ref or ""
    return MfaStatus(
        enabled=bool(raw) and not raw.startswith("pending:"),
        pending=raw.startswith("pending:"),
    )


@router.post("/mfa/setup", response_model=MfaSetupOut)
async def mfa_setup(db: DBSession, identity_id: CurrentIdentityId, request: Request) -> MfaSetupOut:
    """Begin TOTP enrollment. Returns a provisioning URI that the client
    renders as a QR code. The user then calls ``/mfa/activate`` with a fresh
    code to confirm.
    """
    ident = await IdentityRepository(db).get(identity_id)
    if ident is None:
        raise Unauthorized("no_identity", code="auth.no_identity")
    setup = await mfa_svc.setup(db, identity=ident)
    await audit_svc.record(
        db,
        action="auth.mfa_setup_started",
        actor_identity_id=identity_id,
        resource_type="identity",
        resource_id=identity_id,
        summary="MFA enrollment started",
        request=request,
    )
    await db.commit()
    return MfaSetupOut(otpauth_uri=setup.otpauth_uri, secret=setup.secret)


@router.post("/mfa/activate", status_code=status.HTTP_204_NO_CONTENT)
async def mfa_activate(
    body: MfaActivateIn,
    db: DBSession,
    identity_id: CurrentIdentityId,
    request: Request,
) -> Response:
    ident = await IdentityRepository(db).get(identity_id)
    if ident is None:
        raise Unauthorized("no_identity", code="auth.no_identity")
    ok = await mfa_svc.activate(db, identity=ident, code=body.code)
    if not ok:
        raise Unauthorized("mfa_invalid", code="auth.mfa_invalid")
    await audit_svc.record(
        db,
        action="auth.mfa_enabled",
        actor_identity_id=identity_id,
        resource_type="identity",
        resource_id=identity_id,
        summary="MFA enabled",
        request=request,
    )
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/mfa/disable", status_code=status.HTTP_204_NO_CONTENT)
async def mfa_disable(
    body: MfaDisableIn,
    db: DBSession,
    identity_id: CurrentIdentityId,
    request: Request,
) -> Response:
    """Disable MFA — requires the account password again (high-trust action)."""
    ident = await IdentityRepository(db).get(identity_id)
    if ident is None:
        raise Unauthorized("no_identity", code="auth.no_identity")
    from app.core.security import verify_password

    if not ident.password_hash or not verify_password(body.password, ident.password_hash):
        raise Unauthorized("invalid_credentials", code="auth.invalid_credentials")
    await mfa_svc.disable(db, identity=ident)
    await audit_svc.record(
        db,
        action="auth.mfa_disabled",
        actor_identity_id=identity_id,
        resource_type="identity",
        resource_id=identity_id,
        summary="MFA disabled",
        request=request,
    )
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ─── OAuth login ─────────────────────────────────────────
class OAuthProvidersOut(BaseModel):
    providers: list[str]


@router.get("/oauth/providers", response_model=OAuthProvidersOut)
async def oauth_providers() -> OAuthProvidersOut:
    """Names of OAuth providers currently configured — drives the login UI.
    Providers with missing env credentials don't appear."""
    return OAuthProvidersOut(providers=registered_providers())


def _frontend_base(request: Request) -> str:
    """Best-effort base URL for frontend redirects.

    If ``OAUTH_REDIRECT_BASE`` is set in env we use it verbatim; otherwise we
    pick the first CORS origin (the dev convention: localhost:3000) since
    that's where the SPA is running.
    """
    if settings.OAUTH_REDIRECT_BASE:
        return settings.OAUTH_REDIRECT_BASE.rstrip("/")
    origins = [o.strip() for o in settings.CORS_ALLOW_ORIGINS.split(",") if o.strip()]
    if origins:
        return origins[0].rstrip("/")
    # As a last resort reuse our own origin (same-origin deploys).
    return str(request.base_url).rstrip("/")


@router.get(
    "/oauth/{provider}/start",
    dependencies=[Depends(rate_limit("oauth_start", limit=10, period_seconds=60))],
)
async def oauth_start(provider: str, request: Request, next: str = "/") -> Response:
    """Kick off the OAuth dance. Redirects the browser to the provider."""
    if provider not in registered_providers():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "auth.oauth_provider_unknown", "provider": provider},
        )
    client = oauth.create_client(provider)
    if client is None:
        raise HTTPException(status_code=404, detail="provider_not_configured")
    # Where the IdP should redirect back to.
    redirect_uri = str(request.url_for("oauth_callback", provider=provider))
    # Stash the frontend ``next`` in the server session so the callback can
    # forward the user to a deep link after token issuance. We sanitise
    # at the point of storage so the session only ever holds safe values —
    # defence-in-depth against a later code path forgetting to validate.
    request.session["oauth_next"] = sanitize_next_path(next)
    return await client.authorize_redirect(request, redirect_uri)


@router.get("/oauth/{provider}/callback", name="oauth_callback")
async def oauth_callback(
    provider: str, request: Request, db: DBSession, response: Response
) -> Response:
    if provider not in registered_providers():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="provider_not_configured")
    client = oauth.create_client(provider)
    try:
        token = await client.authorize_access_token(request)
    except Exception as e:
        log.warning("oauth callback failed for %s: %s", provider, e)
        fe = _frontend_base(request)
        return RedirectResponse(
            f"{fe}/login?{urlencode({'error': 'oauth_failed', 'provider': provider})}"
        )

    profile = await _resolve_profile(client, provider, token)
    if profile is None:
        fe = _frontend_base(request)
        return RedirectResponse(f"{fe}/login?{urlencode({'error': 'oauth_profile_missing'})}")

    email = (profile.get("email") or "").lower()
    sub = str(profile.get("sub") or profile.get("id") or "")
    name = profile.get("name") or email.split("@", 1)[0]
    avatar_url = profile.get("avatar_url") or profile.get("picture")
    if not (email or sub):
        fe = _frontend_base(request)
        return RedirectResponse(f"{fe}/login?{urlencode({'error': 'oauth_insufficient_scope'})}")

    identity = await _provision_oauth_identity(
        db, provider=provider, sub=sub, email=email, name=name, avatar_url=avatar_url
    )

    # Issue the same cookies as a normal password login and bounce to the SPA.
    access, access_exp, refresh, _ = await svc.issue_tokens(
        db,
        identity=identity,
        user_agent=request.headers.get("user-agent"),
        ip=request.client.host if request.client else None,
    )
    await audit_svc.record(
        db,
        action="auth.oauth_login",
        actor_identity_id=identity.id,
        resource_type="identity",
        resource_id=identity.id,
        summary=f"OAuth login via {provider} for {identity.email}",
        metadata={"provider": provider},
        request=request,
    )
    await db.commit()

    next_path = request.session.pop("oauth_next", "/") or "/"
    next_path = sanitize_next_path(next_path)
    fe = _frontend_base(request)
    redirect = RedirectResponse(
        f"{fe}{next_path}?{urlencode({'access_token': access, 'expires_at': access_exp.isoformat()})}"
    )
    _set_refresh_cookie(redirect, token=refresh)
    return redirect


async def _resolve_profile(client, provider: str, token: dict) -> dict | None:  # type: ignore[no-untyped-def]
    """Normalize provider profile to a common shape."""
    try:
        if provider == "github":
            resp = await client.get("user", token=token)
            raw = resp.json() or {}
            email = raw.get("email")
            if not email:
                # GitHub users with private primary email need an extra call.
                emails = await client.get("user/emails", token=token)
                for e in emails.json() or []:
                    if e.get("primary") and e.get("verified"):
                        email = e.get("email")
                        break
            return {
                "id": raw.get("id"),
                "email": email,
                "name": raw.get("name") or raw.get("login"),
                "avatar_url": raw.get("avatar_url"),
            }
        if provider == "google":
            info = token.get("userinfo") or {}
            if not info:
                resp = await client.get(
                    "https://openidconnect.googleapis.com/v1/userinfo", token=token
                )
                info = resp.json() or {}
            return {
                "sub": info.get("sub"),
                "email": info.get("email"),
                "name": info.get("name"),
                "avatar_url": info.get("picture"),
            }
        if provider == "microsoft":
            info = token.get("userinfo") or {}
            if not info:
                resp = await client.get("https://graph.microsoft.com/oidc/userinfo", token=token)
                info = resp.json() or {}
            return {
                "sub": info.get("sub") or info.get("oid"),
                "email": info.get("email") or info.get("preferred_username"),
                "name": info.get("name"),
            }
    except Exception as e:  # pragma: no cover
        log.warning("oauth profile fetch failed for %s: %s", provider, e)
    return None


async def _provision_oauth_identity(
    db,  # type: ignore[no-untyped-def]
    *,
    provider: str,
    sub: str,
    email: str,
    name: str,
    avatar_url: str | None,
):
    """Find-or-create the Identity for this SSO session.

    Match rules:
      1. Exact ``(oauth_provider, oauth_id)`` — the account is already linked.
      2. Email match + not yet SSO-linked → attach provider + id (trusts IdP
         email verification for Google / Microsoft; GitHub only returns
         verified primary emails via our lookup).
      3. Otherwise create a fresh Identity with ACTIVE status and no password
         (password login disabled until the user sets one on the profile page).
    """
    from sqlalchemy import select

    from app.db.models.identity import Identity, IdentityStatus

    repo = IdentityRepository(db)

    if sub:
        linked = (
            await db.execute(
                select(Identity)
                .where(Identity.oauth_provider == provider)
                .where(Identity.oauth_id == sub)
            )
        ).scalar_one_or_none()
        if linked is not None:
            return linked

    if email:
        by_email = await repo.get_by_email(email)
        if by_email is not None:
            if not by_email.oauth_provider and sub:
                by_email = await repo.update(
                    by_email,
                    oauth_provider=provider,
                    oauth_id=sub,
                    avatar_url=by_email.avatar_url or avatar_url,
                )
            return by_email

    return await repo.create(
        email=email or f"{provider}-{sub}@sso.local",
        name=name,
        password_hash=None,
        status=IdentityStatus.ACTIVE,
        oauth_provider=provider,
        oauth_id=sub or None,
        avatar_url=avatar_url,
        profile_json={},
    )
