"""DAO for Agent and AgentPermission models."""

import uuid
from typing import Any
from collections.abc import Sequence
from datetime import datetime, timezone

from sqlalchemy import exists, func, or_, select
from sqlalchemy.orm import selectinload

from app.dao.base import TenantScopedBaseDAO
from app.models.agent import Agent, AgentPermission


class AgentDAO(TenantScopedBaseDAO[Agent]):
    """Tenant-scoped DAO for Agent entities.

    All query methods automatically apply the current tenant_id from ContextVar.
    For platform-admin cross-tenant queries use the parent ``BaseDAO.get()``
    and annotate with ``# arch-guard: allow (platform_admin cross-tenant)``.
    """

    def __init__(self) -> None:
        super().__init__(Agent)

    # ------------------------------------------------------------------
    # Single-record lookups
    # ------------------------------------------------------------------

    async def get_active(self, agent_id: uuid.UUID) -> Agent | None:
        """Fetch a non-deleted agent by ID, scoped to current tenant."""
        tenant_id = self._require_tenant_id()
        async with self.session(readonly=True) as db:
            stmt = (
                select(Agent)
                .where(
                    Agent.id == agent_id,
                    Agent.tenant_id == tenant_id,
                    Agent.deleted_at.is_(None),
                )
            )
            return (await db.execute(stmt)).scalar_one_or_none()

    async def get_with_models(self, agent_id: uuid.UUID) -> Agent | None:
        """Fetch agent with primary and fallback LLM models eagerly loaded."""
        tenant_id = self._require_tenant_id()
        async with self.session(readonly=True) as db:
            stmt = (
                select(Agent)
                .where(
                    Agent.id == agent_id,
                    Agent.tenant_id == tenant_id,
                    Agent.deleted_at.is_(None),
                )
                .options(
                    selectinload(Agent.primary_model),
                    selectinload(Agent.fallback_model),
                )
            )
            return (await db.execute(stmt)).scalar_one_or_none()

    async def get_including_deleted(self, agent_id: uuid.UUID) -> Agent | None:
        """Fetch an agent by ID including soft-deleted records."""
        tenant_id = self._require_tenant_id()
        async with self.session(readonly=True) as db:
            stmt = select(Agent).where(
                Agent.id == agent_id,
                Agent.tenant_id == tenant_id,
            )
            return (await db.execute(stmt)).scalar_one_or_none()

    # ------------------------------------------------------------------
    # List queries
    # ------------------------------------------------------------------

    async def list_active(
        self,
        *,
        skip: int = 0,
        limit: int = 100,
        include_system: bool = True,
    ) -> Sequence[Agent]:
        """List all non-deleted agents in the current tenant."""
        tenant_id = self._require_tenant_id()
        async with self.session(readonly=True) as db:
            stmt = select(Agent).where(
                Agent.tenant_id == tenant_id,
                Agent.deleted_at.is_(None),
            )
            if not include_system:
                stmt = stmt.where(Agent.is_system.is_(False))
            stmt = stmt.order_by(Agent.created_at.desc()).offset(skip).limit(limit)
            return (await db.execute(stmt)).scalars().all()

    async def list_by_ids(
        self, agent_ids: Sequence[uuid.UUID], db: Any = None
    ) -> Sequence[Agent]:
        """Fetch multiple Agents by IDs."""
        if not agent_ids:
            return []
        async with self.session(db=db, readonly=True) as session_db:
            stmt = select(Agent).where(
                Agent.id.in_(agent_ids),
                Agent.deleted_at.is_(None),
            )
            return (await session_db.execute(stmt)).scalars().all()

    async def list_visible(
        self,
        user_id: uuid.UUID,
        user_role: str,
        *,
        skip: int = 0,
        limit: int = 100,
    ) -> Sequence[Agent]:
        """List agents visible to a specific user per access_mode rules.

        - creator always sees their own agents
        - company-mode agents visible to all users in tenant
        - custom-mode: visible to admins or users with explicit permission
        - private: only visible to the creator
        """
        tenant_id = self._require_tenant_id()
        is_admin = user_role in ("platform_admin", "org_admin")

        async with self.session(readonly=True) as db:
            visible_conditions = [
                Agent.creator_id == user_id,
                Agent.access_mode == "company",
            ]
            if is_admin:
                visible_conditions.append(Agent.access_mode == "custom")
            else:
                visible_conditions.append(
                    exists().where(
                        AgentPermission.agent_id == Agent.id,
                        AgentPermission.scope_type == "user",
                        AgentPermission.scope_id == user_id,
                        AgentPermission.access_level.in_(["use", "manage"]),
                    )
                )
            stmt = (
                select(Agent)
                .where(
                    Agent.tenant_id == tenant_id,
                    Agent.deleted_at.is_(None),
                    or_(*visible_conditions),
                )
                .order_by(Agent.created_at.desc())
                .offset(skip)
                .limit(limit)
            )
            return (await db.execute(stmt)).scalars().all()

    async def count_active(self) -> int:
        """Count non-deleted agents in the current tenant."""
        tenant_id = self._require_tenant_id()
        async with self.session(readonly=True) as db:
            result = await db.execute(
                select(func.count()).where(
                    Agent.tenant_id == tenant_id,
                    Agent.deleted_at.is_(None),
                )
            )
            return result.scalar_one()

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------

    async def soft_delete(self, agent_id: uuid.UUID) -> Agent | None:
        """Soft-delete an agent (set deleted_at), scoped to current tenant."""
        tenant_id = self._require_tenant_id()
        async with self.session() as db:
            stmt = select(Agent).where(
                Agent.id == agent_id,
                Agent.tenant_id == tenant_id,
                Agent.deleted_at.is_(None),
            )
            agent = (await db.execute(stmt)).scalar_one_or_none()
            if agent:
                agent.deleted_at = datetime.now(timezone.utc)
                await db.flush()
            return agent

    async def update_last_active(self, agent_id: uuid.UUID) -> None:
        """Refresh last_active_at timestamp for an agent in the current tenant."""
        tenant_id = self._require_tenant_id()
        async with self.session() as db:
            stmt = select(Agent).where(
                Agent.id == agent_id,
                Agent.tenant_id == tenant_id,
            )
            agent = (await db.execute(stmt)).scalar_one_or_none()
            if agent:
                agent.last_active_at = datetime.now(timezone.utc)
                await db.flush()

    # ------------------------------------------------------------------
    # AgentPermission sub-queries
    # ------------------------------------------------------------------

    async def get_user_permission(
        self, agent_id: uuid.UUID, user_id: uuid.UUID
    ) -> AgentPermission | None:
        """Return the explicit AgentPermission row for a user, if any."""
        async with self.session(readonly=True) as db:
            stmt = select(AgentPermission).where(
                AgentPermission.agent_id == agent_id,
                AgentPermission.scope_type == "user",
                AgentPermission.scope_id == user_id,
            ).limit(1)
            return (await db.execute(stmt)).scalar_one_or_none()

    async def list_permissions(self, agent_id: uuid.UUID) -> Sequence[AgentPermission]:
        """Return all permissions for a given agent."""
        async with self.session(readonly=True) as db:
            stmt = select(AgentPermission).where(AgentPermission.agent_id == agent_id)
            return (await db.execute(stmt)).scalars().all()

    async def upsert_permission(
        self,
        *,
        agent_id: uuid.UUID,
        scope_type: str,
        scope_id: uuid.UUID | None,
        access_level: str,
    ) -> AgentPermission:
        """Create or update an AgentPermission row (upsert by natural key)."""
        async with self.session() as db:
            stmt = select(AgentPermission).where(
                AgentPermission.agent_id == agent_id,
                AgentPermission.scope_type == scope_type,
                AgentPermission.scope_id == scope_id,
            ).limit(1)
            perm = (await db.execute(stmt)).scalar_one_or_none()
            if perm is None:
                perm = AgentPermission(
                    agent_id=agent_id,
                    scope_type=scope_type,
                    scope_id=scope_id,
                    access_level=access_level,
                )
                db.add(perm)
            else:
                perm.access_level = access_level
            await db.flush()
            return perm

    async def delete_permission(
        self, agent_id: uuid.UUID, scope_type: str, scope_id: uuid.UUID | None
    ) -> bool:
        """Delete an explicit permission row. Returns True if a row was removed."""
        async with self.session() as db:
            stmt = select(AgentPermission).where(
                AgentPermission.agent_id == agent_id,
                AgentPermission.scope_type == scope_type,
                AgentPermission.scope_id == scope_id,
            ).limit(1)
            perm = (await db.execute(stmt)).scalar_one_or_none()
            if perm:
                await db.delete(perm)
                await db.flush()
                return True
            return False


agent_dao = AgentDAO()
