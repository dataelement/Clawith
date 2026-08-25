"""DAO for Group, GroupMember models."""

import uuid
from typing import Any
from collections.abc import Sequence
from datetime import datetime, timezone

from sqlalchemy import select

from app.dao.base import TenantScopedBaseDAO
from app.models.group import Group, GroupMember


class GroupDAO(TenantScopedBaseDAO[Group]):
    """Tenant-scoped DAO for Group entities."""

    def __init__(self) -> None:
        super().__init__(Group)

    async def get_active(self, group_id: uuid.UUID, db: Any = None) -> Group | None:
        """Fetch a non-deleted group by ID, scoped to current tenant if present."""
        tenant_id = self._require_tenant_id()
        async with self.session(db=db, readonly=True) as session_db:
            stmt = select(Group).where(
                Group.id == group_id,
                Group.deleted_at.is_(None),
            )
            if tenant_id is not None:
                stmt = stmt.where(Group.tenant_id == tenant_id)
            return (await session_db.execute(stmt)).scalar_one_or_none()

    async def get_member(
        self, group_id: uuid.UUID, participant_id: uuid.UUID, db: Any = None
    ) -> GroupMember | None:
        """Return active membership row for a participant in a group."""
        async with self.session(db=db, readonly=True) as session_db:
            stmt = select(GroupMember).where(
                GroupMember.group_id == group_id,
                GroupMember.participant_id == participant_id,
                GroupMember.removed_at.is_(None),
            ).limit(1)
            return (await session_db.execute(stmt)).scalar_one_or_none()

    async def list_active(
        self, *, skip: int = 0, limit: int = 100
    ) -> Sequence[Group]:
        """List all non-deleted groups in the current tenant."""
        tenant_id = self._require_tenant_id()
        async with self.session(readonly=True) as db:
            stmt = (
                select(Group)
                .where(
                    Group.tenant_id == tenant_id,
                    Group.deleted_at.is_(None),
                )
                .order_by(Group.created_at.desc())
                .offset(skip)
                .limit(limit)
            )
            return (await db.execute(stmt)).scalars().all()

    async def soft_delete(self, group_id: uuid.UUID) -> Group | None:
        """Soft-delete a group (set deleted_at), scoped to current tenant."""
        tenant_id = self._require_tenant_id()
        async with self.session() as db:
            stmt = select(Group).where(
                Group.id == group_id,
                Group.tenant_id == tenant_id,
                Group.deleted_at.is_(None),
            )
            group = (await db.execute(stmt)).scalar_one_or_none()
            if group:
                group.deleted_at = datetime.now(timezone.utc)
                await db.flush()
            return group

    # ------------------------------------------------------------------
    # GroupMember sub-queries
    # ------------------------------------------------------------------

    async def list_members(
        self, group_id: uuid.UUID, *, skip: int = 0, limit: int = 200
    ) -> Sequence[GroupMember]:
        """List all active members in a group."""
        async with self.session(readonly=True) as db:
            stmt = (
                select(GroupMember)
                .where(
                    GroupMember.group_id == group_id,
                    GroupMember.removed_at.is_(None),
                )
                .offset(skip)
                .limit(limit)
            )
            return (await db.execute(stmt)).scalars().all()

    async def add_member(
        self,
        group_id: uuid.UUID,
        participant_id: uuid.UUID,
        role: str = "member",
    ) -> GroupMember:
        """Add a participant to a group (idempotent: re-activates if removed)."""
        async with self.session() as db:
            # Check for existing (possibly removed) membership
            stmt = select(GroupMember).where(
                GroupMember.group_id == group_id,
                GroupMember.participant_id == participant_id,
            ).limit(1)
            existing = (await db.execute(stmt)).scalar_one_or_none()
            if existing:
                existing.removed_at = None
                existing.role = role
                await db.flush()
                return existing
            member = GroupMember(
                group_id=group_id,
                participant_id=participant_id,
                role=role,
            )
            db.add(member)
            await db.flush()
            return member

    async def remove_member(
        self, group_id: uuid.UUID, participant_id: uuid.UUID
    ) -> GroupMember | None:
        """Soft-remove a participant from a group."""
        async with self.session() as db:
            stmt = select(GroupMember).where(
                GroupMember.group_id == group_id,
                GroupMember.participant_id == participant_id,
                GroupMember.removed_at.is_(None),
            ).limit(1)
            member = (await db.execute(stmt)).scalar_one_or_none()
            if member:
                member.removed_at = datetime.now(timezone.utc)
                await db.flush()
            return member

    async def list_groups_for_participant(
        self, participant_id: uuid.UUID, *, skip: int = 0, limit: int = 100
    ) -> Sequence[Group]:
        """List active groups that a participant belongs to (current tenant)."""
        tenant_id = self._require_tenant_id()
        async with self.session(readonly=True) as db:
            stmt = (
                select(Group)
                .join(GroupMember, Group.id == GroupMember.group_id)
                .where(
                    Group.tenant_id == tenant_id,
                    Group.deleted_at.is_(None),
                    GroupMember.participant_id == participant_id,
                    GroupMember.removed_at.is_(None),
                )
                .order_by(Group.created_at.desc())
                .offset(skip)
                .limit(limit)
            )
            return (await db.execute(stmt)).scalars().all()


group_dao = GroupDAO()
