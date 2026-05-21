"""Repository for the M3.7 per-identity user profile facts."""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFound
from app.db.models.user_profile import UserProfileDimension, UserProfileFact
from app.db.repository import AsyncRepository


class UserProfileFactRepository(AsyncRepository[UserProfileFact]):
    model = UserProfileFact

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, UserProfileFact)

    async def list_for_identity(
        self,
        *,
        workspace_id: uuid.UUID,
        identity_id: uuid.UUID,
        dimension: UserProfileDimension | None = None,
        limit: int = 200,
    ) -> Sequence[UserProfileFact]:
        """Walk every non-deleted fact for the identity, newest first.

        ``dimension`` narrows the scan to a single bucket which the
        ``/me/profile`` UI uses when the user expands one card.
        """
        stmt = select(UserProfileFact).where(
            UserProfileFact.workspace_id == workspace_id,
            UserProfileFact.identity_id == identity_id,
            UserProfileFact.deleted_at.is_(None),
        )
        if dimension is not None:
            stmt = stmt.where(UserProfileFact.dimension == dimension)
        stmt = stmt.order_by(desc(UserProfileFact.created_at)).limit(int(limit))
        return (await self.session.execute(stmt)).scalars().all()

    async def get_active_for_dimension(
        self,
        *,
        workspace_id: uuid.UUID,
        identity_id: uuid.UUID,
        dimension: UserProfileDimension,
    ) -> UserProfileFact | None:
        """Pick the single highest-confidence non-superseded row.

        Confirmed rows beat unconfirmed ones for the same dimension —
        the renderer relies on this so the user-vouched fact wins
        whenever both a confirmed older row and a noisier fresh
        candidate exist.
        """
        stmt = (
            select(UserProfileFact)
            .where(
                UserProfileFact.workspace_id == workspace_id,
                UserProfileFact.identity_id == identity_id,
                UserProfileFact.dimension == dimension,
                UserProfileFact.deleted_at.is_(None),
                UserProfileFact.superseded_by_id.is_(None),
                UserProfileFact.user_rejected.is_(False),
            )
            .order_by(
                desc(UserProfileFact.user_confirmed),
                desc(UserProfileFact.confidence),
                desc(UserProfileFact.created_at),
            )
            .limit(1)
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def supersede(
        self, *, fact_id: uuid.UUID, by_fact_id: uuid.UUID
    ) -> None:
        """Mark ``fact_id`` as superseded by ``by_fact_id``.

        A no-op if the source row is missing — the daily sweep races
        the GDPR cascade and a missing parent simply means the row got
        purged in between extract passes.
        """
        existing = await self.get(fact_id)
        if existing is None:
            return
        existing.superseded_by_id = by_fact_id
        await self.session.flush([existing])

    async def confirm(
        self, *, fact_id: uuid.UUID, identity_id: uuid.UUID
    ) -> UserProfileFact:
        """Flip ``user_confirmed`` to True and ensure ``user_rejected`` is False.

        Raises :class:`NotFound` if the row doesn't exist or belongs to
        another identity (defensive — the API layer also checks).
        """
        fact = await self.get(fact_id)
        if fact is None or fact.identity_id != identity_id:
            raise NotFound(
                "user_profile_fact_not_found",
                code="user_profile.fact_not_found",
            )
        fact.user_confirmed = True
        fact.user_rejected = False
        await self.session.flush([fact])
        return fact

    async def reject(
        self, *, fact_id: uuid.UUID, identity_id: uuid.UUID
    ) -> UserProfileFact:
        """Flip ``user_rejected`` to True. Confirmed → False on rejection."""
        fact = await self.get(fact_id)
        if fact is None or fact.identity_id != identity_id:
            raise NotFound(
                "user_profile_fact_not_found",
                code="user_profile.fact_not_found",
            )
        fact.user_rejected = True
        fact.user_confirmed = False
        await self.session.flush([fact])
        return fact

    async def list_active_per_dimension(
        self,
        *,
        workspace_id: uuid.UUID,
        identity_id: uuid.UUID,
    ) -> dict[UserProfileDimension, UserProfileFact | None]:
        """Return a fully-populated mapping with one row per dimension.

        Dimensions that have no eligible fact map to ``None`` — the
        renderer skips those buckets so the system-prompt fragment
        never carries an empty bullet.
        """
        out: dict[UserProfileDimension, UserProfileFact | None] = {}
        for dim in UserProfileDimension:
            out[dim] = await self.get_active_for_dimension(
                workspace_id=workspace_id,
                identity_id=identity_id,
                dimension=dim,
            )
        return out


__all__ = ["UserProfileFactRepository"]
