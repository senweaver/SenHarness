"""DTOs for the Skill Hub catalog (M3.1)."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import Field

from app.db.models.hub_skill_pack import HubScope, HubSkillPackState
from app.schemas._base import ORMModel, Timestamped


class HubSkillPackRead(Timestamped):
    scope: HubScope
    tenant_id: uuid.UUID | None
    slug: str
    name: str
    description: str | None
    state: HubSkillPackState
    promoted_from_pack_id: uuid.UUID | None = None
    promoted_from_workspace_id: uuid.UUID | None = None
    promoted_by_identity_id: uuid.UUID | None = None
    tags: list[str] = Field(default_factory=list)


class HubSkillPackList(ORMModel):
    items: list[HubSkillPackRead]


class HubSkillPackVersionRead(ORMModel):
    id: uuid.UUID
    hub_pack_id: uuid.UUID
    version_no: int
    content_hash: str
    is_active: bool
    promoted_from_workspace_version_id: uuid.UUID | None = None
    created_at: datetime
    updated_at: datetime


class HubSkillPackVersionWithContent(HubSkillPackVersionRead):
    content_md: str
    files_json: dict


class HubSkillPackVersionList(ORMModel):
    hub_pack_id: uuid.UUID
    items: list[HubSkillPackVersionRead]


class HubSkillPackTransitionRequest(ORMModel):
    """Body for ``POST /admin/skills/hub/{id}/transition``."""

    target_state: HubSkillPackState
    reason: str = Field(min_length=1, max_length=512)


# ── M3.3 promote / subscribe / pull DTOs ─────────────────────
class HubPromoteRequest(ORMModel):
    """Body for ``POST /skills/packs/{pack_id}/promote-to-hub``.

    ``target_scope`` defaults to TENANT; PLATFORM scope requires the
    caller to hold the platform-admin role (the route raises 403
    otherwise via the M3.2 preview blocker).
    """

    target_scope: HubScope = HubScope.TENANT
    target_slug: str | None = Field(default=None, max_length=120)
    version_id: uuid.UUID | None = None
    reason: str | None = Field(default=None, max_length=512)


class HubPromoteSanitizationStats(ORMModel):
    redacted_emails: int = 0
    redacted_urls: int = 0
    redacted_paths: int = 0
    redacted_pii: int = 0
    redacted_extra: int = 0
    run_id_hashed_count: int = 0
    failure_reason: str | None = None


class HubPromoteResponse(ORMModel):
    """Response for the ``promote-to-hub`` verb."""

    approval_id: uuid.UUID
    pack_id: uuid.UUID
    target_scope: HubScope
    target_slug: str
    target_tenant_id: uuid.UUID | None
    sanitized_content_hash: str
    sanitization_stats: HubPromoteSanitizationStats
    will_dedup_against_version_id: uuid.UUID | None = None
    will_dedup_against_pack_id: uuid.UUID | None = None
    expires_at: datetime


class HubSubscribeRequest(ORMModel):
    auto_pull: bool = True


class HubSubscriptionRead(Timestamped):
    workspace_id: uuid.UUID
    hub_pack_id: uuid.UUID
    auto_pull: bool
    last_pulled_version_no: int | None = None
    last_pulled_at: datetime | None = None
    subscribed_by_identity_id: uuid.UUID | None = None


class HubSubscriptionStatus(ORMModel):
    """Body of ``GET /skills/hub/{hub_pack_id}/subscription-status``.

    ``subscription`` is ``None`` when the workspace is not yet
    subscribed; ``hub_active_version_no`` reflects the catalog's
    currently authoritative version (``None`` if the hub pack has
    never had an active version yet). ``has_update_available`` is
    True when the workspace is subscribed and the hub has shipped a
    later ``version_no`` than the workspace last pulled.
    """

    hub_pack_id: uuid.UUID
    subscribed: bool
    subscription: HubSubscriptionRead | None = None
    hub_active_version_no: int | None = None
    has_update_available: bool = False


class HubPullResponse(ORMModel):
    """Response for ``POST /skills/hub/{hub_pack_id}/pull``.

    ``status='pulled'`` — created a local CANDIDATE version (still
    flows through M2.4 verifier).
    ``status='up_to_date'`` — subscription cursor already matches
    the hub's active version; nothing was created.
    ``status='no_active_version'`` — the hub pack has no
    ``is_active=True`` row yet (catalog seed without published
    content).
    """

    status: str
    hub_pack_id: uuid.UUID
    hub_version_no: int | None = None
    local_pack_id: uuid.UUID | None = None
    local_version_id: uuid.UUID | None = None
    local_version_no: int | None = None
