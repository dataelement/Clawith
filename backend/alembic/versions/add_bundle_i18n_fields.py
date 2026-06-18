"""Add bilingual fields (name_en / description_en / capability_bullets_en) to agent_bundles.

Bundle metadata is currently CN-only (name="AU 沪金 8-Agent 决策团队" etc.). When an
EN user views the Talent Market, the bundle card still renders the CN string raw.
Per-bundle EN fields let authors ship bilingual cards while staying backwards
compatible — when ``*_en`` fields are absent the frontend falls back to the
primary (CN) field so zh-only authors keep working without change.

Revision ID: add_bundle_i18n_fields
Revises: add_agent_is_from_bundle
Create Date: 2026-05-20
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSON


revision = "add_bundle_i18n_fields"
down_revision = "add_agent_is_from_bundle"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("agent_bundles", sa.Column("name_en", sa.String(length=200), nullable=True))
    op.add_column("agent_bundles", sa.Column("description_en", sa.Text(), nullable=True))
    op.add_column("agent_bundles", sa.Column("capability_bullets_en", JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("agent_bundles", "capability_bullets_en")
    op.drop_column("agent_bundles", "description_en")
    op.drop_column("agent_bundles", "name_en")
