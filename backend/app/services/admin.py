"""Platform-level admin operations (bootstrap)."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import Conflict
from app.core.security import hash_password
from app.db.models.identity import Identity, IdentityStatus, PlatformRole
from app.repositories.identity import IdentityRepository


async def create_platform_admin(
    session: AsyncSession, *, email: str, name: str, password: str
) -> Identity:
    repo = IdentityRepository(session)
    if await repo.get_by_email(email.lower()):
        raise Conflict("email_taken", code="admin.email_taken")
    return await repo.create(
        email=email.lower(),
        name=name,
        password_hash=hash_password(password),
        status=IdentityStatus.ACTIVE,
        platform_role=PlatformRole.PLATFORM_ADMIN,
    )
