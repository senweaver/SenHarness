"""IM Channel — connects an external messaging platform (Slack / Feishu /
Discord / generic webhook) to an Agent or Squad in this workspace.

Inbound:
    external provider  →  POST /api/v1/hooks/ingress/{channel_id}?token=...
    We authenticate the channel by ``inbound_token`` (shared secret), route
    the event to the bound Agent/Squad, and write the conversation into a
    normal Session row with ``kind=channel``.

Outbound:
    After the Agent responds, we call the provider's chat API using
    ``config_json.bot_token`` to post the reply back to the origin thread.
"""

from __future__ import annotations

import uuid

from sqlalchemy import ForeignKey, Index, String, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.mixins import SoftDeleteMixin, TimestampMixin, UuidPkMixin, WorkspaceScopedMixin


class ChannelKind:
    """Known IM channel kinds (not an exhaustive enum).

    V2 relaxed this from a closed ``StrEnum`` for the same reason
    :class:`app.db.models.agent.BackendKind` got relaxed — the
    Provider registry (see ``services/channels/__init__.py``) is the
    runtime authority on what's installable. Listing constants here
    keeps IDE autocomplete and seed defaults readable, but the DB
    column accepts any string that matches the schema pattern.

    Bundled providers (one file each in ``services/channels/``):

        SLACK      — Slack workspace bot, v0 HMAC + 5-min replay window
        FEISHU     — Feishu / Lark, verification_token (1.0 / 2.0)
        DISCORD    — Discord app, Ed25519 signature
        WEBHOOK    — generic JSON inbound, no outbound reply
        DINGTALK   — DingTalk robot, HMAC-SHA256 URL signature
        WECOM      — WeChat Work app, AES-encrypted push with
                     VerifyURL / echo-str handshake
    """

    SLACK = "slack"
    FEISHU = "feishu"
    DISCORD = "discord"
    WEBHOOK = "webhook"
    DINGTALK = "dingtalk"
    WECOM = "wecom"


class Channel(UuidPkMixin, TimestampMixin, SoftDeleteMixin, WorkspaceScopedMixin, Base):
    __tablename__ = "channels"
    __table_args__ = (
        Index("ix_channels_workspace_kind", "workspace_id", "kind"),
        Index("ix_channels_external_app_id_hash", "external_app_id_hash"),
    )

    name: Mapped[str] = mapped_column(String(128), nullable=False)
    # Widened from String(16) → String(32) so future longer kinds
    # ("whatsapp_business_api", "homeassistant", etc.) don't need a
    # migration. Whatever the registry accepts lives here.
    kind: Mapped[str] = mapped_column(String(32), nullable=False)

    # Shared secret — clients must pass ``?token=<inbound_token>`` to call the
    # ingress endpoint. Rotate via the admin UI if leaked.
    inbound_token: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)

    # Provider-specific JSON: bot_token, signing_secret, workspace team_id, etc.
    # Values that look like secrets are masked in GET responses.
    config_json: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)

    # M0.8: per-channel sender ACL. Default ``{}`` means ``allow_all`` so
    # rows that pre-date this column behave exactly like before.
    # Schema: ``{"mode": "allow_all" | "allow_listed" | "deny_listed",
    #           "allow": [...external_user_id], "deny": [...]}``.
    sender_allowlist_json: Mapped[dict] = mapped_column(
        JSONB,
        default=dict,
        server_default=text("'{}'::jsonb"),
        nullable=False,
    )

    # P0 "single channel → multi-agent routing" config. Default ``{}`` means
    # ``bind_scope="agent"`` — i.e. the legacy "this channel talks to exactly
    # ``default_agent_id``" behaviour, fully backward compatible. Recognised
    # keys (see ``app.services.channel_routing.parse_routing_config``):
    #   bind_scope               agent|workspace|user        (default agent)
    #   scope_ref_id             uuid|null  (workspace_id when scope=workspace)
    #   allowlist_agent_ids      [uuid]|null (narrows the resolved pool)
    #   dm_policy                open|allowlist|disabled|pairing (default open)
    #   group_policy             same set                    (default disabled)
    #   menu_style               auto|text|buttons           (default auto)
    #   selection_window_seconds int                         (default 300)
    #   reply_attribution        prefix|identity|off         (default prefix)
    # Kept as a JSON column for P0; promote hot keys to indexed columns later.
    routing_config_json: Mapped[dict] = mapped_column(
        JSONB,
        default=dict,
        server_default=text("'{}'::jsonb"),
        nullable=False,
    )

    # M0.8: SHA-256 of the per-kind external app identity (e.g. discord
    # bot_token, slack signing_secret). Used by the partial unique index
    # ``uq_channel_external_app_per_kind`` to refuse the same bot being
    # bound to two workspaces simultaneously. ``NULL`` for kinds with no
    # stable external identity (e.g. generic webhook).
    external_app_id_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)

    default_agent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("agents.id", ondelete="SET NULL"),
        nullable=True,
    )
    default_squad_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("squads.id", ondelete="SET NULL"),
        nullable=True,
    )

    enabled: Mapped[bool] = mapped_column(default=True, nullable=False)
    metadata_json: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)

    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("identities.id", ondelete="SET NULL"),
        nullable=True,
    )
