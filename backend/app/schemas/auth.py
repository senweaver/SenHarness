"""Auth DTOs: register / login / refresh / reset / tokens."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, EmailStr, Field

from app.db.models.identity import IdentityStatus


class RegisterIn(BaseModel):
    email: EmailStr
    name: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=8, max_length=128)
    invitation_code: str | None = Field(default=None, max_length=64)


class LoginIn(BaseModel):
    email: EmailStr
    password: str
    # Optional 6-digit TOTP code. Required when the identity has MFA enabled;
    # the API returns 401 with ``detail.code == "auth.mfa_required"`` if it's
    # missing so the UI can prompt for it.
    totp_code: str | None = Field(default=None, max_length=8)


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_at: datetime


class RefreshOut(TokenOut):
    pass


class ForgotPasswordIn(BaseModel):
    email: EmailStr


class ResetPasswordIn(BaseModel):
    token: str
    new_password: str = Field(min_length=8, max_length=128)


class VerifyEmailIn(BaseModel):
    token: str


class WorkspaceSummary(BaseModel):
    id: uuid.UUID
    name: str
    slug: str


class TokenPairOut(BaseModel):
    access_token: str
    refresh_token: str
    expires_at: datetime
    refresh_expires_at: datetime
    token_type: str = "bearer"


class RegistrationResponse(BaseModel):
    identity_id: uuid.UUID
    email: EmailStr
    name: str
    status: IdentityStatus
    workspace: WorkspaceSummary | None = None
    workspace_slug_warning: bool = False
    auto_login_tokens: TokenPairOut | None = None
    requires_email_verification: bool = False
    registration_mode: str


class RegistrationModeOut(BaseModel):
    mode: str
    invitation_required: bool
    requires_email_verification: bool


class ResendVerificationIn(BaseModel):
    email: EmailStr
