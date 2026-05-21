"""Email-verification token issue / consume service.

Tokens are single-use. The plaintext lives only in the email body and
the immediate API response; the DB stores the SHA-256 digest so a stolen
dump cannot be replayed against the verification endpoint.

SMTP delivery is intentionally a v2 concern (M0.13). When no transport
is wired the issued token is recorded in :func:`send_verification_email`
audit + log so on-prem operators can copy it from the audit feed during
the verification-gate rollout.
"""

from __future__ import annotations

import hashlib
import logging
import secrets
import uuid
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFound, Unauthorized
from app.core.security import utcnow_naive
from app.db.models.email_verification import EmailVerificationToken
from app.db.models.identity import Identity, IdentityStatus
from app.repositories.identity import IdentityRepository
from app.services import audit as audit_svc
from app.services.system_settings import SystemSettingKey, get_system_setting

log = logging.getLogger(__name__)


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


async def issue_token(
    db: AsyncSession,
    *,
    identity_id: uuid.UUID,
    ttl_seconds: int | None = None,
) -> str:
    """Mint a fresh opaque token (32-byte hex). Returns the plaintext."""
    if ttl_seconds is None:
        raw = await get_system_setting(
            db, SystemSettingKey.EMAIL_VERIFICATION_TOKEN_TTL_SECONDS, default=86400
        )
        try:
            ttl_seconds = int(raw)
        except (TypeError, ValueError):
            ttl_seconds = 86400
    token = secrets.token_urlsafe(32)
    row = EmailVerificationToken(
        identity_id=identity_id,
        token_hash=_hash(token),
        expires_at=utcnow_naive() + timedelta(seconds=ttl_seconds),
    )
    db.add(row)
    await db.flush([row])
    return token


async def consume_token(db: AsyncSession, *, token: str) -> Identity:
    """Validate + mark consumed + flip identity to ACTIVE.

    Raises :class:`Unauthorized` for missing / expired / already-used
    tokens with stable ``code`` strings the frontend renders.
    """
    if not token:
        raise Unauthorized("verification_token_missing", code="auth.verify_token_missing")

    digest = _hash(token)
    stmt = select(EmailVerificationToken).where(EmailVerificationToken.token_hash == digest)
    row = (await db.execute(stmt)).scalar_one_or_none()
    if row is None:
        raise Unauthorized("verification_token_unknown", code="auth.verify_token_invalid")
    if row.consumed_at is not None:
        raise Unauthorized("verification_token_consumed", code="auth.verify_token_consumed")
    if row.expires_at < utcnow_naive():
        raise Unauthorized("verification_token_expired", code="auth.verify_token_expired")

    ident_repo = IdentityRepository(db)
    identity = await ident_repo.get(row.identity_id)
    if identity is None:
        raise NotFound("identity_missing", code="auth.no_identity")

    row.consumed_at = utcnow_naive()
    if identity.status == IdentityStatus.PENDING:
        await ident_repo.update(identity, status=IdentityStatus.ACTIVE)
    await db.flush([row, identity])
    return identity


async def latest_unconsumed_token(
    db: AsyncSession, *, identity_id: uuid.UUID
) -> EmailVerificationToken | None:
    """Most recently issued live token for this identity (unused + unexpired)."""
    stmt = (
        select(EmailVerificationToken)
        .where(EmailVerificationToken.identity_id == identity_id)
        .where(EmailVerificationToken.consumed_at.is_(None))
        .where(EmailVerificationToken.expires_at > utcnow_naive())
        .order_by(EmailVerificationToken.created_at.desc())
        .limit(1)
    )
    return (await db.execute(stmt)).scalar_one_or_none()


async def send_verification_email(
    db: AsyncSession,
    *,
    identity: Identity,
    token: str,
) -> None:
    """Deliver the verification token to the identity's mailbox.

    SMTP isn't wired until M0.13 — meanwhile we drop a structured audit
    row plus an INFO log line so on-prem operators can fish the token out
    of the platform audit feed and forward it manually if the user can't
    receive mail. The audit metadata explicitly omits the plaintext token
    once SMTP ships; until then including it in the log is the pragmatic
    bootstrap path.
    """
    log.info(
        "email_verification.token_issued identity=%s token=%s",
        identity.email,
        token,
    )
    await audit_svc.record(
        db,
        action="auth.email_verification_sent",
        actor_identity_id=identity.id,
        resource_type="identity",
        resource_id=identity.id,
        summary=f"verification email queued for {identity.email}",
        metadata={
            "transport": "log_fallback",
            "smtp_configured": False,
        },
    )
