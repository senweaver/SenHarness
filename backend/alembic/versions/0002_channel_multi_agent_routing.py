"""channel multi-agent routing (P0)

Adds the "single channel → multiple agents" routing surface:

* ``channels.routing_config_json`` — per-channel bind scope + policy blob
  (default ``{}`` = legacy ``bind_scope=agent`` behaviour).
* ``channel_user_link`` — ``(channel_id, external_user_id) → identity``.
* ``channel_conversation_state`` — sticky route + numbered-menu snapshot
  per ``(channel_id, peer_key)``.

Revision ID: 0002_channel_multi_agent_routing
Revises: 0001_initial_schema
Create Date: 2026-05-31 00:00:00.000000
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002_channel_multi_agent_routing"
down_revision: Union[str, Sequence[str], None] = "0001_initial_schema"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "channels",
        sa.Column(
            "routing_config_json",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
    )

    op.create_table(
        "channel_user_link",
        sa.Column("channel_id", sa.UUID(), nullable=False),
        sa.Column("external_user_id", sa.String(length=200), nullable=False),
        sa.Column("identity_id", sa.UUID(), nullable=False),
        sa.Column("verified_at", sa.DateTime(), nullable=True),
        sa.Column("verified_via", sa.String(length=16), nullable=False),
        sa.Column("created_by", sa.UUID(), nullable=True),
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(
            ["channel_id"],
            ["channels.id"],
            name=op.f("fk_channel_user_link_channel_id_channels"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["identities.id"],
            name=op.f("fk_channel_user_link_created_by_identities"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["identity_id"],
            ["identities.id"],
            name=op.f("fk_channel_user_link_identity_id_identities"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name=op.f("fk_channel_user_link_workspace_id_workspaces"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_channel_user_link")),
        sa.UniqueConstraint(
            "channel_id",
            "external_user_id",
            name="uq_channel_user_link_channel_user",
        ),
    )
    op.create_index(
        op.f("ix_channel_user_link_channel_id"),
        "channel_user_link",
        ["channel_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_channel_user_link_external_user_id"),
        "channel_user_link",
        ["external_user_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_channel_user_link_identity_id"),
        "channel_user_link",
        ["identity_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_channel_user_link_workspace_id"),
        "channel_user_link",
        ["workspace_id"],
        unique=False,
    )

    op.create_table(
        "channel_conversation_state",
        sa.Column("channel_id", sa.UUID(), nullable=False),
        sa.Column("peer_key", sa.String(length=200), nullable=False),
        sa.Column("active_workspace_id", sa.UUID(), nullable=True),
        sa.Column("active_agent_id", sa.UUID(), nullable=True),
        sa.Column("last_menu_at", sa.DateTime(), nullable=True),
        sa.Column(
            "last_menu_options_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(
            ["active_agent_id"],
            ["agents.id"],
            name=op.f("fk_channel_conversation_state_active_agent_id_agents"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["channel_id"],
            ["channels.id"],
            name=op.f("fk_channel_conversation_state_channel_id_channels"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name=op.f("fk_channel_conversation_state_workspace_id_workspaces"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_channel_conversation_state")),
        sa.UniqueConstraint(
            "channel_id",
            "peer_key",
            name="uq_channel_conversation_state_channel_peer",
        ),
    )
    op.create_index(
        op.f("ix_channel_conversation_state_channel_id"),
        "channel_conversation_state",
        ["channel_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_channel_conversation_state_workspace_id"),
        "channel_conversation_state",
        ["workspace_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_channel_conversation_state_workspace_id"),
        table_name="channel_conversation_state",
    )
    op.drop_index(
        op.f("ix_channel_conversation_state_channel_id"),
        table_name="channel_conversation_state",
    )
    op.drop_table("channel_conversation_state")

    op.drop_index(
        op.f("ix_channel_user_link_workspace_id"),
        table_name="channel_user_link",
    )
    op.drop_index(
        op.f("ix_channel_user_link_identity_id"),
        table_name="channel_user_link",
    )
    op.drop_index(
        op.f("ix_channel_user_link_external_user_id"),
        table_name="channel_user_link",
    )
    op.drop_index(
        op.f("ix_channel_user_link_channel_id"),
        table_name="channel_user_link",
    )
    op.drop_table("channel_user_link")

    op.drop_column("channels", "routing_config_json")
