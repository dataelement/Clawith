"""Remove plaintext-style agent credential fields.

Revision ID: rm_agent_credential_secrets
Revises: add_primary_chat_sessions_unread, add_wechat_channel_support, add_workspace_revisions, merge_okr_api_key
Create Date: 2026-04-22
"""

from typing import Sequence, Union

from alembic import op


revision: str = "rm_agent_credential_secrets"
down_revision: Union[str, Sequence[str], None] = (
    "add_primary_chat_sessions_unread",
    "add_wechat_channel_support",
    "add_workspace_revisions",
    "merge_okr_api_key",
)
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TABLE agent_credentials DROP COLUMN IF EXISTS username")
    op.execute("ALTER TABLE agent_credentials DROP COLUMN IF EXISTS password")
    op.execute("ALTER TABLE agent_credentials DROP COLUMN IF EXISTS login_url")


def downgrade() -> None:
    op.execute("ALTER TABLE agent_credentials ADD COLUMN IF NOT EXISTS username TEXT")
    op.execute("ALTER TABLE agent_credentials ADD COLUMN IF NOT EXISTS password TEXT")
    op.execute("ALTER TABLE agent_credentials ADD COLUMN IF NOT EXISTS login_url VARCHAR(500)")
