"""Public captured Agent visibility and authorization contracts."""

from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Literal, cast
from uuid import UUID, uuid4

from sqlalchemy.exc import IntegrityError

from app.infrastructure.errors import AccessDenied, Conflict, InvalidInput, NotFound
from app.infrastructure.transactions import TransactionContext
from app.modules.agent.public import AgentMetadataView, AgentService
from app.modules.identity_tenant.public import IdentityService, TenantPrincipal, require_admin
from app.modules.permission.models import AgentVisibilityGrantRecord, AgentVisibilityRecord
from app.modules.permission.repository import PermissionRepository

Visibility = Literal["tenant", "restricted"]
PermissionLevel = Literal["none", "use", "manage"]
MAX_CAPTURED_AGENT_IDS = 1000
VISIBILITY_SCAN_BATCH = 1001
MAX_VISIBILITY_SCAN = 10_000


@dataclass(frozen=True, slots=True)
class AgentVisibilityView:
    agent_id: UUID
    tenant_id: UUID
    visibility: Visibility
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class AgentVisibilityGrantView:
    id: UUID
    tenant_id: UUID
    agent_id: UUID
    membership_id: UUID | None
    source_agent_id: UUID | None
    granted_by_membership_id: UUID
    created_at: datetime
    revoked_at: datetime | None
    updated_at: datetime


class PermissionService:
    """Own Agent visibility policy and capture bounded human authorization."""

    def __init__(self, transaction: TransactionContext) -> None:
        self._repository = PermissionRepository(transaction.session)
        self._agents = AgentService(transaction)
        self._identities = IdentityService(transaction)

    async def set_visibility(
        self,
        principal: TenantPrincipal,
        *,
        agent_id: UUID,
        visibility: Visibility,
    ) -> AgentVisibilityView:
        require_admin(principal)
        if visibility not in {"tenant", "restricted"}:
            raise InvalidInput("visibility must be tenant or restricted")
        await self._agents.get_metadata(tenant_id=principal.tenant_id, agent_id=agent_id)
        record = await self._repository.get_visibility(principal.tenant_id, agent_id)
        now = datetime.now(UTC)
        if record is None:
            record = AgentVisibilityRecord(
                id=uuid4(),
                tenant_id=principal.tenant_id,
                agent_id=agent_id,
                visibility=visibility,
                created_at=now,
                updated_at=now,
            )
            self._repository.add_visibility(record)
        else:
            record.visibility = visibility
            record.updated_at = now
        await self._flush_or_conflict("Agent visibility could not be stored")
        return _visibility_view(record)

    async def get_visibility(self, principal: TenantPrincipal, *, agent_id: UUID) -> AgentVisibilityView:
        require_admin(principal)
        record = await self._repository.get_visibility(principal.tenant_id, agent_id)
        if record is None:
            raise NotFound("Agent visibility is not configured")
        return _visibility_view(record)

    async def grant_membership(
        self,
        principal: TenantPrincipal,
        *,
        agent_id: UUID,
        membership_id: UUID,
    ) -> AgentVisibilityGrantView:
        require_admin(principal)
        await self._agents.get_metadata(tenant_id=principal.tenant_id, agent_id=agent_id)
        await self._identities.require_membership(tenant_id=principal.tenant_id, membership_id=membership_id)
        return await self._grant(
            principal,
            agent_id=agent_id,
            membership_id=membership_id,
            source_agent_id=None,
        )

    async def grant_agent(
        self,
        principal: TenantPrincipal,
        *,
        agent_id: UUID,
        source_agent_id: UUID,
    ) -> AgentVisibilityGrantView:
        require_admin(principal)
        await self._agents.get_metadata(tenant_id=principal.tenant_id, agent_id=agent_id)
        await self._agents.get_metadata(tenant_id=principal.tenant_id, agent_id=source_agent_id)
        return await self._grant(
            principal,
            agent_id=agent_id,
            membership_id=None,
            source_agent_id=source_agent_id,
        )

    async def revoke_membership_grant(
        self,
        principal: TenantPrincipal,
        *,
        agent_id: UUID,
        membership_id: UUID,
    ) -> AgentVisibilityGrantView:
        require_admin(principal)
        grant = await self._repository.get_membership_grant(principal.tenant_id, agent_id, membership_id)
        return await self._revoke(grant)

    async def revoke_agent_grant(
        self,
        principal: TenantPrincipal,
        *,
        agent_id: UUID,
        source_agent_id: UUID,
    ) -> AgentVisibilityGrantView:
        require_admin(principal)
        grant = await self._repository.get_agent_grant(principal.tenant_id, agent_id, source_agent_id)
        return await self._revoke(grant)

    async def freeze_principal(self, principal: TenantPrincipal) -> TenantPrincipal:
        """Capture member Agent visibility once for an Auth login session.

        The representation holds at most 1000 IDs. Scope resolution scans at most
        10000 policy rows and fails explicitly if either bound is exceeded.
        """
        if principal.can_manage_all_agents:
            return replace(principal, allowed_agent_ids=frozenset())
        captured: set[UUID] = set()
        offset = 0
        while offset < MAX_VISIBILITY_SCAN:
            remaining = MAX_VISIBILITY_SCAN - offset
            query_limit = min(VISIBILITY_SCAN_BATCH, remaining + 1)
            candidate_ids = await self._repository.list_member_candidate_ids(
                principal.tenant_id,
                principal.membership_id,
                limit=query_limit,
                offset=offset,
            )
            if not candidate_ids:
                return replace(principal, allowed_agent_ids=frozenset(captured))
            if len(candidate_ids) > remaining:
                raise InvalidInput(f"login Agent visibility policy scan exceeds the {MAX_VISIBILITY_SCAN}-row bound")
            active_ids = await self._agents.filter_active_ids(tenant_id=principal.tenant_id, agent_ids=candidate_ids)
            captured.update(active_ids)
            if len(captured) > MAX_CAPTURED_AGENT_IDS:
                raise InvalidInput(f"login Agent visibility exceeds the {MAX_CAPTURED_AGENT_IDS}-Agent bound")
            offset += len(candidate_ids)
            if len(candidate_ids) < query_limit:
                return replace(principal, allowed_agent_ids=frozenset(captured))
        raise InvalidInput(f"login Agent visibility policy scan exceeds the {MAX_VISIBILITY_SCAN}-row bound")

    async def resolve_principal(self, principal: TenantPrincipal, *, agent_id: UUID) -> PermissionLevel:
        """Resolve only the authorization already captured in a login Principal."""
        await self._agents.get_metadata(tenant_id=principal.tenant_id, agent_id=agent_id)
        if principal.can_manage_all_agents:
            return "manage"
        return "use" if agent_id in principal.allowed_agent_ids else "none"

    async def require_principal_access(self, principal: TenantPrincipal, *, agent_id: UUID) -> AgentMetadataView:
        """Guard human intake with the fixed login-session authorization."""
        if await self.resolve_principal(principal, agent_id=agent_id) == "none":
            raise AccessDenied("Agent access is denied")
        return await self._agents.get_metadata(tenant_id=principal.tenant_id, agent_id=agent_id)

    async def resolve_autonomous(
        self, *, tenant_id: UUID, source_agent_id: UUID, target_agent_id: UUID
    ) -> PermissionLevel:
        """Resolve current Agent-subject visibility at autonomous intake."""
        source = await self._agents.get_metadata(tenant_id=tenant_id, agent_id=source_agent_id)
        target = await self._agents.get_metadata(tenant_id=tenant_id, agent_id=target_agent_id)
        if not source.enabled or source.archived_at is not None or not target.enabled or target.archived_at is not None:
            return "none"
        visibility = await self._repository.get_visibility(tenant_id, target_agent_id)
        if visibility is None:
            return "none"
        if visibility.visibility == "tenant":
            return "use"
        grant = await self._repository.get_agent_grant(tenant_id, target_agent_id, source_agent_id)
        return "use" if grant is not None and grant.revoked_at is None else "none"

    async def require_autonomous_access(
        self, *, tenant_id: UUID, source_agent_id: UUID, target_agent_id: UUID
    ) -> AgentMetadataView:
        """Guard autonomous intake with current Agent-subject policy."""
        if (
            await self.resolve_autonomous(
                tenant_id=tenant_id,
                source_agent_id=source_agent_id,
                target_agent_id=target_agent_id,
            )
            == "none"
        ):
            raise AccessDenied("autonomous Agent access is denied")
        return await self._agents.get_metadata(tenant_id=tenant_id, agent_id=target_agent_id)

    async def _grant(
        self,
        principal: TenantPrincipal,
        *,
        agent_id: UUID,
        membership_id: UUID | None,
        source_agent_id: UUID | None,
    ) -> AgentVisibilityGrantView:
        if membership_id is not None:
            record = await self._repository.get_membership_grant(principal.tenant_id, agent_id, membership_id)
        else:
            assert source_agent_id is not None
            record = await self._repository.get_agent_grant(principal.tenant_id, agent_id, source_agent_id)
        now = datetime.now(UTC)
        if record is None:
            record = AgentVisibilityGrantRecord(
                id=uuid4(),
                tenant_id=principal.tenant_id,
                agent_id=agent_id,
                membership_id=membership_id,
                source_agent_id=source_agent_id,
                granted_by_membership_id=principal.membership_id,
                created_at=now,
                revoked_at=None,
                updated_at=now,
            )
            self._repository.add_grant(record)
        else:
            record.granted_by_membership_id = principal.membership_id
            record.revoked_at = None
            record.updated_at = now
        await self._flush_or_conflict("Agent visibility grant already exists")
        return _grant_view(record)

    async def _revoke(self, grant: AgentVisibilityGrantRecord | None) -> AgentVisibilityGrantView:
        if grant is None:
            raise NotFound("Agent visibility grant does not exist")
        if grant.revoked_at is None:
            now = datetime.now(UTC)
            grant.revoked_at = now
            grant.updated_at = now
            await self._repository.flush()
        return _grant_view(grant)

    async def _flush_or_conflict(self, message: str) -> None:
        try:
            await self._repository.flush()
        except IntegrityError:
            raise Conflict(message) from None


def _visibility_view(record: AgentVisibilityRecord) -> AgentVisibilityView:
    return AgentVisibilityView(
        agent_id=record.agent_id,
        tenant_id=record.tenant_id,
        visibility=cast(Visibility, record.visibility),
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


def _grant_view(record: AgentVisibilityGrantRecord) -> AgentVisibilityGrantView:
    return AgentVisibilityGrantView(
        id=record.id,
        tenant_id=record.tenant_id,
        agent_id=record.agent_id,
        membership_id=record.membership_id,
        source_agent_id=record.source_agent_id,
        granted_by_membership_id=record.granted_by_membership_id,
        created_at=record.created_at,
        revoked_at=record.revoked_at,
        updated_at=record.updated_at,
    )
