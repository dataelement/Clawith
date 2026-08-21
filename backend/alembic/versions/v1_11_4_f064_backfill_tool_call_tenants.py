"""Backfill tenant ownership for tool-call chat history.

Revision ID: f064_tool_call_tenants
Revises: f063_merge_v1_11_4_heads
Create Date: 2026-08-14 00:00:00
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "f064_tool_call_tenants"
down_revision: str | Sequence[str] | None = "f063_merge_v1_11_4_heads"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Restore tenant visibility for tool calls written after the F060 backfill."""
    op.execute(
        """
        UPDATE chat_messages
        SET tenant_id = agents.tenant_id
        FROM agents
        WHERE chat_messages.agent_id = agents.id
          AND chat_messages.role = 'tool_call'
          AND chat_messages.tenant_id IS NULL;
        """
    )
    op.execute(
        """
        UPDATE chat_messages
        SET tenant_id = chat_sessions.tenant_id
        FROM chat_sessions
        WHERE chat_messages.conversation_id = chat_sessions.id::text
          AND chat_messages.role = 'tool_call'
          AND chat_messages.tenant_id IS NULL;
        """
    )


def downgrade() -> None:
    """Data ownership backfills are intentionally not reversed."""
