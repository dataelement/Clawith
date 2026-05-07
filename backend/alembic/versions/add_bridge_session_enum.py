"""Add remaining bridge session/tool action types to activity_action_enum.

Revision ID: add_bridge_session_enum
Revises: add_bridge_activity_enum

Beyond bridge_attached/bridge_detached (added in the prior migration),
the bridge code also logs per-session events and reverse-tool-call
events. The enum was missing all of them, so every bridge session
silently dropped its audit trail.

Values added:
  - bridge_installer_download  (agents.py download_bridge_installer)
  - local_session_start        (session_dispatcher)
  - local_session_done
  - local_session_error
  - reverse_tool_call          (bridge-initiated tool calls)
  - reverse_tool_result
"""
from alembic import op


revision = 'add_bridge_session_enum'
down_revision = 'add_bridge_activity_enum'
branch_labels = None
depends_on = None


_NEW_VALUES = (
    "bridge_installer_download",
    "local_session_start",
    "local_session_done",
    "local_session_error",
    "reverse_tool_call",
    "reverse_tool_result",
)


def upgrade() -> None:
    for v in _NEW_VALUES:
        op.execute(f"ALTER TYPE activity_action_enum ADD VALUE IF NOT EXISTS '{v}'")


def downgrade() -> None:
    # PostgreSQL does not support removing values from an enum type.
    pass
