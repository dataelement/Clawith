"""Add plaintext api_key column to agents, alongside api_key_hash.

This decouples installer download from key rotation. The existing design
stored only the sha256 hash, so every installer download had to mint a
fresh key (the plaintext needed to bake into the installer was otherwise
unrecoverable). That invalidated any bridge currently using the key —
a bad UX when the user just wants to re-download for a different runtime
on the same machine.

Storing the plaintext alongside the hash lets download be idempotent:
reuse the existing plaintext if present, fall back to mint-and-store on
first download for legacy agents (nullable column, no backfill needed).
The hash stays for the legacy dual-path auth in gateway._get_agent_by_key.

Revision ID: add_agent_api_key
Revises: add_bridge_session_enum
Create Date: 2026-04-22
"""
from alembic import op


revision = "add_agent_api_key"
down_revision = "add_bridge_session_enum"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE agents ADD COLUMN IF NOT EXISTS api_key VARCHAR(128)"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE agents DROP COLUMN IF EXISTS api_key")
