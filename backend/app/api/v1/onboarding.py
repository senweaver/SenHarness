"""Onboarding routes."""

from __future__ import annotations

from fastapi import APIRouter

from app.api.deps import CurrentIdentityId, DBSession
from app.schemas.onboarding import OnboardingCompleteOut
from app.services import onboarding as svc

router = APIRouter()


@router.post("/complete", response_model=OnboardingCompleteOut)
async def complete_onboarding(
    db: DBSession,
    identity_id: CurrentIdentityId,
) -> OnboardingCompleteOut:
    """Stamp ``identities.onboarded_at`` if not already set.

    Idempotent: a caller who has already completed onboarding gets the
    existing timestamp back, no DB write.
    """
    onboarded_at = await svc.mark_onboarded(db, identity_id=identity_id)
    await db.commit()
    return OnboardingCompleteOut(onboarded_at=onboarded_at)
