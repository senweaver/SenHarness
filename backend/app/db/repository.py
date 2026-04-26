"""Generic async repository base.

Goals:
  - Strong typing (`Generic[TModel]`).
  - Cover 80%+ of simple CRUD without leaking SQLAlchemy into services.
  - No coupling to Pydantic DTOs — services do DTO<->model mapping.
  - Workspace-scoped filtering baked in (optional).
  - Soft-delete aware.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import Any, Generic, TypeVar

from sqlalchemy import and_, delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import Base

TModel = TypeVar("TModel", bound=Base)


class AsyncRepository(Generic[TModel]):
    """Tiny, strongly-typed SQLAlchemy 2 async repository."""

    model: type[TModel]

    def __init__(self, session: AsyncSession, model: type[TModel] | None = None):
        self.session = session
        if model is not None:
            self.model = model
        if not hasattr(self, "model"):
            raise TypeError(
                f"{type(self).__name__} must set `model` class attribute or pass `model=...`"
            )

    # ─── Read ─────────────────────────────────────────────
    async def get(self, id_: uuid.UUID, *, include_deleted: bool = False) -> TModel | None:
        stmt = select(self.model).where(self.model.id == id_)  # type: ignore[attr-defined]
        stmt = self._scope_soft_delete(stmt, include_deleted)
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def get_by(self, *, include_deleted: bool = False, **filters: Any) -> TModel | None:
        stmt = select(self.model).where(and_(*self._filters(filters)))
        stmt = self._scope_soft_delete(stmt, include_deleted)
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def exists(self, **filters: Any) -> bool:
        stmt = select(func.count()).select_from(self.model).where(and_(*self._filters(filters)))
        stmt = self._scope_soft_delete(stmt, False)
        return bool((await self.session.execute(stmt)).scalar())

    async def list(
        self,
        *,
        offset: int = 0,
        limit: int = 50,
        order_by: Any | None = None,
        include_deleted: bool = False,
        **filters: Any,
    ) -> Sequence[TModel]:
        stmt = select(self.model).where(and_(*self._filters(filters)))
        stmt = self._scope_soft_delete(stmt, include_deleted)
        if order_by is not None:
            stmt = stmt.order_by(order_by)
        stmt = stmt.offset(offset).limit(limit)
        return (await self.session.execute(stmt)).scalars().all()

    async def count(self, *, include_deleted: bool = False, **filters: Any) -> int:
        stmt = select(func.count()).select_from(self.model).where(and_(*self._filters(filters)))
        stmt = self._scope_soft_delete(stmt, include_deleted)
        return int((await self.session.execute(stmt)).scalar() or 0)

    # ─── Write ────────────────────────────────────────────
    async def create(self, **data: Any) -> TModel:
        obj = self.model(**data)  # type: ignore[call-arg]
        self.session.add(obj)
        await self.session.flush([obj])
        return obj

    async def update(self, obj: TModel, /, **data: Any) -> TModel:
        for k, v in data.items():
            setattr(obj, k, v)
        await self.session.flush([obj])
        # ``onupdate=func.now()`` on TimestampMixin expires ``updated_at`` after
        # flush (server-side value pending). If we don't proactively reload,
        # downstream ``model_validate(obj)`` triggers a lazy load and hits
        # ``MissingGreenlet`` once we're outside the async-with block.
        await self.session.refresh(obj)
        return obj

    async def update_where(self, *, values: dict[str, Any], **filters: Any) -> int:
        stmt = (
            update(self.model)
            .where(and_(*self._filters(filters)))
            .values(**values)
            .execution_options(synchronize_session="fetch")
        )
        result = await self.session.execute(stmt)
        return result.rowcount or 0

    async def soft_delete(self, obj: TModel) -> None:
        if hasattr(obj, "deleted_at"):
            from app.core.security import utcnow_naive

            obj.deleted_at = utcnow_naive()  # type: ignore[assignment]
            await self.session.flush([obj])
        else:
            await self.hard_delete(obj)

    async def hard_delete(self, obj: TModel) -> None:
        await self.session.delete(obj)
        await self.session.flush()

    async def hard_delete_where(self, **filters: Any) -> int:
        stmt = delete(self.model).where(and_(*self._filters(filters)))
        result = await self.session.execute(stmt)
        return result.rowcount or 0

    # ─── Helpers ─────────────────────────────────────────
    def _filters(self, filters: dict[str, Any]) -> list[Any]:
        clauses: list[Any] = []
        for key, value in filters.items():
            col = getattr(self.model, key, None)
            if col is None:
                raise AttributeError(f"{self.model.__name__} has no attribute '{key}'")
            if isinstance(value, (list, tuple, set)):
                clauses.append(col.in_(value))
            else:
                clauses.append(col == value)
        return clauses

    def _scope_soft_delete(self, stmt: Any, include_deleted: bool) -> Any:
        if include_deleted:
            return stmt
        if hasattr(self.model, "deleted_at"):
            return stmt.where(self.model.deleted_at.is_(None))  # type: ignore[attr-defined]
        return stmt
