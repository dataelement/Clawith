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


AGENTS_TABLE = "agents"
LLM_MODELS_TABLE = "llm_models"
DELETED_AT_COLUMN = "deleted_at"
AGENTS_ACTIVE_INDEX = "ix_agents_active_tenant_created_at"
LLM_MODELS_ACTIVE_INDEX = "ix_llm_models_active_tenant_created_at"


def _inspector():
    return sa.inspect(op.get_bind())


def _column_exists(table_name: str, column_name: str) -> bool:
    return column_name in {
        column["name"] for column in _inspector().get_columns(table_name)
    }


def _index_exists(table_name: str, index_name: str) -> bool:
    return index_name in {
        index["name"] for index in _inspector().get_indexes(table_name)
    }


def upgrade() -> None:
    if not _column_exists(AGENTS_TABLE, DELETED_AT_COLUMN):
        op.add_column(
            AGENTS_TABLE,
            sa.Column(DELETED_AT_COLUMN, sa.DateTime(timezone=True), nullable=True),
        )

    if not _column_exists(LLM_MODELS_TABLE, DELETED_AT_COLUMN):
        op.add_column(
            LLM_MODELS_TABLE,
            sa.Column(DELETED_AT_COLUMN, sa.DateTime(timezone=True), nullable=True),
        )

    if not _index_exists(AGENTS_TABLE, AGENTS_ACTIVE_INDEX):
        op.create_index(
            AGENTS_ACTIVE_INDEX,
            AGENTS_TABLE,
            ["tenant_id", "created_at"],
            unique=False,
            postgresql_where=sa.text("deleted_at IS NULL"),
        )

    if not _index_exists(LLM_MODELS_TABLE, LLM_MODELS_ACTIVE_INDEX):
        op.create_index(
            LLM_MODELS_ACTIVE_INDEX,
            LLM_MODELS_TABLE,
            ["tenant_id", "created_at"],
            unique=False,
            postgresql_where=sa.text("deleted_at IS NULL"),
        )


def downgrade() -> None:
    if _index_exists(LLM_MODELS_TABLE, LLM_MODELS_ACTIVE_INDEX):
        op.drop_index(LLM_MODELS_ACTIVE_INDEX, table_name=LLM_MODELS_TABLE)

    if _index_exists(AGENTS_TABLE, AGENTS_ACTIVE_INDEX):
        op.drop_index(AGENTS_ACTIVE_INDEX, table_name=AGENTS_TABLE)

    if _column_exists(LLM_MODELS_TABLE, DELETED_AT_COLUMN):
        op.drop_column(LLM_MODELS_TABLE, DELETED_AT_COLUMN)

    if _column_exists(AGENTS_TABLE, DELETED_AT_COLUMN):
        op.drop_column(AGENTS_TABLE, DELETED_AT_COLUMN)
