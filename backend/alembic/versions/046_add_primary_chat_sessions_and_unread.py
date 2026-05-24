"""Add primary first-party chat sessions and per-session read tracking.

Revision ID: add_primary_chat_sessions_unread
Revises: add_member_daily_company_reports
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "add_primary_chat_sessions_unread"
down_revision: Union[str, Sequence[str], None] = "add_member_daily_company_reports"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    table_names = set(inspector.get_table_names())

    if "chat_sessions" not in table_names:
        return

    chat_session_columns = {col["name"] for col in inspector.get_columns("chat_sessions")}
    if "is_primary" not in chat_session_columns:
        op.add_column(
            "chat_sessions",
            sa.Column("is_primary", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        )
    if "last_read_at_by_user" not in chat_session_columns:
        op.add_column(
            "chat_sessions",
            sa.Column("last_read_at_by_user", sa.DateTime(timezone=True), nullable=True),
        )

    # Existing sessions should not suddenly light up as unread after upgrade.
    op.execute(
        """
        UPDATE chat_sessions
        SET last_read_at_by_user = COALESCE(last_message_at, created_at)
        WHERE last_read_at_by_user IS NULL
        """
    )

    # Elect a primary platform session for each (agent, user) pair based on the most useful
    # historical thread: prefer sessions with user messages, then most recently active.
    op.execute(
        """
        WITH message_stats AS (
            SELECT
                conversation_id,
                SUM(CASE WHEN role = 'user' THEN 1 ELSE 0 END) AS user_msg_count
            FROM chat_messages
            GROUP BY conversation_id
        ),
        ranked_sessions AS (
            SELECT
                cs.id,
                ROW_NUMBER() OVER (
                    PARTITION BY cs.agent_id, cs.user_id
                    ORDER BY
                        CASE WHEN COALESCE(ms.user_msg_count, 0) > 0 THEN 0 ELSE 1 END,
                        COALESCE(cs.last_message_at, cs.created_at) DESC,
                        cs.created_at DESC
                ) AS rn
            FROM chat_sessions cs
            LEFT JOIN message_stats ms
                ON ms.conversation_id = cs.id::text
            WHERE cs.source_channel = 'web'
              AND COALESCE(cs.is_group, false) = false
        )
        UPDATE chat_sessions cs
        SET is_primary = true
        FROM ranked_sessions rs
        WHERE cs.id = rs.id
          AND rs.rn = 1
        """
    )

    chat_session_indexes = {idx["name"] for idx in inspector.get_indexes("chat_sessions")}
    if "uq_chat_sessions_primary_platform" not in chat_session_indexes:
        op.create_index(
            "uq_chat_sessions_primary_platform",
            "chat_sessions",
            ["agent_id", "user_id"],
            unique=True,
            postgresql_where=sa.text("is_primary = true AND source_channel = 'web' AND is_group = false"),
        )
    if "ix_chat_sessions_is_primary" not in chat_session_indexes:
        op.create_index("ix_chat_sessions_is_primary", "chat_sessions", ["is_primary"])


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    table_names = set(inspector.get_table_names())

    if "chat_sessions" not in table_names:
        return

    chat_session_indexes = {idx["name"] for idx in inspector.get_indexes("chat_sessions")}
    if "ix_chat_sessions_is_primary" in chat_session_indexes:
        op.drop_index("ix_chat_sessions_is_primary", table_name="chat_sessions")
    if "uq_chat_sessions_primary_platform" in chat_session_indexes:
        op.drop_index("uq_chat_sessions_primary_platform", table_name="chat_sessions")

    chat_session_columns = {col["name"] for col in inspector.get_columns("chat_sessions")}
    if "last_read_at_by_user" in chat_session_columns:
        op.drop_column("chat_sessions", "last_read_at_by_user")
    if "is_primary" in chat_session_columns:
        op.drop_column("chat_sessions", "is_primary")
