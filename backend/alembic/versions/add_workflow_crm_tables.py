"""Add workflow and CRM tables.

Revision ID: add_workflow_crm
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSON

revision = "add_workflow_crm"
down_revision = None  # standalone migration, safe to run in any order
branch_labels = None
depends_on = None


def upgrade():
    conn = op.get_bind()

    # ─── Workflows ─────────────────────────────────────
    if not conn.dialect.has_table(conn, "workflows"):
        op.create_table(
            "workflows",
            sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
            sa.Column("title", sa.String(500), nullable=False),
            sa.Column("user_instruction", sa.Text, nullable=False),
            sa.Column("status", sa.String(20), nullable=False, server_default="planning"),
            sa.Column("created_by", UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("tenant_id", UUID(as_uuid=True), sa.ForeignKey("tenants.id"), nullable=False),
            sa.Column("summary", sa.Text),
            sa.Column("next_steps", sa.Text),
            sa.Column("plan_data", JSON),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
            sa.Column("completed_at", sa.DateTime(timezone=True)),
        )

    if not conn.dialect.has_table(conn, "workflow_steps"):
        op.create_table(
            "workflow_steps",
            sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
            sa.Column("workflow_id", UUID(as_uuid=True), sa.ForeignKey("workflows.id", ondelete="CASCADE"), nullable=False),
            sa.Column("agent_id", UUID(as_uuid=True), sa.ForeignKey("agents.id")),
            sa.Column("task_id", UUID(as_uuid=True), sa.ForeignKey("tasks.id")),
            sa.Column("step_order", sa.Integer, nullable=False),
            sa.Column("title", sa.String(500), nullable=False),
            sa.Column("instruction", sa.Text),
            sa.Column("agent_name", sa.String(100)),
            sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
            sa.Column("deliverable_type", sa.String(50), server_default="markdown"),
            sa.Column("deliverable_data", JSON),
            sa.Column("raw_output", sa.Text),
            sa.Column("started_at", sa.DateTime(timezone=True)),
            sa.Column("completed_at", sa.DateTime(timezone=True)),
        )

    # ─── CRM ───────────────────────────────────────────
    if not conn.dialect.has_table(conn, "crm_contacts"):
        op.create_table(
            "crm_contacts",
            sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
            sa.Column("tenant_id", UUID(as_uuid=True), sa.ForeignKey("tenants.id"), nullable=False, index=True),
            sa.Column("name", sa.String(200), nullable=False),
            sa.Column("company", sa.String(300)),
            sa.Column("email", sa.String(300)),
            sa.Column("phone", sa.String(100)),
            sa.Column("country", sa.String(100)),
            sa.Column("industry", sa.String(200)),
            sa.Column("source", sa.String(100)),
            sa.Column("tags", JSON, server_default=sa.text("'[]'")),
            sa.Column("chatwoot_contact_id", sa.Integer),
            sa.Column("notes", sa.Text),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        )

    if not conn.dialect.has_table(conn, "crm_deals"):
        op.create_table(
            "crm_deals",
            sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
            sa.Column("tenant_id", UUID(as_uuid=True), sa.ForeignKey("tenants.id"), nullable=False, index=True),
            sa.Column("contact_id", UUID(as_uuid=True), sa.ForeignKey("crm_contacts.id", ondelete="CASCADE"), nullable=False),
            sa.Column("title", sa.String(300), nullable=False),
            sa.Column("stage", sa.String(50), nullable=False, server_default="lead"),
            sa.Column("value", sa.Numeric(12, 2)),
            sa.Column("currency", sa.String(10), server_default="USD"),
            sa.Column("notes", sa.Text),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        )

    if not conn.dialect.has_table(conn, "crm_activities"):
        op.create_table(
            "crm_activities",
            sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
            sa.Column("tenant_id", UUID(as_uuid=True), sa.ForeignKey("tenants.id"), nullable=False, index=True),
            sa.Column("contact_id", UUID(as_uuid=True), sa.ForeignKey("crm_contacts.id", ondelete="CASCADE"), nullable=False),
            sa.Column("type", sa.String(50), nullable=False),
            sa.Column("summary", sa.Text, nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        )


def downgrade():
    op.drop_table("crm_activities")
    op.drop_table("crm_deals")
    op.drop_table("crm_contacts")
    op.drop_table("workflow_steps")
    op.drop_table("workflows")
