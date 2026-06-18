"""Add bundle-group sidebar fields to agents + principal_slug to agent_bundles.

A tenant who hires bundle(s) ends up with N agents in their sidebar — for AU8,
that's 8 rows that all conceptually belong to one "Star Team." Without grouping
the list overflows; without a designated principal the user doesn't know who to
chat with first. This migration adds the columns needed for both UX fixes:

- agents.bundle_slug — denormalized for sidebar header lookup
- agents.bundle_hire_group_id — UUID per hire tx so re-hires fold separately
- agents.is_bundle_principal — marks the yellow-star principal
- agent_bundles.principal_slug — bundle author declares which agent is primary

All columns are nullable / default False so the migration is safe on existing
data; existing bundle agents stay ungrouped until a manual backfill or re-hire.

Revision ID: add_agent_bundle_group_fields
Revises: add_bundle_i18n_fields
Create Date: 2026-05-20
"""

from alembic import op
import sqlalchemy as sa


revision = "add_agent_bundle_group_fields"
down_revision = "add_bundle_i18n_fields"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "agents",
        sa.Column("bundle_slug", sa.String(length=100), nullable=True),
    )
    op.add_column(
        "agents",
        sa.Column(
            "bundle_hire_group_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
    )
    op.add_column(
        "agents",
        sa.Column(
            "is_bundle_principal",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.add_column(
        "agent_bundles",
        sa.Column("principal_slug", sa.String(length=100), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("agent_bundles", "principal_slug")
    op.drop_column("agents", "is_bundle_principal")
    op.drop_column("agents", "bundle_hire_group_id")
    op.drop_column("agents", "bundle_slug")
