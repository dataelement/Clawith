"""Add user/company onboarding state.

Revision ID: add_user_tenant_onboarding
Revises: add_agent_focus_items
Create Date: 2026-05-09
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "add_user_tenant_onboarding"
down_revision: Union[str, Sequence[str], None] = "add_agent_focus_items"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "user_tenant_onboardings",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="in_progress"),
        sa.Column("current_step", sa.String(length=32), nullable=False, server_default="assistant"),
        sa.Column("entry_mode", sa.String(length=32), nullable=False, server_default="create"),
        sa.Column("personal_assistant_agent_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("agents.id", ondelete="SET NULL"), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("user_id", "tenant_id", name="uq_user_tenant_onboarding"),
    )
    op.create_index("ix_user_tenant_onboardings_user_id", "user_tenant_onboardings", ["user_id"])
    op.create_index("ix_user_tenant_onboardings_tenant_id", "user_tenant_onboardings", ["tenant_id"])


def downgrade() -> None:
    op.drop_index("ix_user_tenant_onboardings_tenant_id", table_name="user_tenant_onboardings")
    op.drop_index("ix_user_tenant_onboardings_user_id", table_name="user_tenant_onboardings")
    op.drop_table("user_tenant_onboardings")
