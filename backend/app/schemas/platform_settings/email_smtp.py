"""Outbound SMTP transport configuration.

When ``enabled = False`` the platform falls back to ``LogEmailTransport``
from M0.10 (audit-only, no real mail). ``password_ref`` is a vault
reference (see :class:`AuthOAuthSettings`'s ``client_secret_ref``);
the raw secret never lives in this row, so a settings export is safe
to commit.

The ``test_connection`` admin endpoint constructs an in-memory
``SmtpEmailTransport`` from a posted payload (without writing to the
DB) and dispatches a single test message — this lets the operator
verify credentials before flipping ``enabled`` on.
"""

from __future__ import annotations

from pydantic import BaseModel, EmailStr, Field


class EmailSmtpSettings(BaseModel):
    enabled: bool = False
    host: str | None = Field(default=None, max_length=255)
    port: int = Field(default=587, ge=1, le=65535)
    username: str | None = Field(default=None, max_length=255)
    password_ref: str | None = Field(default=None, max_length=255)
    from_address: EmailStr | None = None
    use_tls: bool = True
