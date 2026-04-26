"""Identity repository."""

from __future__ import annotations

from app.db.models.identity import Identity
from app.db.repository import AsyncRepository


class IdentityRepository(AsyncRepository[Identity]):
    model = Identity

    async def get_by_email(self, email: str) -> Identity | None:
        return await self.get_by(email=email)
