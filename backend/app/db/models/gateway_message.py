"""GatewayMessage — bidirectional queue between SenHarness and remote workers.

``direction="request"``  SenHarness→Worker  RunRequest payload awaiting poll.
``direction="event"``    Worker→SenHarness  RunEvent emitted during a run.

The pair ``(run_id, direction, seq)`` is unique so replays (worker resends an
emit after a network blip) are absorbed idempotently.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.mixins import TimestampMixin, UuidPkMixin, WorkspaceScopedMixin


class GatewayMessageDirection(StrEnum):
    REQUEST = "request"
    EVENT = "event"


class GatewayMessageStatus(StrEnum):
    PENDING = "pending"       # request waiting to be polled
    DELIVERED = "delivered"   # request handed to a worker
    ACKED = "acked"           # request fully answered (final/error emitted)
    EXPIRED = "expired"       # run timeout
    FAILED = "failed"         # run cancelled / worker errored
    EMITTED = "emitted"       # event recorded (terminal for event rows)


class GatewayMessage(UuidPkMixin, TimestampMixin, WorkspaceScopedMixin, Base):
    __tablename__ = "gateway_messages"
    __table_args__ = (
        Index(
            "ix_gateway_messages_adapter_status",
            "adapter_id",
            "status",
            "created_at",
        ),
        Index(
            "ix_gateway_messages_run_direction",
            "run_id",
            "direction",
            "seq",
            "created_at",
        ),
        UniqueConstraint(
            "run_id", "direction", "seq",
            name="uq_gateway_messages_run_direction_seq",
        ),
    )

    adapter_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("backend_adapters.id", ondelete="CASCADE"),
        nullable=False,
    )
    run_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    session_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("sessions.id", ondelete="SET NULL"),
        nullable=True,
    )
    agent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("agents.id", ondelete="SET NULL"),
        nullable=True,
    )

    direction: Mapped[GatewayMessageDirection] = mapped_column(
        String(16), nullable=False
    )
    # Request rows use the constant ``"run"``. Event rows mirror RunEventKind
    # plus a synthetic ``"cancel"`` marker pushed by OpenClawBackend.cancel().
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    seq: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    payload_json: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)

    status: Mapped[GatewayMessageStatus] = mapped_column(
        String(16),
        default=GatewayMessageStatus.PENDING,
        nullable=False,
    )
    claimed_at: Mapped[datetime | None] = mapped_column(nullable=True)
