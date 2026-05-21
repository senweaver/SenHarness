"""Onboarding service — flips the audit timestamp on ``identities.onboarded_at``.

Idempotent by design: if the caller already has a non-NULL
``onboarded_at`` the existing value is returned unchanged. The
``identities.onboarded_at`` column is *only* an audit field — the
new-user overlay is gated client-side by ``?onboarding=1`` or the
AvatarMenu "restart onboarding" trigger.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFound
from app.core.security import utcnow_naive
from app.repositories.identity import IdentityRepository

log = logging.getLogger(__name__)


async def mark_onboarded(
    db: AsyncSession, *, identity_id: uuid.UUID
) -> datetime:
    repo = IdentityRepository(db)
    identity = await repo.get(identity_id)
    if identity is None:
        raise NotFound("identity_not_found", code="identity.not_found")
    if identity.onboarded_at is not None:
        return identity.onboarded_at
    stamp = utcnow_naive()
    await repo.update(identity, onboarded_at=stamp)
    return stamp
