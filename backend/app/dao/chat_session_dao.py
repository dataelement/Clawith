"""DAO for ChatSession model."""

import uuid
from typing import Any
from collections.abc import Sequence
from datetime import datetime, timezone

from sqlalchemy import select

from app.dao.base import TenantScopedBaseDAO
from app.models.chat_session import ChatSession


class ChatSessionDAO(TenantScopedBaseDAO[ChatSession]):
    """Tenant-scoped DAO for ChatSession entities."""

    def __init__(self) -> None:
        super().__init__(ChatSession)

    async def get_active(self, session_id: uuid.UUID, db: Any = None) -> ChatSession | None:
        """Fetch a non-deleted session by ID, scoped to current tenant if present."""
        tenant_id = self._require_tenant_id()
        async with self.session(db=db, readonly=True) as session_db:
            stmt = select(ChatSession).where(
                ChatSession.id == session_id,
                ChatSession.deleted_at.is_(None),
            )
            if tenant_id is not None:
                stmt = stmt.where(ChatSession.tenant_id == tenant_id)
            return (await session_db.execute(stmt)).scalar_one_or_none()

    async def get_including_deleted(self, session_id: uuid.UUID, db: Any = None) -> ChatSession | None:
        """Fetch a session by ID including soft-deleted records."""
        tenant_id = self._require_tenant_id()
        async with self.session(db=db, readonly=True) as session_db:
            stmt = select(ChatSession).where(ChatSession.id == session_id)
            if tenant_id is not None:
                stmt = stmt.where(ChatSession.tenant_id == tenant_id)
            return (await session_db.execute(stmt)).scalar_one_or_none()

    async def get_primary_direct(
        self,
        agent_id: uuid.UUID,
        user_id: uuid.UUID,
    ) -> ChatSession | None:
        """Return the primary direct (P2P) session between a user and agent."""
        tenant_id = self._require_tenant_id()
        async with self.session(readonly=True) as db:
            stmt = select(ChatSession).where(
                ChatSession.tenant_id == tenant_id,
                ChatSession.agent_id == agent_id,
                ChatSession.user_id == user_id,
                ChatSession.session_type == "direct",
                ChatSession.is_primary.is_(True),
                ChatSession.deleted_at.is_(None),
            ).limit(1)
            return (await db.execute(stmt)).scalar_one_or_none()

    async def get_or_create_primary_direct(
        self,
        agent_id: uuid.UUID,
        user_id: uuid.UUID,
        *,
        source_channel: str = "web",
    ) -> tuple[ChatSession, bool]:
        """Find or create the primary direct session; returns (session, created)."""
        tenant_id = self._require_tenant_id()
        existing = await self.get_primary_direct(agent_id, user_id)
        if existing:
            return existing, False

        async with self.session() as db:
            session = ChatSession(
                tenant_id=tenant_id,
                agent_id=agent_id,
                user_id=user_id,
                session_type="direct",
                is_primary=True,
                source_channel=source_channel,
            )
            db.add(session)
            await db.flush()
            return session, True

    async def find_by_external_conv_id(
        self, agent_id: uuid.UUID, external_conv_id: str
    ) -> ChatSession | None:
        """Find a session by its external IM platform conversation ID."""
        async with self.session(readonly=True) as db:
            stmt = select(ChatSession).where(
                ChatSession.agent_id == agent_id,
                ChatSession.external_conv_id == external_conv_id,
                ChatSession.deleted_at.is_(None),
            ).limit(1)
            return (await db.execute(stmt)).scalar_one_or_none()

    async def list_by_agent(
        self,
        agent_id: uuid.UUID,
        *,
        user_id: uuid.UUID | None = None,
        skip: int = 0,
        limit: int = 50,
    ) -> Sequence[ChatSession]:
        """List non-deleted sessions for an agent, optionally filtered by user."""
        tenant_id = self._require_tenant_id()
        async with self.session(readonly=True) as db:
            stmt = select(ChatSession).where(
                ChatSession.tenant_id == tenant_id,
                ChatSession.agent_id == agent_id,
                ChatSession.deleted_at.is_(None),
            )
            if user_id is not None:
                stmt = stmt.where(ChatSession.user_id == user_id)
            stmt = stmt.order_by(ChatSession.updated_at.desc()).offset(skip).limit(limit)
            return (await db.execute(stmt)).scalars().all()

    async def list_by_group(
        self,
        group_id: uuid.UUID,
        *,
        skip: int = 0,
        limit: int = 50,
    ) -> Sequence[ChatSession]:
        """List non-deleted group sessions for a given group."""
        tenant_id = self._require_tenant_id()
        async with self.session(readonly=True) as db:
            stmt = (
                select(ChatSession)
                .where(
                    ChatSession.tenant_id == tenant_id,
                    ChatSession.group_id == group_id,
                    ChatSession.session_type == "group",
                    ChatSession.deleted_at.is_(None),
                )
                .order_by(ChatSession.updated_at.desc())
                .offset(skip)
                .limit(limit)
            )
            return (await db.execute(stmt)).scalars().all()

    async def list_for_user(
        self,
        user_id: uuid.UUID,
        *,
        session_type: str | None = None,
        skip: int = 0,
        limit: int = 50,
    ) -> Sequence[ChatSession]:
        """List non-deleted sessions for a specific user in the current tenant."""
        tenant_id = self._require_tenant_id()
        async with self.session(readonly=True) as db:
            stmt = select(ChatSession).where(
                ChatSession.tenant_id == tenant_id,
                ChatSession.user_id == user_id,
                ChatSession.deleted_at.is_(None),
            )
            if session_type is not None:
                stmt = stmt.where(ChatSession.session_type == session_type)
            stmt = stmt.order_by(ChatSession.updated_at.desc()).offset(skip).limit(limit)
            return (await db.execute(stmt)).scalars().all()

    async def soft_delete(self, session_id: uuid.UUID) -> ChatSession | None:
        """Soft-delete a session (set deleted_at), scoped to current tenant."""
        tenant_id = self._require_tenant_id()
        async with self.session() as db:
            stmt = select(ChatSession).where(
                ChatSession.id == session_id,
                ChatSession.tenant_id == tenant_id,
                ChatSession.deleted_at.is_(None),
            )
            sess = (await db.execute(stmt)).scalar_one_or_none()
            if sess:
                sess.deleted_at = datetime.now(timezone.utc)
                await db.flush()
            return sess

    async def touch_last_message_at(
        self, session_id: uuid.UUID, ts: datetime | None = None
    ) -> None:
        """Update last_message_at timestamp on a session."""
        tenant_id = self._require_tenant_id()
        async with self.session() as db:
            stmt = select(ChatSession).where(
                ChatSession.id == session_id,
                ChatSession.tenant_id == tenant_id,
            )
            sess = (await db.execute(stmt)).scalar_one_or_none()
            if sess:
                sess.last_message_at = ts or datetime.now(timezone.utc)
                await db.flush()


chat_session_dao = ChatSessionDAO()
