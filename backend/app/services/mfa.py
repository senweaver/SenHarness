"""TOTP-based multi-factor authentication.

Design:
    * Secret lives in ``identities.mfa_secret_ref`` as a base32 string. When
      populated, the login flow requires a 6-digit code in addition to the
      password.
    * ``setup()`` generates a fresh secret + returns the provisioning URI so
      the frontend can render a QR code. The secret is stored as ``pending:``
      prefix until ``activate()`` sees a valid code from the user.
    * ``verify()`` checks a 6-digit code against the stored secret with a
      ±1-step window (30s each side) to tolerate clock skew.

No DB migration needed — the ``mfa_secret_ref`` column already exists.
"""

from __future__ import annotations

import logging
import secrets
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.identity import Identity
from app.repositories.identity import IdentityRepository

log = logging.getLogger(__name__)

_PENDING_PREFIX = "pending:"


@dataclass(slots=True)
class MfaSetup:
    secret: str  # base32
    otpauth_uri: str


def _issuer() -> str:
    return "SenHarness"


def _provisioning_uri(secret: str, label: str) -> str:
    try:
        import pyotp
    except ImportError:  # pragma: no cover
        raise RuntimeError("pyotp not installed") from None
    return pyotp.TOTP(secret).provisioning_uri(name=label, issuer_name=_issuer())


async def setup(session: AsyncSession, *, identity: Identity) -> MfaSetup:
    """Begin the enrollment flow. Generates a fresh secret and stores it as
    ``pending:`` so the user has to prove possession before it's honored at
    login. Any previous pending secret is overwritten."""
    try:
        import pyotp
    except ImportError as e:  # pragma: no cover
        raise RuntimeError("pyotp not installed") from e

    secret = pyotp.random_base32()
    await IdentityRepository(session).update(
        identity, mfa_secret_ref=f"{_PENDING_PREFIX}{secret}"
    )
    uri = _provisioning_uri(secret, identity.email)
    return MfaSetup(secret=secret, otpauth_uri=uri)


async def activate(
    session: AsyncSession, *, identity: Identity, code: str
) -> bool:
    """Confirm the pending secret by verifying one TOTP code. On success the
    ``pending:`` prefix is dropped and MFA is live for the next login.
    """
    raw = identity.mfa_secret_ref or ""
    if not raw.startswith(_PENDING_PREFIX):
        return False
    secret = raw[len(_PENDING_PREFIX) :]
    if not _verify_code(secret, code):
        return False
    await IdentityRepository(session).update(identity, mfa_secret_ref=secret)
    return True


async def disable(session: AsyncSession, *, identity: Identity) -> None:
    """Remove MFA — the user proved they know the password to get here."""
    await IdentityRepository(session).update(identity, mfa_secret_ref=None)


def is_enabled(identity: Identity) -> bool:
    """True when MFA is live (not pending)."""
    raw = identity.mfa_secret_ref or ""
    return bool(raw) and not raw.startswith(_PENDING_PREFIX)


def verify_login_code(identity: Identity, code: str) -> bool:
    raw = identity.mfa_secret_ref or ""
    if not raw or raw.startswith(_PENDING_PREFIX):
        # If not enabled OR still pending, don't accept login codes; the UI
        # should catch the pending state earlier but this is a defence.
        return False
    return _verify_code(raw, code)


# ─── Internals ───────────────────────────────────────────
def _verify_code(secret: str, code: str) -> bool:
    """TOTP verify with ±1 step tolerance (≈ 30s either side)."""
    try:
        import pyotp
    except ImportError:  # pragma: no cover
        return False
    code = (code or "").strip().replace(" ", "")
    if not code.isdigit() or len(code) != 6:
        return False
    totp = pyotp.TOTP(secret)
    return totp.verify(code, valid_window=1)


def generate_recovery_codes(n: int = 8) -> list[str]:
    """Short backup codes the user can stash offline."""
    return [secrets.token_hex(4) for _ in range(n)]
