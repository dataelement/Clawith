"""Add configurable memory context limit to agents.

Background:
    The memory.md context limit was previously hardcoded to 2,000 characters.

Scope:
    Add a per-agent memory_context_max_chars column with a default value of 2,000.

Idempotent:
    The column is added only when it does not already exist.

Revision ID: f066_configurable_memory_context
Revises: f065_feishu_group_target
Create Date: 2026-09-02
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "f066_configurable_memory_context"
down_revision: str | Sequence[str] | None = "f065_feishu_group_target"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    columns = {
        column["name"]
        for column in inspector.get_columns("agents")
    }

    if "memory_context_max_chars" not in columns:
        op.add_column(
            "agents",
            sa.Column(
                "memory_context_max_chars",
                sa.Integer(),
                nullable=False,
                server_default=sa.text("2000"),
            ),
        )
        op.alter_column(
            "agents",
            "memory_context_max_chars",
            server_default=None,
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    columns = {
        column["name"]
        for column in inspector.get_columns("agents")
    }

    if "memory_context_max_chars" in columns:
        op.drop_column("agents", "memory_context_max_chars")