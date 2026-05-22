"""Cross-platform logical thread + channel binding (M3.6).

A *logical thread* is the user-facing "conversation" abstraction that
spans multiple IM channels. The same identity might message an Agent
via WeChat in the morning, then continue the same thread via Slack at
night; both inbounds resolve to the same :class:`LogicalThread` and
therefore the same primary :class:`~app.db.models.session.Session`.

Two tables back the feature:

* ``logical_threads`` — one row per `(workspace, identity, agent)`
  conversation arc. Holds the canonical ``primary_session_id`` plus
  user-given ``label`` and ``last_activity_at`` for the thread list UI.
* ``thread_channel_bindings`` — one row per `(thread, channel,
  external_user_id)`. Carries ``is_paired`` so the dispatcher refuses
  cross-platform routing until both sides of the pair complete the
  6-digit handshake. The ``UniqueConstraint`` on
  ``(channel_id, external_user_id)`` keeps a single platform user from
  showing up under two threads.

Workspace-scoped + soft-deleted so the M0.11 retention sweep can
cascade by either ``workspace_id`` or ``identity_id`` without bespoke
SQL.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import ForeignKey, Index, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.mixins import (
    SoftDeleteMixin,
    TimestampMixin,
    UuidPkMixin,
    WorkspaceScopedMixin,
)


class LogicalThread(UuidPkMixin, TimestampMixin, SoftDeleteMixin, WorkspaceScopedMixin, Base):
    __tablename__ = "logical_threads"

    identity_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("identities.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    agent_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("agents.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    primary_session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("sessions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    label: Mapped[str | None] = mapped_column(String(200), nullable=True)
    last_activity_at: Mapped[datetime] = mapped_column(
        nullable=False,
        server_default=func.now(),
        index=True,
    )

    __table_args__ = (
        Index(
            "ix_logical_threads_identity_agent_active",
            "workspace_id",
            "identity_id",
            "agent_id",
        ),
    )


class ThreadChannelBinding(
    UuidPkMixin, TimestampMixin, SoftDeleteMixin, WorkspaceScopedMixin, Base
):
    __tablename__ = "thread_channel_bindings"

    thread_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("logical_threads.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # NULL means web/CLI binding (no IM channel row); the thread is
    # still routable from the in-app session list.
    channel_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("channels.id", ondelete="SET NULL"),
        nullable=True,
    )
    # Platform-side user identifier (Slack ``Uxxx``, WeChat openid,
    # Discord snowflake, ...). NULL paired with a NULL channel_id keeps
    # the row valid for web/CLI bindings keyed only by ``identity_id``
    # via the parent thread.
    external_user_id: Mapped[str | None] = mapped_column(String(200), nullable=True, index=True)
    last_seen_at: Mapped[datetime] = mapped_column(
        nullable=False,
        server_default=func.now(),
        index=True,
    )
    # Defaults to False so the first inbound on a new (channel,
    # external_user_id) pair lands in a fresh thread; the dispatcher
    # honours an unpaired binding only when ``cross_platform_enabled``
    # is True AND a pairing-code consume has flipped the flag for both
    # sides of the pair.
    is_paired: Mapped[bool] = mapped_column(default=False, server_default="false", nullable=False)

    __table_args__ = (
        UniqueConstraint(
            "channel_id",
            "external_user_id",
            name="uq_thread_channel_bindings_channel_user",
        ),
    )
