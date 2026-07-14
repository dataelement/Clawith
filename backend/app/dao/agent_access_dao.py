"""DAO helpers for agent access control."""

from typing import Any, Sequence

from sqlalchemy import select

from app.dao.base import BaseDAO
from app.models.agent import Agent, AgentPermission
from app.models.org import AgentRelationship, OrgMember
from app.models.user import User


class AgentAccessDAO(BaseDAO[Agent]):
    """Read access patterns used by permission checks."""

    def __init__(self) -> None:
        super().__init__(Agent)

    async def get_agent(self, agent_id: Any) -> Agent | None:
        """Fetch a single agent by id."""
        return await self.get(agent_id)

    async def get_user(self, user_id: Any) -> User | None:
        """Fetch a single user by id."""
        async with self.session(readonly=True) as db:
            result = await db.execute(select(User).where(User.id == user_id))
            return result.scalar_one_or_none()

    async def get_org_member(self, member_id: Any) -> OrgMember | None:
        """Fetch a single organization member by id."""
        async with self.session(readonly=True) as db:
            result = await db.execute(select(OrgMember).where(OrgMember.id == member_id))
            return result.scalar_one_or_none()

    async def list_permissions(self, agent_id: Any) -> Sequence[AgentPermission]:
        """List all permission rows for an agent."""
        async with self.session(readonly=True) as db:
            result = await db.execute(select(AgentPermission).where(AgentPermission.agent_id == agent_id))
            return result.scalars().all()

    async def list_active_user_ids_by_tenant(self, tenant_id: Any) -> list[Any]:
        """Return active user ids in a tenant."""
        async with self.session(readonly=True) as db:
            result = await db.execute(
                select(User.id).where(
                    User.tenant_id == tenant_id,
                    User.is_active == True,  # noqa: E712
                )
            )
            return [row[0] for row in result.fetchall()]

    async def list_custom_permission_user_ids(self, agent_id: Any) -> list[Any]:
        """Return user ids explicitly permitted on an agent."""
        async with self.session(readonly=True) as db:
            result = await db.execute(
                select(AgentPermission.scope_id).where(
                    AgentPermission.agent_id == agent_id,
                    AgentPermission.scope_type == "user",
                    AgentPermission.scope_id.isnot(None),
                )
            )
            return [row[0] for row in result.fetchall() if row[0]]

    async def list_active_admin_user_ids_by_tenant(self, tenant_id: Any) -> list[Any]:
        """Return active tenant admin user ids."""
        async with self.session(readonly=True) as db:
            result = await db.execute(
                select(User.id).where(
                    User.tenant_id == tenant_id,
                    User.is_active == True,  # noqa: E712
                    User.role.in_(["platform_admin", "org_admin"]),
                )
            )
            return [row[0] for row in result.fetchall()]

    async def list_active_relationship_user_ids(
        self,
        *,
        agent_id: Any,
        tenant_id: Any,
        user_ids: set[Any],
    ) -> set[Any]:
        """Return active org-member user ids already linked to an agent."""
        if not user_ids:
            return set()
        async with self.session(readonly=True) as db:
            result = await db.execute(
                select(OrgMember.user_id)
                .join(AgentRelationship, AgentRelationship.member_id == OrgMember.id)
                .where(
                    AgentRelationship.agent_id == agent_id,
                    OrgMember.tenant_id == tenant_id,
                    OrgMember.status == "active",
                    OrgMember.user_id.in_(user_ids),
                )
            )
            return {row[0] for row in result.fetchall() if row[0]}

    async def list_active_users_by_ids(self, *, user_ids: set[Any], tenant_id: Any) -> Sequence[User]:
        """Return active users by ids under one tenant."""
        if not user_ids:
            return []
        async with self.session(readonly=True) as db:
            result = await db.execute(
                select(User).where(
                    User.id.in_(user_ids),
                    User.tenant_id == tenant_id,
                    User.is_active.is_(True),
                )
            )
            return result.scalars().all()


agent_access_dao = AgentAccessDAO()
