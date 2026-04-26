"""Auth DTOs: register / login / refresh / reset / tokens."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, EmailStr, Field


class RegisterIn(BaseModel):
    email: EmailStr
    name: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=8, max_length=128)


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
