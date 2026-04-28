"""Add Tenant.default_model_id + backfill per-tenant to earliest enabled model.

Revision ID: add_tenant_default_model
Revises: add_agent_bootstrap_fields
Create Date: 2026-04-23

Each tenant gets a default_model_id pointing at its first enabled LLM model
(by created_at ascending). Tenants with no enabled models stay NULL; the admin
picks one when they finally add a model (handled at the API layer).
"""
from typing import Sequence, Union

from alembic import op


revision: str = 'add_tenant_default_model'
down_revision: Union[str, None] = 'add_agent_bootstrap_fields'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add the nullable FK column. ON DELETE SET NULL — if a model is deleted,
    # tenants that pointed at it revert to "no default."
    op.execute("""
        ALTER TABLE tenants
        ADD COLUMN IF NOT EXISTS default_model_id UUID
        REFERENCES llm_models(id) ON DELETE SET NULL
    """)

    # Backfill: for each tenant, pick its earliest-created enabled model.
    op.execute("""
        UPDATE tenants t
        SET default_model_id = m.id
        FROM (
            SELECT DISTINCT ON (tenant_id) tenant_id, id
            FROM llm_models
            WHERE enabled = TRUE AND tenant_id IS NOT NULL
            ORDER BY tenant_id, created_at ASC
        ) m
        WHERE t.id = m.tenant_id AND t.default_model_id IS NULL
    """)


def downgrade() -> None:
    op.execute("ALTER TABLE tenants DROP COLUMN IF EXISTS default_model_id")
