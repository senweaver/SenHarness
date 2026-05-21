"""Repository for `SessionShare` rows."""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy import desc, or_, select
from sqlalchemy import func as sa_func

from app.db.models.session import Session as SessionModel
from app.db.models.session_share import SessionShare
from app.db.repository import AsyncRepository


class SessionShareRepository(AsyncRepository[SessionShare]):
    model = SessionShare

    async def list_for_session(self, *, session_id: uuid.UUID) -> Sequence[SessionShare]:
        stmt = (
            select(SessionShare)
            .where(SessionShare.session_id == session_id)
            .order_by(desc(SessionShare.created_at))
        )
        return (await self.session.execute(stmt)).scalars().all()

    async def get_by_token(self, *, token: str) -> SessionShare | None:
        if not token:
            return None
        stmt = select(SessionShare).where(SessionShare.token == token)
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def find_direct(
        self, *, session_id: uuid.UUID, identity_id: uuid.UUID
    ) -> SessionShare | None:
        """Return the direct (per-user) share of a session, if any."""
        stmt = select(SessionShare).where(
            SessionShare.session_id == session_id,
            SessionShare.shared_with_identity_id == identity_id,
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def list_shared_with(
        self,
        *,
        identity_id: uuid.UUID,
        offset: int = 0,
        limit: int = 50,
    ) -> tuple[list[SessionModel], int]:
        """Return sessions shared with the caller + total count.

        Manual JOIN instead of ``joinedload`` to avoid requiring an ORM
        relationship on ``SessionShare`` (kept light to make Alembic
        autogenerate happy).
        """
        not_expired = or_(
            SessionShare.expires_at.is_(None),
            SessionShare.expires_at > sa_func.now(),
        )
        stmt = (
            select(SessionModel)
            .join(SessionShare, SessionShare.session_id == SessionModel.id)
            .where(
                SessionShare.shared_with_identity_id == identity_id,
                SessionModel.deleted_at.is_(None),
                not_expired,
            )
            .order_by(desc(SessionShare.created_at))
            .offset(offset)
            .limit(limit)
        )
        rows = list((await self.session.execute(stmt)).scalars().all())

        count_q = (
            select(sa_func.count())
            .select_from(SessionShare)
            .join(SessionModel, SessionModel.id == SessionShare.session_id)
            .where(
                SessionShare.shared_with_identity_id == identity_id,
                SessionModel.deleted_at.is_(None),
                not_expired,
            )
        )
        total = int((await self.session.execute(count_q)).scalar() or 0)
        return rows, total
