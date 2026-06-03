"""channel layered binding + per-sender override (P1)

Builds on ``0002_channel_multi_agent_routing``:

* ``channel_binding`` — layered "most-specific-wins" routing rules per
  channel (``peer`` > ``group`` > ``channel_default``). An empty table
  degrades to the channel-level ``routing_config_json`` (P0 behaviour).
* ``channel_conversation_state.sender_key`` — per-sender override
  discriminator inside a group. ``""`` (server default) is the shared
  group/conversation route, so existing P0 rows keep their meaning; the
  unique key widens to ``(channel_id, peer_key, sender_key)``.

Revision ID: 0003_channel_layered_binding
Revises: 0002_channel_multi_agent_routing
Create Date: 2026-06-01 00:00:00.000000
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003_channel_layered_binding"
down_revision: Union[str, Sequence[str], None] = "0002_channel_multi_agent_routing"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── Per-sender override on the conversation state ──
    op.add_column(
        "channel_conversation_state",
        sa.Column(
            "sender_key",
            sa.String(length=200),
            server_default=sa.text("''"),
            nullable=False,
        ),
    )
    op.drop_constraint(
        "uq_channel_conversation_state_channel_peer",
        "channel_conversation_state",
        type_="unique",
    )
    op.create_unique_constraint(
        "uq_channel_conversation_state_channel_peer",
        "channel_conversation_state",
        ["channel_id", "peer_key", "sender_key"],
    )

    # ── Layered binding rules ──
    op.create_table(
        "channel_binding",
        sa.Column("channel_id", sa.UUID(), nullable=False),
        sa.Column("match_scope", sa.String(length=16), nullable=False),
        sa.Column("match_value", sa.String(length=200), nullable=True),
        sa.Column("bind_scope", sa.String(length=16), nullable=True),
        sa.Column("scope_ref_id", sa.UUID(), nullable=True),
        sa.Column("target_agent_id", sa.UUID(), nullable=True),
        sa.Column(
            "allowlist_agent_ids_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column("priority", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(
            ["channel_id"],
            ["channels.id"],
            name=op.f("fk_channel_binding_channel_id_channels"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["target_agent_id"],
            ["agents.id"],
            name=op.f("fk_channel_binding_target_agent_id_agents"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name=op.f("fk_channel_binding_workspace_id_workspaces"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_channel_binding")),
        sa.UniqueConstraint(
            "channel_id",
            "match_scope",
            "match_value",
            name="uq_channel_binding_channel_scope_value",
        ),
    )
    op.create_index(
        op.f("ix_channel_binding_channel_id"),
        "channel_binding",
        ["channel_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_channel_binding_workspace_id"),
        "channel_binding",
        ["workspace_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_channel_binding_workspace_id"), table_name="channel_binding")
    op.drop_index(op.f("ix_channel_binding_channel_id"), table_name="channel_binding")
    op.drop_table("channel_binding")

    op.drop_constraint(
        "uq_channel_conversation_state_channel_peer",
        "channel_conversation_state",
        type_="unique",
    )
    op.create_unique_constraint(
        "uq_channel_conversation_state_channel_peer",
        "channel_conversation_state",
        ["channel_id", "peer_key"],
    )
    op.drop_column("channel_conversation_state", "sender_key")
