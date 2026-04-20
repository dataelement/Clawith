"""Add participants table, extend chat_sessions and chat_messages, migrate messages data, drop messages table."""

import uuid
import sqlalchemy as sa
from alembic import op

revision = "add_participants"
down_revision = "add_invitation_codes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    user_columns = {
        row[0]
        for row in conn.execute(sa.text("""
            SELECT column_name
            FROM information_schema.columns
            WHERE table_name = 'users'
        """)).fetchall()
    }
    has_username = "username" in user_columns

    # 1. Create participants table (idempotent)
    conn.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS participants (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            type VARCHAR(10) NOT NULL,
            ref_id UUID NOT NULL,
            display_name VARCHAR(100) NOT NULL,
            avatar_url VARCHAR(500),
            created_at TIMESTAMP WITH TIME ZONE DEFAULT now(),
            CONSTRAINT uq_participants_type_ref UNIQUE (type, ref_id)
        )
    """))
    conn.execute(sa.text("""
        CREATE INDEX IF NOT EXISTS ix_participants_ref_id ON participants (ref_id)
    """))

    # 2. Backfill: create Participant for every existing User
    # New schema: username lives in identities table; old schema: username was on users directly
    has_identities = conn.execute(sa.text(
        "SELECT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'identities')"
    )).scalar()

    if has_identities:
        conn.execute(sa.text("""
            INSERT INTO participants (id, type, ref_id, display_name, avatar_url)
            SELECT gen_random_uuid(), 'user', u.id, COALESCE(u.display_name, i.username, 'User'), u.avatar_url
            FROM users u
            LEFT JOIN identities i ON u.identity_id = i.id
            ON CONFLICT DO NOTHING
        """))
    elif has_username:
        conn.execute(sa.text("""
            INSERT INTO participants (id, type, ref_id, display_name, avatar_url)
            SELECT gen_random_uuid(), 'user', id, COALESCE(display_name, username, 'User'), avatar_url
            FROM users
            ON CONFLICT DO NOTHING
        """))
    else:
        conn.execute(sa.text("""
            INSERT INTO participants (id, type, ref_id, display_name, avatar_url)
            SELECT gen_random_uuid(), 'user', id, COALESCE(display_name, 'User'), avatar_url
            FROM users
            ON CONFLICT DO NOTHING
        """))

    # 3. Backfill: create Participant for every existing Agent
    conn.execute(sa.text("""
        INSERT INTO participants (id, type, ref_id, display_name, avatar_url)
        SELECT gen_random_uuid(), 'agent', id, name, avatar_url
        FROM agents
        ON CONFLICT DO NOTHING
    """))

    # 4. Add columns to chat_sessions
    conn.execute(sa.text(
        "ALTER TABLE chat_sessions ADD COLUMN IF NOT EXISTS participant_id UUID REFERENCES participants(id)"
    ))
    conn.execute(sa.text(
        "ALTER TABLE chat_sessions ADD COLUMN IF NOT EXISTS peer_agent_id UUID REFERENCES agents(id)"
    ))
    conn.execute(sa.text(
        "ALTER TABLE chat_sessions ADD COLUMN IF NOT EXISTS external_conv_id VARCHAR(200)"
    ))

    # 5. Add participant_id to chat_messages
    conn.execute(sa.text(
        "ALTER TABLE chat_messages ADD COLUMN IF NOT EXISTS participant_id UUID REFERENCES participants(id)"
    ))

    # 6. Backfill participant_id on chat_sessions from user_id
    conn.execute(sa.text("""
        UPDATE chat_sessions cs
        SET participant_id = p.id
        FROM participants p
        WHERE p.type = 'user' AND p.ref_id = cs.user_id
        AND cs.participant_id IS NULL
    """))

    # 7. Backfill participant_id on chat_messages from user_id
    conn.execute(sa.text("""
        UPDATE chat_messages cm
        SET participant_id = p.id
        FROM participants p
        WHERE p.type = 'user' AND p.ref_id = cm.user_id
        AND cm.participant_id IS NULL
    """))

    # 8. Migrate agent-to-agent messages from `messages` table into chat_sessions + chat_messages
    # Only if messages table exists (it may not on fresh installs)
    has_messages = conn.execute(sa.text(
        "SELECT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'messages')"
    )).scalar()

    if has_messages:
        # Get distinct agent-to-agent conversation pairs
        pairs = conn.execute(sa.text("""
            SELECT DISTINCT
                LEAST(sender_id, receiver_id) AS agent_a,
                GREATEST(sender_id, receiver_id) AS agent_b
            FROM messages
            WHERE sender_type = 'agent' AND receiver_type = 'agent'
        """)).fetchall()

        for agent_a, agent_b in pairs:
            session_id = str(uuid.uuid4())

            # Get the sender participant (agent_a)
            part_a = conn.execute(sa.text(
                "SELECT id FROM participants WHERE type = 'agent' AND ref_id = :ref"
            ), {"ref": str(agent_a)}).scalar()

            # Get time bounds
            times = conn.execute(sa.text("""
                SELECT MIN(created_at), MAX(created_at) FROM messages
                WHERE sender_type = 'agent' AND receiver_type = 'agent'
                AND ((sender_id = :a AND receiver_id = :b) OR (sender_id = :b AND receiver_id = :a))
            """), {"a": str(agent_a), "b": str(agent_b)}).fetchone()

            # Create ChatSession (agent_a is the session owner, agent_b is peer)
            # user_id = creator of agent_a (for backward compatibility)
            creator_id = conn.execute(sa.text(
                "SELECT creator_id FROM agents WHERE id = :id"
            ), {"id": str(agent_a)}).scalar()

            conn.execute(sa.text("""
                INSERT INTO chat_sessions (id, agent_id, user_id, title, source_channel, participant_id, peer_agent_id, created_at, last_message_at)
                VALUES (:id, :agent_id, :user_id, :title, 'agent', :participant_id, :peer_agent_id, :created_at, :last_msg_at)
            """), {
                "id": session_id,
                "agent_id": agent_a,
                "user_id": str(creator_id) if creator_id else str(agent_a),
                "title": "Agent Conversation",
                "participant_id": str(part_a) if part_a else None,
                "peer_agent_id": str(agent_b),
                "created_at": times[0],
                "last_msg_at": times[1],
            })

            # Copy messages into chat_messages
            msgs = conn.execute(sa.text("""
                SELECT sender_id, content, created_at FROM messages
                WHERE sender_type = 'agent' AND receiver_type = 'agent'
                AND ((sender_id = :a AND receiver_id = :b) OR (sender_id = :b AND receiver_id = :a))
                ORDER BY created_at
            """), {"a": str(agent_a), "b": str(agent_b)}).fetchall()

            for sender_id, content, created_at in msgs:
                sender_part = conn.execute(sa.text(
                    "SELECT id FROM participants WHERE type = 'agent' AND ref_id = :ref"
                ), {"ref": str(sender_id)}).scalar()

                conn.execute(sa.text("""
                    INSERT INTO chat_messages (id, agent_id, user_id, role, content, conversation_id, participant_id, created_at)
                    VALUES (:id, :agent_id, :user_id, :role, :content, :conv_id, :part_id, :created_at)
                """), {
                    "id": str(uuid.uuid4()),
                    "agent_id": str(agent_a),
                    "user_id": str(creator_id) if creator_id else str(agent_a),
                    "role": "assistant" if str(sender_id) == str(agent_a) else "user",
                    "content": content,
                    "conv_id": session_id,
                    "part_id": str(sender_part) if sender_part else None,
                    "created_at": created_at,
                })

        # 9. Drop messages table
        op.drop_table("messages")

        # Drop the enum types used by messages table
        try:
            conn.execute(sa.text("DROP TYPE IF EXISTS msg_participant_type_enum"))
            conn.execute(sa.text("DROP TYPE IF EXISTS msg_type_enum"))
        except Exception:
            pass


def downgrade() -> None:
    # Remove new columns
    op.drop_column("chat_messages", "participant_id")
    op.drop_column("chat_sessions", "peer_agent_id")
    op.drop_column("chat_sessions", "participant_id")
    op.drop_table("participants")
