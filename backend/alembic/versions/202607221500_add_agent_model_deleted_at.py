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


def _columns(table_name: str) -> set[str]:
    inspector = sa.inspect(op.get_bind())
    return {column["name"] for column in inspector.get_columns(table_name)}


def _indexes(table_name: str) -> set[str]:
    inspector = sa.inspect(op.get_bind())
    return {index["name"] for index in inspector.get_indexes(table_name)}


def upgrade() -> None:
    # Some deployments created this column before Alembic recorded this
    # revision. Inspect first so those databases can safely reach head.
    if "deleted_at" not in _columns("agents"):
        op.add_column(
            "agents",
            sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        )
    if "deleted_at" not in _columns("llm_models"):
        op.add_column(
            "llm_models",
            sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        )

    if "ix_agents_active_tenant_created_at" not in _indexes("agents"):
        op.create_index(
            "ix_agents_active_tenant_created_at",
            "agents",
            ["tenant_id", "created_at"],
            unique=False,
            postgresql_where=sa.text("deleted_at IS NULL"),
        )
    if "ix_llm_models_active_tenant_created_at" not in _indexes("llm_models"):
        op.create_index(
            "ix_llm_models_active_tenant_created_at",
            "llm_models",
            ["tenant_id", "created_at"],
            unique=False,
            postgresql_where=sa.text("deleted_at IS NULL"),
        )


def downgrade() -> None:
    if "ix_llm_models_active_tenant_created_at" in _indexes("llm_models"):
        op.drop_index("ix_llm_models_active_tenant_created_at", table_name="llm_models")
    if "ix_agents_active_tenant_created_at" in _indexes("agents"):
        op.drop_index("ix_agents_active_tenant_created_at", table_name="agents")
    if "deleted_at" in _columns("llm_models"):
        op.drop_column("llm_models", "deleted_at")
    if "deleted_at" in _columns("agents"):
        op.drop_column("agents", "deleted_at")
