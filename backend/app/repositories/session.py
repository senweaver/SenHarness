"""Session + Message + SessionStar repositories."""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy import asc, desc, func, select

from app.db.models.message import Message
from app.db.models.session import Session as SessionModel
from app.db.models.session_star import SessionStar
from app.db.repository import AsyncRepository


class SessionRepository(AsyncRepository[SessionModel]):
    model = SessionModel

    async def list_for_identity(
        self,
        *,
        workspace_id: uuid.UUID,
        identity_id: uuid.UUID,
        limit: int = 50,
        offset: int = 0,
    ) -> Sequence[SessionModel]:
        # COALESCE keeps brand-new sessions (last_message_at IS NULL) at
        # the top by falling back to created_at — without this the
        # ``nulls_last`` clause buried freshly-created sessions until the
        # first reply landed.
        stmt = (
            select(SessionModel)
            .where(
                SessionModel.workspace_id == workspace_id,
                SessionModel.owner_identity_id == identity_id,
                SessionModel.deleted_at.is_(None),
            )
            .order_by(
                func.coalesce(
                    SessionModel.last_message_at, SessionModel.created_at
                ).desc()
            )
            .offset(offset)
            .limit(limit)
        )
        return (await self.session.execute(stmt)).scalars().all()

    async def find_channel_session(
        self,
        *,
        workspace_id: uuid.UUID,
        channel_id: uuid.UUID,
        thread_key: str,
    ) -> SessionModel | None:
        """Look up an existing channel session by its thread_key metadata."""
        stmt = (
            select(SessionModel)
            .where(
                SessionModel.workspace_id == workspace_id,
                SessionModel.channel_id == channel_id,
                SessionModel.deleted_at.is_(None),
                SessionModel.metadata_json["thread_key"].astext == thread_key,
            )
            .order_by(desc(SessionModel.created_at))
            .limit(1)
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()


class MessageRepository(AsyncRepository[Message]):
    model = Message

    async def list_for_session(
        self, *, session_id: uuid.UUID, limit: int = 200, offset: int = 0
    ) -> Sequence[Message]:
        stmt = (
            select(Message)
            .where(Message.session_id == session_id)
            .order_by(asc(Message.created_at))
            .offset(offset)
            .limit(limit)
        )
        return (await self.session.execute(stmt)).scalars().all()

    async def list_recent(
        self, *, session_id: uuid.UUID, limit: int = 40
    ) -> list[Message]:
        """Newest-N messages in chronological order (for history rehydrate)."""
        stmt = (
            select(Message)
            .where(Message.session_id == session_id)
            .order_by(desc(Message.created_at))
            .limit(limit)
        )
        rows = list((await self.session.execute(stmt)).scalars().all())
        rows.reverse()
        return rows


class SessionStarRepository(AsyncRepository[SessionStar]):
    model = SessionStar

    async def get_for(
        self, identity_id: uuid.UUID, session_id: uuid.UUID
    ) -> SessionStar | None:
        return await self.get_by(identity_id=identity_id, session_id=session_id)
