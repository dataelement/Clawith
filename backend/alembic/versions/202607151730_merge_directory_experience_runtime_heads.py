"""merge Directory, experience-library, and durable-Runtime migration heads

Revision ID: merge_dir_exp_runtime_heads
Revises: add_agent_directory_indexes, experience_markdown_body,
         add_tenant_planning_model
Create Date: 2026-07-15 17:30:00
"""

from collections.abc import Sequence


revision: str = "merge_dir_exp_runtime_heads"
down_revision: tuple[str, str, str] = (
    "add_agent_directory_indexes",
    "experience_markdown_body",
    "add_tenant_planning_model",
)
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Join the three independently developed feature branches."""


def downgrade() -> None:
    """Split back to the three feature heads without changing schema."""
