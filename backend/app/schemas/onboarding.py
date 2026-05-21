"""Onboarding DTOs."""

from __future__ import annotations

from datetime import datetime

from app.schemas._base import ORMModel


class OnboardingCompleteOut(ORMModel):
    onboarded_at: datetime
