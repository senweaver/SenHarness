"""DTOs for four-layer memory profiles."""

from __future__ import annotations

import uuid

from pydantic import Field

from app.db.models.memory_profile import MemoryProfileKind
from app.schemas._base import ORMModel, Timestamped


class MemoryProfileUpsert(ORMModel):
    """Replace the full markdown for a profile.

    The service layer truncates + records the final ``char_count``; no
    need for the caller to pre-trim.
    """

    content_md: str = Field(default="", description="Markdown body.")
    soul_dims_json: dict | None = Field(
        default=None,
        description=(
            "SOUL kind only: 12-dimension user-model fragments keyed by canonical dimension name."
        ),
    )
    metadata_json: dict | None = None


class MemoryProfileRead(Timestamped):
    workspace_id: uuid.UUID
    kind: MemoryProfileKind
    subject_id: uuid.UUID
    identity_id: uuid.UUID | None
    content_md: str
    char_count: int
    soul_dims_json: dict
    pending_updates_json: list
    metadata_json: dict
    updated_by: uuid.UUID | None


class SoulUpdateProposal(ORMModel):
    """Queue a SOUL rewrite for approval.

    Writes never go straight to ``content_md`` — they sit in
    ``pending_updates_json`` until the identity (or a workspace admin)
    calls the approve / reject endpoint.
    """

    proposed_content: str = Field(min_length=1)
    proposed_dims: dict = Field(default_factory=dict)
    source_session_id: uuid.UUID | None = None
    rationale: str = Field(default="", max_length=512)


class SoulUpdateRead(ORMModel):
    id: str
    proposed_content: str
    proposed_dims: dict
    proposed_at: str
    proposed_by_identity_id: uuid.UUID | None
    source_session_id: uuid.UUID | None
    rationale: str


class SoulDecisionIn(ORMModel):
    decision: str = Field(pattern="^(approve|reject)$")
    reason: str = Field(default="", max_length=512)
