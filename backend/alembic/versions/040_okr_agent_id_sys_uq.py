"""Add okr_agent_id to okr_settings; add unique partial index on system agents.

Two changes in one migration:
  1. okr_settings.okr_agent_id  — stores the canonical OKR Agent UUID so all
     OKR APIs can look it up by ID rather than fuzzy-matching by name.
  2. agents partial unique index  — database-level guard that prevents more than
     one non-stopped system agent with the same name per tenant from ever being
     inserted, regardless of application-layer checks.

Revision ID: add_okr_agent_id_and_system_agent_uq
Revises: f1a2b3c4d5e6, add_agent_is_system
Create Date: 2026-04-15
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID


# revision identifiers
revision: str = "okr_agent_id_sys_uq"
down_revision: Union[str, tuple] = ("f1a2b3c4d5e6", "add_agent_is_system")
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    # 1. Add okr_agent_id column to okr_settings (nullable — may not exist yet
    #    on deployments that haven't run the OKR seeder since this migration)
    table_names = set(inspector.get_table_names())
    if "okr_settings" in table_names:
        okr_settings_columns = {col["name"] for col in inspector.get_columns("okr_settings")}
        if "okr_agent_id" not in okr_settings_columns:
            op.add_column(
                "okr_settings",
                sa.Column(
                    "okr_agent_id",
                    UUID(as_uuid=True),
                    nullable=True,
                    comment="UUID of the canonical OKR Agent for this tenant",
                ),
            )

    # 2. Create a partial unique index on agents so that the DB itself prevents
    #    duplicate active system agents with the same name per tenant.
    #    The WHERE clause excludes stopped agents so historical records are kept.
    if "agents" in table_names:
        agent_indexes = {idx["name"] for idx in inspector.get_indexes("agents")}
        if "uq_active_system_agent_name" not in agent_indexes:
            op.create_index(
                "uq_active_system_agent_name",
                "agents",
                ["tenant_id", "name"],
                unique=True,
                postgresql_where=sa.text("is_system = TRUE AND status != 'stopped'"),
            )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    table_names = set(inspector.get_table_names())

    if "agents" in table_names:
        agent_indexes = {idx["name"] for idx in inspector.get_indexes("agents")}
        if "uq_active_system_agent_name" in agent_indexes:
            op.drop_index("uq_active_system_agent_name", table_name="agents")

    if "okr_settings" in table_names:
        okr_settings_columns = {col["name"] for col in inspector.get_columns("okr_settings")}
        if "okr_agent_id" in okr_settings_columns:
            op.drop_column("okr_settings", "okr_agent_id")
