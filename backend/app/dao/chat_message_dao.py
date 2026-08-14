"""Tenant-safe DAO for the unified ChatMessage model."""

import uuid
from collections.abc import Sequence

from sqlalchemy import select

from app.dao.base import BaseDAO
from app.models.audit import ChatMessage


class ChatMessageDAO(BaseDAO[ChatMessage]):
    """DAO for ChatMessage entities with explicit tenant ownership."""

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
        tenant_id: uuid.UUID,
        agent_id: uuid.UUID | None,
        user_id: uuid.UUID | None,
        role: str,
        content: str,
        conversation_id: str,
        participant_id: uuid.UUID | None = None,
        thinking: str | None = None,
        mentions: list | None = None,
    ) -> ChatMessage:
        """Create a single chat message."""
        async with self.session() as db:
            msg = ChatMessage(
                tenant_id=tenant_id,
                agent_id=agent_id,
                user_id=user_id,
                role=role,
                content=content,
                conversation_id=conversation_id,
                participant_id=participant_id,
                thinking=thinking,
                mentions=mentions or [],
            )
            db.add(msg)
            await db.flush()
            return msg

    async def bulk_create(self, messages: list[dict]) -> Sequence[ChatMessage]:
        """Insert multiple messages in a single flush."""
        async with self.session() as db:
            missing_tenant = [index for index, message in enumerate(messages) if not message.get("tenant_id")]
            if missing_tenant:
                raise ValueError(
                    "Every ChatMessage requires tenant_id; missing at indexes "
                    + ", ".join(str(index) for index in missing_tenant)
                )
            objs = [
                ChatMessage(
                    tenant_id=message["tenant_id"],
                    **{key: value for key, value in message.items() if key != "tenant_id"},
                )
                for message in messages
            ]
            db.add_all(objs)
            await db.flush()
            return objs


chat_message_dao = ChatMessageDAO()
