"""JWT access + refresh + bcrypt password helpers.

Design:
  - Access token: short TTL (default 30m), `Authorization: Bearer <jwt>`.
  - Refresh token: long TTL (default 30d), stored in HttpOnly cookie.
  - Token blacklist supported via `jti` + `app.db.models.token_blacklist`.
"""

from __future__ import annotations

import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

import bcrypt
import jwt

from app.core.config import settings
from app.core.errors import Unauthorized

TokenKind = Literal["access", "refresh"]


# ─── Password ─────────────────────────────────────────────
def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode(), hashed.encode())
    except ValueError:
        return False


# ─── Tokens ───────────────────────────────────────────────
def _now() -> datetime:
    return datetime.now(UTC)


def utcnow_naive() -> datetime:
    """Naive UTC `datetime` for DB columns defined as `TIMESTAMP WITHOUT TIME ZONE`.

    Project convention: ORM columns are naive UTC; API layer can still return
    ISO-8601 with a `Z` suffix when serializing.
    """
    return datetime.now(UTC).replace(tzinfo=None)


def _encode(payload: dict[str, Any]) -> str:
    return jwt.encode(payload, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def create_access_token(
    *,
    identity_id: str,
    workspace_id: str | None = None,
    roles: list[str] | None = None,
    extra: dict[str, Any] | None = None,
) -> tuple[str, datetime, str]:
    """Return `(token, expires_at, jti)` — `expires_at` is naive UTC for DB."""
    jti = uuid.uuid4().hex
    exp_naive = utcnow_naive() + timedelta(seconds=settings.JWT_ACCESS_TTL_SECONDS)
    payload: dict[str, Any] = {
        "sub": identity_id,
        "kind": "access",
        "jti": jti,
        "iat": int(_now().timestamp()),
        "exp": int(exp_naive.replace(tzinfo=UTC).timestamp()),
    }
    if workspace_id:
        payload["ws"] = workspace_id
    if roles:
        payload["roles"] = roles
    if extra:
        payload.update(extra)
    return _encode(payload), exp_naive, jti


def create_refresh_token(*, identity_id: str) -> tuple[str, datetime, str]:
    jti = uuid.uuid4().hex
    exp_naive = utcnow_naive() + timedelta(seconds=settings.JWT_REFRESH_TTL_SECONDS)
    payload = {
        "sub": identity_id,
        "kind": "refresh",
        "jti": jti,
        "iat": int(_now().timestamp()),
        "exp": int(exp_naive.replace(tzinfo=UTC).timestamp()),
    }
    return _encode(payload), exp_naive, jti


def decode_token(token: str, *, expected_kind: TokenKind | None = None) -> dict[str, Any]:
    try:
        payload = jwt.decode(token, settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM])
    except jwt.ExpiredSignatureError as e:
        raise Unauthorized("token_expired", code="auth.token_expired") from e
    except jwt.InvalidTokenError as e:
        raise Unauthorized("token_invalid", code="auth.token_invalid") from e
    if expected_kind and payload.get("kind") != expected_kind:
        raise Unauthorized("token_kind_mismatch", code="auth.token_kind_mismatch")
    return payload


# ─── Random tokens (share links, invitations, webhook tokens) ──
def random_token(length: int = 32) -> str:
    return secrets.token_urlsafe(length)
