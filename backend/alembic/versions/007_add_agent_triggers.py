"""Add agent_triggers table for Pulse engine.

Revision ID: add_agent_triggers
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB

revision = "add_agent_triggers"
down_revision = "add_participants"
branch_labels = None
depends_on = None


def upgrade():
    conn = op.get_bind()
    if conn.dialect.has_table(conn, "agent_triggers"):
        return
    op.create_table(
        "agent_triggers",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("agent_id", UUID(as_uuid=True), sa.ForeignKey("agents.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("type", sa.String(20), nullable=False),
        sa.Column("config", JSONB, nullable=False, server_default=sa.text("'{}'")),
        sa.Column("reason", sa.Text, nullable=False, server_default=""),
        sa.Column("agenda_ref", sa.String(200)),
        sa.Column("is_enabled", sa.Boolean, server_default=sa.text("true")),
        sa.Column("last_fired_at", sa.DateTime(timezone=True)),
        sa.Column("fire_count", sa.Integer, server_default=sa.text("0")),
        sa.Column("max_fires", sa.Integer),
        sa.Column("cooldown_seconds", sa.Integer, server_default=sa.text("60")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("expires_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("agent_id", "name", name="uq_agent_trigger_name"),
    )


def downgrade():
    conn = op.get_bind()
    if not conn.dialect.has_table(conn, "agent_triggers"):
        return
    op.drop_table("agent_triggers")
