"""Add a tenant-owned model override for multi-Agent planning.

Revision ID: add_tenant_planning_model
Revises: add_chat_message_cursor_index
Create Date: 2026-07-14 16:00:00
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "add_tenant_planning_model"
down_revision: str | None = "add_chat_message_cursor_index"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "tenants",
        sa.Column("planning_model_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_tenants_planning_model_id_llm_models",
        "tenants",
        "llm_models",
        ["planning_model_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_tenants_planning_model_id_llm_models",
        "tenants",
        type_="foreignkey",
    )
    op.drop_column("tenants", "planning_model_id")
