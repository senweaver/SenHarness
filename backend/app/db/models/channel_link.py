"""Channel → multi-agent routing state (P0 + P1).

Three tables back the "single channel → multiple agents" routing feature:

* ``channel_user_link`` — maps a platform-side user
  ``(channel_id, external_user_id)`` to a SenHarness :class:`Identity`.
  The WeChat QR-login confirm point writes the scanning operator's link
  with ``verified_via='qr_scan'``; the ``/bind <code>`` chat command and
  admin tooling cover everyone else. The link is what lets a routed run
  execute *as the resolved identity* (correct quota / audit / approval),
  and is the precondition for the ``user`` bind scope (one WeChat that can
  reach an identity's agents across every workspace they belong to).

* ``channel_conversation_state`` — the current route for one conversation
  ``(channel_id, peer_key, sender_key)``. ``peer_key`` is the
  provider-neutral conversation key (WeChat private-chat peer,
  Feishu/Slack ``chat_id``). ``sender_key`` is empty (``""``) for the
  shared group/conversation-level route (P0 default) and the individual
  sender id for a P1 per-sender override inside a group. Holds the sticky
  ``(active_workspace_id, active_agent_id)`` plus the numbered-selection
  menu snapshot so a bare "2" within the selection window can resolve
  back to the agent it labelled.

* ``channel_binding`` (P1) — layered "most-specific-wins" routing rules.
  A channel may carry several bindings; resolution ranks them by
  ``match_scope`` specificity (``peer`` > ``group`` > ``channel_default``,
  with the higher ladder rungs reserved for future providers) and falls
  back to the channel-level ``routing_config_json`` when no binding
  matches — so an empty table is exactly the P0 behaviour.

The first two align with :mod:`app.db.models.logical_thread`'s mixin stack
(``UuidPk + Timestamp + SoftDelete + WorkspaceScoped``) so the retention
sweep can cascade by ``workspace_id`` without bespoke SQL. ``workspace_id``
here is always the channel's *owning* workspace; the routed target
workspace lives in ``active_workspace_id`` (which may differ under the
``user`` scope).
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import ForeignKey, Integer, String, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.mixins import (
    SoftDeleteMixin,
    TimestampMixin,
    UuidPkMixin,
    WorkspaceScopedMixin,
)

# ``verified_via`` accepted values — kept as a plain String(16) column (no
# native enum) to match the rest of the schema's "open string" convention.
VERIFIED_VIA_VALUES = ("qr_scan", "bind_code", "pairing", "admin")

# ``channel_binding.match_scope`` accepted values, most-specific first.
# Only ``peer`` / ``group`` / ``channel_default`` are actively matched by
# the P0+P1 providers; the remaining rungs are reserved so the OpenClaw-
# style 8-level ladder can be filled in without another migration.
BINDING_MATCH_SCOPES = (
    "peer",
    "thread",
    "role",
    "guild",
    "team",
    "account",
    "group",
    "channel_default",
)


class ChannelUserLink(UuidPkMixin, TimestampMixin, SoftDeleteMixin, WorkspaceScopedMixin, Base):
    __tablename__ = "channel_user_link"
    __table_args__ = (
        UniqueConstraint(
            "channel_id",
            "external_user_id",
            name="uq_channel_user_link_channel_user",
        ),
    )

    channel_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("channels.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    external_user_id: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    identity_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("identities.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    verified_at: Mapped[datetime | None] = mapped_column(nullable=True)
    verified_via: Mapped[str] = mapped_column(String(16), nullable=False)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("identities.id", ondelete="SET NULL"),
        nullable=True,
    )


class ChannelConversationState(
    UuidPkMixin, TimestampMixin, SoftDeleteMixin, WorkspaceScopedMixin, Base
):
    __tablename__ = "channel_conversation_state"
    __table_args__ = (
        UniqueConstraint(
            "channel_id",
            "peer_key",
            "sender_key",
            name="uq_channel_conversation_state_channel_peer",
        ),
    )

    channel_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("channels.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    peer_key: Mapped[str] = mapped_column(String(200), nullable=False)

    # P1 per-sender override discriminator. ``""`` (the server default) is
    # the shared conversation/group-level route — i.e. the exact P0 row.
    # A non-empty value is one sender's personal override inside a group
    # (``group_override="per_sender"``), resolved ahead of the shared row.
    sender_key: Mapped[str] = mapped_column(
        String(200),
        nullable=False,
        server_default=text("''"),
        default="",
    )

    # The routed target. ``active_workspace_id`` is a plain UUID (not an FK)
    # because under the ``user`` scope it can point at a workspace other
    # than the channel's owner; we don't want a CASCADE from the channel's
    # workspace. ``active_agent_id`` SET NULLs so a deleted agent simply
    # drops the stickiness back to the default on the next inbound.
    active_workspace_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    active_agent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("agents.id", ondelete="SET NULL"),
        nullable=True,
    )

    # Numbered-selection support. ``last_menu_options_json`` is the ordered
    # list the welcome/menu copy rendered — ``[{alias, agent_id,
    # workspace_id}]`` — so a bare digit within ``selection_window_seconds``
    # of ``last_menu_at`` resolves to the agent it labelled.
    last_menu_at: Mapped[datetime | None] = mapped_column(nullable=True)
    last_menu_options_json: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)


class ChannelBinding(UuidPkMixin, TimestampMixin, SoftDeleteMixin, WorkspaceScopedMixin, Base):
    """One layered routing rule for a channel (P1 "most-specific-wins").

    A binding narrows or redirects routing for a slice of the channel's
    traffic identified by ``(match_scope, match_value)``:

    * ``channel_default`` (``match_value`` ignored) — the channel-wide
      fallback rung; equivalent to the channel-level config but expressible
      as a row so it can carry its own scope / default agent / allowlist.
    * ``peer`` — matches a single conversation key (a DM peer or a group
      ``chat_id``).
    * ``group`` — matches a group ``chat_id`` (only in group chats).

    Any non-null override field replaces the channel-level value for matched
    traffic; null fields inherit. ``priority`` breaks ties between bindings
    of equal specificity (higher wins).
    """

    __tablename__ = "channel_binding"
    __table_args__ = (
        UniqueConstraint(
            "channel_id",
            "match_scope",
            "match_value",
            name="uq_channel_binding_channel_scope_value",
        ),
    )

    channel_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("channels.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    match_scope: Mapped[str] = mapped_column(String(16), nullable=False)
    # NULL for ``channel_default``; the peer_key / chat_id otherwise. Stored
    # as empty-string-able so the unique constraint stays meaningful.
    match_value: Mapped[str | None] = mapped_column(String(200), nullable=True)

    # Routing overrides. NULL = inherit the channel-level value.
    bind_scope: Mapped[str | None] = mapped_column(String(16), nullable=True)
    scope_ref_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    target_agent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("agents.id", ondelete="SET NULL"),
        nullable=True,
    )
    allowlist_agent_ids_json: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    priority: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0"), default=0
    )
