"""Two-factor authentication policy.

``totp_required = True`` is enforced at the next login, not
retroactively — already-authenticated identities can finish their
session without re-enrolling. ``backup_codes_count`` controls the
number of one-shot recovery codes generated when a user enrols TOTP;
existing codes are NOT re-issued when the value changes.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class AuthMfaSettings(BaseModel):
    totp_required: bool = False
    backup_codes_count: int = Field(default=8, ge=4, le=20)
