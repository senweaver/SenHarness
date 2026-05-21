"""Service layer for session sharing.

Owns:
  * Token generation (URL-safe, 64 chars).
  * Recipient resolution (UUID or email).
  * Permission checks (only the session owner can share / list / revoke).
  * Public-token lookup with expiry handling.
"""

from __future__ import annotations

import secrets
import uuid
from collections.abc import Iterable, Sequence
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import Conflict, NotFound, PermissionDenied, ValidationFailed
from app.db.models.identity import Identity
from app.db.models.session import Session as SessionModel
from app.db.models.session_share import SessionShare, SharePermission, ShareVisibility
from app.repositories.identity import IdentityRepository
from app.repositories.session import MessageRepository, SessionRepository
from app.repositories.session_share import SessionShareRepository

TOKEN_BYTES = 32  # → 43 url-safe chars (well within 64-char column)


def generate_token() -> str:
    return secrets.token_urlsafe(TOKEN_BYTES)


async def share_session(
    db: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    session_id: uuid.UUID,
    actor_identity_id: uuid.UUID,
    shared_with: str | None,
    generate_link: bool,
    permission: str = "view",
    visibility: ShareVisibility = ShareVisibility.WORKSPACE,
    expires_at: datetime | None = None,
) -> SessionShare:
    """Create a share — direct or public link.

    The DTO layer already enforces "at least one of (shared_with, generate_link)",
    so we don't re-check that here.
    """
    if shared_with is None and not generate_link:
        raise ValidationFailed(
            "Provide ``shared_with`` or ``generate_link``",
            code="share.missing_target",
        )

    sess = await SessionRepository(db).get(session_id)
    if sess is None or sess.workspace_id != workspace_id:
        raise NotFound("session_not_found", code="session.not_found")
    if sess.owner_identity_id != actor_identity_id:
        raise PermissionDenied(
            "Only the session owner can share it",
            code="share.not_owner",
        )

    perm_enum = _coerce_permission(permission)

    repo = SessionShareRepository(db)
    target_identity_id: uuid.UUID | None = None
    if shared_with:
        target = await _resolve_identity(db, shared_with)
        target_identity_id = target.id
        existing = await repo.find_direct(
            session_id=session_id, identity_id=target_identity_id
        )
        if existing is not None:
            # Re-share = update permission/expiry, keep the row id stable.
            return await repo.update(
                existing,
                permission=perm_enum,
                expires_at=expires_at,
                visibility=visibility,
                shared_by_identity_id=actor_identity_id,
            )

    token = generate_token() if generate_link else None
    return await repo.create(
        session_id=session_id,
        token=token,
        permission=perm_enum,
        visibility=visibility,
        expires_at=expires_at,
        created_by=actor_identity_id,
        shared_by_identity_id=actor_identity_id,
        shared_with_identity_id=target_identity_id,
    )


async def list_shares(
    db: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    session_id: uuid.UUID,
    actor_identity_id: uuid.UUID,
) -> tuple[Sequence[SessionShare], dict[uuid.UUID, str]]:
    """Owner-only. Returns shares + a ``{identity_id: email}`` lookup table."""
    sess = await SessionRepository(db).get(session_id)
    if sess is None or sess.workspace_id != workspace_id:
        raise NotFound("session_not_found", code="session.not_found")
    if sess.owner_identity_id != actor_identity_id:
        raise PermissionDenied(
            "Only the session owner can list shares",
            code="share.not_owner",
        )
    rows = await SessionShareRepository(db).list_for_session(session_id=session_id)
    identity_ids: set[uuid.UUID] = set()
    for row in rows:
        if row.shared_by_identity_id is not None:
            identity_ids.add(row.shared_by_identity_id)
        if row.shared_with_identity_id is not None:
            identity_ids.add(row.shared_with_identity_id)
    emails = await _email_lookup(db, identity_ids)
    return rows, emails


async def revoke_share(
    db: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    session_id: uuid.UUID,
    share_id: uuid.UUID,
    actor_identity_id: uuid.UUID,
) -> None:
    """Owner-only. Hard-deletes the share row."""
    sess = await SessionRepository(db).get(session_id)
    if sess is None or sess.workspace_id != workspace_id:
        raise NotFound("session_not_found", code="session.not_found")
    if sess.owner_identity_id != actor_identity_id:
        raise PermissionDenied(
            "Only the session owner can revoke shares",
            code="share.not_owner",
        )
    repo = SessionShareRepository(db)
    row = await repo.get(share_id)
    if row is None or row.session_id != session_id:
        raise NotFound("share_not_found", code="share.not_found")
    await repo.hard_delete(row)


async def list_shared_with_me(
    db: AsyncSession,
    *,
    identity_id: uuid.UUID,
    offset: int = 0,
    limit: int = 50,
) -> tuple[list[SessionModel], int]:
    return await SessionShareRepository(db).list_shared_with(
        identity_id=identity_id, offset=offset, limit=limit
    )


async def get_by_token(
    db: AsyncSession, *, token: str
) -> tuple[SessionShare, SessionModel, list]:
    """Resolve a public token → (share, session, message rows). 404 / Gone otherwise."""
    if not token:
        raise NotFound("share_not_found", code="share.not_found")
    repo = SessionShareRepository(db)
    share = await repo.get_by_token(token=token)
    if share is None:
        raise NotFound("share_not_found", code="share.not_found")
    if share.expires_at is not None and share.expires_at <= _utcnow_naive():
        raise NotFound("share_expired", code="share.expired")
    sess = await SessionRepository(db).get(share.session_id)
    if sess is None or sess.deleted_at is not None:
        raise NotFound("session_not_found", code="session.not_found")
    msgs = await MessageRepository(db).list_for_session(
        session_id=sess.id, limit=500
    )
    return share, sess, list(msgs)


def _coerce_permission(perm: str) -> SharePermission:
    try:
        return SharePermission(perm)
    except ValueError as e:
        raise ValidationFailed(
            f"unknown permission '{perm}'", code="share.bad_permission"
        ) from e


async def _resolve_identity(db: AsyncSession, selector: str) -> Identity:
    """Resolve a UUID-or-email selector to an Identity row."""
    selector = selector.strip()
    repo = IdentityRepository(db)
    # Try UUID first.
    try:
        ident = await repo.get(uuid.UUID(selector))
    except (ValueError, TypeError):
        ident = None
    if ident is None and "@" in selector:
        ident = await repo.get_by_email(selector.lower())
    if ident is None:
        raise NotFound(
            f"identity not found for {selector!r}",
            code="share.identity_not_found",
        )
    return ident


async def _email_lookup(
    db: AsyncSession, identity_ids: Iterable[uuid.UUID]
) -> dict[uuid.UUID, str]:
    ids = list(identity_ids)
    if not ids:
        return {}
    rows = await IdentityRepository(db).list(id=ids, limit=len(ids))
    return {row.id: row.email for row in rows}


def _utcnow_naive() -> datetime:
    """Naive UTC `datetime` matching the column timezone (False)."""
    from app.core.security import utcnow_naive

    return utcnow_naive()


_ = Conflict  # keep import in case future code uses it
