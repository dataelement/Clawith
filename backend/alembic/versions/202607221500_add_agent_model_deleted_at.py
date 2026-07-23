"""Add logical deletion markers for Agent and LLM Model.

Revision ID: add_agent_model_deleted_at
Revises: add_experience_revision_drafts
Create Date: 2026-07-22 15:00:00
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "add_agent_model_deleted_at"
down_revision: str | None = "add_experience_revision_drafts"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "agents",
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "llm_models",
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_agents_active_tenant_created_at",
        "agents",
        ["tenant_id", "created_at"],
        unique=False,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_index(
        "ix_llm_models_active_tenant_created_at",
        "llm_models",
        ["tenant_id", "created_at"],
        unique=False,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_llm_models_active_tenant_created_at", table_name="llm_models")
    op.drop_index("ix_agents_active_tenant_created_at", table_name="agents")
    op.drop_column("llm_models", "deleted_at")
    op.drop_column("agents", "deleted_at")
