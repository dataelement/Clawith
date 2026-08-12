"""Tenant-scoped persistence for ChatMessage rows."""

import uuid
from collections.abc import Sequence

from sqlalchemy import select

from app.dao.base import TenantScopedBaseDAO
from app.models.audit import ChatMessage


class ChatMessageDAO(TenantScopedBaseDAO[ChatMessage]):
    """DAO for ChatMessage entities with automatic tenant write scoping."""

    def __init__(self) -> None:
        super().__init__(ChatMessage)

    async def list_by_conversation(
        self,
        conversation_id: str,
        *,
        agent_id: uuid.UUID | None = None,
        skip: int = 0,
        limit: int = 100,
    ) -> Sequence[ChatMessage]:
        """List messages by conversation_id (optionally filtered by agent_id)."""
        async with self.session(readonly=True) as db:
            stmt = select(ChatMessage).where(
                ChatMessage.conversation_id == conversation_id
            )
            if agent_id is not None:
                stmt = stmt.where(ChatMessage.agent_id == agent_id)
            stmt = stmt.order_by(ChatMessage.created_at.asc()).offset(skip).limit(limit)
            return (await db.execute(stmt)).scalars().all()

    async def list_by_agent(
        self,
        agent_id: uuid.UUID,
        *,
        skip: int = 0,
        limit: int = 100,
    ) -> Sequence[ChatMessage]:
        """List recent messages for an agent (caller must verify agent tenant)."""
        async with self.session(readonly=True) as db:
            stmt = (
                select(ChatMessage)
                .where(ChatMessage.agent_id == agent_id)
                .order_by(ChatMessage.created_at.desc())
                .offset(skip)
                .limit(limit)
            )
            return (await db.execute(stmt)).scalars().all()

    async def get_last_by_conversation(
        self, conversation_id: str
    ) -> ChatMessage | None:
        """Return the most recent message in a conversation."""
        async with self.session(readonly=True) as db:
            stmt = (
                select(ChatMessage)
                .where(ChatMessage.conversation_id == conversation_id)
                .order_by(ChatMessage.created_at.desc())
                .limit(1)
            )
            return (await db.execute(stmt)).scalar_one_or_none()

    async def create_message(
        self,
        *,
        agent_id: uuid.UUID | None,
        user_id: uuid.UUID | None,
        role: str,
        content: str,
        conversation_id: str,
        participant_id: uuid.UUID | None = None,
        thinking: str | None = None,
        mentions: list | None = None,
        tenant_id: uuid.UUID | None = None,
    ) -> ChatMessage:
        """Create a single chat message."""
        async with self.session() as db:
            msg = ChatMessage(
                agent_id=agent_id,
                user_id=user_id,
                role=role,
                content=content,
                conversation_id=conversation_id,
                participant_id=participant_id,
                thinking=thinking,
                mentions=mentions or [],
            )
            self.add_scoped(db, msg, tenant_id=tenant_id)
            await db.flush()
            return msg

    async def bulk_create(self, messages: list[dict]) -> Sequence[ChatMessage]:
        """Insert multiple messages in a single flush."""
        async with self.session() as db:
            objs = [ChatMessage(**m) for m in messages]
            for obj in objs:
                self.add_scoped(db, obj)
            await db.flush()
            return objs


chat_message_dao = ChatMessageDAO()
