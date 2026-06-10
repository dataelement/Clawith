"""Merge remaining release heads after PR #494.

Revision ID: merge_pr494_heads
Revises: add_default_mcp_servers, add_whatsapp_channel_support, rm_agent_credential_secrets
Create Date: 2026-04-28
"""

from typing import Sequence, Union


revision: str = "merge_pr494_heads"
down_revision: Union[str, Sequence[str], None] = (
    "add_default_mcp_servers",
    "add_whatsapp_channel_support",
    "rm_agent_credential_secrets",
)
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
