"""DTOs for session sharing — direct user share + public-link share."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import Field, model_validator

from app.db.models.session_share import SharePermission, ShareVisibility
from app.schemas._base import ORMModel, Timestamped


class SessionShareCreate(ORMModel):
    """Create a share — direct (``shared_with``) and/or public link.

    Exactly one of the two must be true: provide ``shared_with`` to invite a
    specific identity (by email or UUID — service resolves), or set
    ``generate_link=True`` to mint a one-shot public token.
    """

    shared_with: str | None = Field(
        default=None,
        description="Identity UUID or email to share with. Omit for link-only shares.",
    )
    generate_link: bool = Field(
        default=False,
        description="When true, server generates a 64-char URL-safe token.",
    )
    permission: Literal["view", "edit"] = Field(
        default="view", description="Access level granted to the recipient."
    )
    visibility: ShareVisibility = Field(
        default=ShareVisibility.WORKSPACE,
        description=(
            "Backwards-compat with the P0 share model. New code should rely on "
            "presence of ``token`` (public link) vs ``shared_with_identity_id`` "
            "(direct invite); operators reading historical rows still need it."
        ),
    )
    expires_at: datetime | None = Field(
        default=None,
        description="Optional hard expiry; after this the token becomes invalid.",
    )

    @model_validator(mode="after")
    def _at_least_one(self):
        if self.shared_with is None and not self.generate_link:
            raise ValueError(
                "Provide ``shared_with`` (direct invite) or set ``generate_link=true``."
            )
        return self


class SessionShareRead(Timestamped):
    """Owner-side view of a single share row."""

    session_id: uuid.UUID
    token: str | None = None
    permission: SharePermission = SharePermission.VIEW
    visibility: ShareVisibility
    shared_by_identity_id: uuid.UUID | None = None
    shared_with_identity_id: uuid.UUID | None = None
    shared_with_email: str | None = Field(
        default=None,
        description="Resolved email of ``shared_with_identity_id`` for UI display.",
    )
    shared_by_email: str | None = Field(
        default=None,
        description="Resolved email of ``shared_by_identity_id`` for UI display.",
    )
    expires_at: datetime | None = None


class SessionShareList(ORMModel):
    items: list[SessionShareRead]
    total: int


class PublicSessionMessage(ORMModel):
    """Trimmed message DTO for ``GET /sessions/shared/{token}`` — no PII / IDs."""

    role: str
    content_json: dict
    tool_call_json: dict | None = None
    attachments_json: list = Field(default_factory=list)
    created_at: datetime


class PublicSharedSession(ORMModel):
    """Read-only public projection of a shared conversation."""

    session_id: uuid.UUID
    title: str | None = None
    permission: SharePermission
    expires_at: datetime | None = None
    messages: list[PublicSessionMessage]
