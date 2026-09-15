"""Public Agent core contracts."""

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy.exc import IntegrityError

from app.infrastructure.errors import AccessDenied, Conflict, InvalidInput, NotFound
from app.infrastructure.transactions import TransactionContext
from app.modules.agent.models import AgentRecord
from app.modules.agent.repository import AgentRepository
from app.modules.identity_tenant.public import TenantPrincipal, require_admin
from app.modules.model.public import ModelService

MAX_PAGE_SIZE = 100
MAX_PERMISSION_AGENT_SCAN = 1001


class _UnsetType:
    __slots__ = ()


_UNSET = _UnsetType()


@dataclass(frozen=True, slots=True)
class AgentView:
    id: UUID
    tenant_id: UUID
    model_id: UUID
    name: str
    avatar: str | None
    description: str | None
    greeting: str | None
    soul: str
    timezone: str
    enabled: bool
    archived_at: datetime | None
    created_by_membership_id: UUID
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class AgentMetadataView:
    """Secret-free Agent identity exposed to other owners."""

    id: UUID
    tenant_id: UUID
    model_id: UUID
    name: str
    enabled: bool
    archived_at: datetime | None


class AgentService:
    """Manage Agent core records inside a caller-owned transaction."""

    def __init__(self, transaction: TransactionContext) -> None:
        self._repository = AgentRepository(transaction.session)
        self._models = ModelService(transaction)

    async def create(
        self,
        principal: TenantPrincipal,
        *,
        name: str,
        soul: str,
        timezone: str,
        model_id: UUID | None = None,
        agent_id: UUID | None = None,
        avatar: str | None = None,
        description: str | None = None,
        greeting: str | None = None,
        enabled: bool = True,
    ) -> AgentView:
        require_admin(principal)
        model = await self._models.resolve_for_agent_creation(principal, model_id=model_id)
        now = datetime.now(UTC)
        record = AgentRecord(
            id=agent_id or uuid4(),
            tenant_id=principal.tenant_id,
            model_id=model.id,
            created_by_membership_id=principal.membership_id,
            name=_required_text(name, field_name="name", max_length=200),
            avatar=_optional_text(avatar, field_name="avatar", max_length=2048),
            description=_optional_text(description, field_name="description", max_length=20_000),
            greeting=_optional_text(greeting, field_name="greeting", max_length=20_000),
            soul=_required_text(soul, field_name="soul", max_length=100_000),
            timezone=_timezone(timezone),
            enabled=enabled,
            archived_at=None,
            created_at=now,
            updated_at=now,
        )
        self._repository.add(record)
        await self._flush_or_conflict("Agent conflicts with existing data")
        return _view(record)

    async def get(self, principal: TenantPrincipal, *, agent_id: UUID) -> AgentView:
        require_admin(principal)
        return _view(await self._require(principal.tenant_id, agent_id))

    async def get_for_execution(self, principal: TenantPrincipal, *, agent_id: UUID) -> AgentView:
        """Read execution configuration within already captured human authorization."""
        if not principal.can_manage_all_agents and agent_id not in principal.allowed_agent_ids:
            raise AccessDenied("Agent access is denied")
        record = await self._require(principal.tenant_id, agent_id)
        if not record.enabled or record.archived_at is not None:
            raise NotFound("Executing Agent is unavailable")
        return _view(record)

    async def get_for_agent_execution(self, *, tenant_id: UUID, agent_id: UUID) -> AgentView:
        """Trusted autonomous intake resolves its selected Agent without fabricating a human Principal."""
        record = await self._require(tenant_id, agent_id)
        if not record.enabled or record.archived_at is not None:
            raise NotFound("Executing Agent is unavailable")
        return _view(record)

    async def list(
        self, principal: TenantPrincipal, *, limit: int = MAX_PAGE_SIZE, offset: int = 0
    ) -> tuple[AgentView, ...]:
        require_admin(principal)
        _page(limit=limit, offset=offset, maximum=MAX_PAGE_SIZE)
        records = await self._repository.list(principal.tenant_id, limit=limit, offset=offset)
        return tuple(_view(record) for record in records)

    async def update(
        self,
        principal: TenantPrincipal,
        *,
        agent_id: UUID,
        name: str | None = None,
        soul: str | None = None,
        timezone: str | None = None,
        model_id: UUID | None = None,
        avatar: str | None | _UnsetType = _UNSET,
        description: str | None | _UnsetType = _UNSET,
        greeting: str | None | _UnsetType = _UNSET,
    ) -> AgentView:
        require_admin(principal)
        if all(value is None for value in (name, soul, timezone, model_id)) and all(
            isinstance(value, _UnsetType) for value in (avatar, description, greeting)
        ):
            raise InvalidInput("at least one Agent field must be provided")
        record = await self._require(principal.tenant_id, agent_id)
        next_name = _required_text(name, field_name="name", max_length=200) if name is not None else record.name
        next_soul = _required_text(soul, field_name="soul", max_length=100_000) if soul is not None else record.soul
        next_timezone = _timezone(timezone) if timezone is not None else record.timezone
        next_model_id = (
            (await self._models.resolve_for_agent_creation(principal, model_id=model_id)).id
            if model_id is not None
            else record.model_id
        )
        next_avatar = (
            record.avatar
            if isinstance(avatar, _UnsetType)
            else _optional_text(avatar, field_name="avatar", max_length=2048)
        )
        next_description = (
            record.description
            if isinstance(description, _UnsetType)
            else _optional_text(description, field_name="description", max_length=20_000)
        )
        next_greeting = (
            record.greeting
            if isinstance(greeting, _UnsetType)
            else _optional_text(greeting, field_name="greeting", max_length=20_000)
        )
        record.name = next_name
        record.soul = next_soul
        record.timezone = next_timezone
        record.model_id = next_model_id
        record.avatar = next_avatar
        record.description = next_description
        record.greeting = next_greeting
        record.updated_at = datetime.now(UTC)
        await self._flush_or_conflict("Agent update conflicts with existing data")
        return _view(record)

    async def set_enabled(self, principal: TenantPrincipal, *, agent_id: UUID, enabled: bool) -> AgentView:
        require_admin(principal)
        record = await self._require(principal.tenant_id, agent_id)
        if record.archived_at is not None and enabled:
            raise InvalidInput("an archived Agent cannot be enabled")
        record.enabled = enabled
        record.updated_at = datetime.now(UTC)
        await self._repository.flush()
        return _view(record)

    async def archive(self, principal: TenantPrincipal, *, agent_id: UUID) -> AgentView:
        require_admin(principal)
        record = await self._require(principal.tenant_id, agent_id)
        if record.archived_at is None:
            now = datetime.now(UTC)
            record.archived_at = now
            record.enabled = False
            record.updated_at = now
            await self._repository.flush()
        return _view(record)

    async def get_metadata(self, *, tenant_id: UUID, agent_id: UUID) -> AgentMetadataView:
        """Read one explicitly Tenant-scoped Agent for another owner."""
        return _metadata(await self._require(tenant_id, agent_id))

    async def list_active_metadata(
        self, *, tenant_id: UUID, limit: int = MAX_PERMISSION_AGENT_SCAN, offset: int = 0
    ) -> tuple[AgentMetadataView, ...]:
        """Read bounded active Agent metadata for Permission scope resolution."""
        _page(limit=limit, offset=offset, maximum=MAX_PERMISSION_AGENT_SCAN)
        records = await self._repository.list(tenant_id, limit=limit, offset=offset, active_only=True)
        return tuple(_metadata(record) for record in records)

    async def list_visible_metadata(self, principal: TenantPrincipal, *, limit: int = 100,
            offset: int = 0) -> tuple[AgentMetadataView, ...]:
        """Paginate active identities within the caller's captured Agent access."""
        _page(limit=limit, offset=offset, maximum=MAX_PAGE_SIZE)
        records = await self._repository.list(principal.tenant_id, limit=limit, offset=offset, active_only=True,
            allowed_ids=None if principal.can_manage_all_agents else principal.allowed_agent_ids)
        return tuple(_metadata(record) for record in records)

    async def filter_active_ids(self, *, tenant_id: UUID, agent_ids: tuple[UUID, ...]) -> frozenset[UUID]:
        """Filter one bounded Permission batch through Agent-owned state."""
        if len(agent_ids) > MAX_PERMISSION_AGENT_SCAN:
            raise InvalidInput(f"Agent metadata batch exceeds {MAX_PERMISSION_AGENT_SCAN} identities")
        records = await self._repository.list_by_ids(tenant_id, agent_ids, active_only=True)
        return frozenset(record.id for record in records)

    async def require_execution_ids(self, principal: TenantPrincipal, *, agent_ids: tuple[UUID, ...]) -> None:
        """Validate one bounded multi-target intake without repeating Agent queries."""
        if len(agent_ids) > MAX_PERMISSION_AGENT_SCAN:
            raise InvalidInput("Agent execution batch exceeds its bound")
        requested = frozenset(agent_ids)
        if not principal.can_manage_all_agents and not requested <= principal.allowed_agent_ids:
            raise AccessDenied("Agent access is denied")
        if await self.filter_active_ids(tenant_id=principal.tenant_id, agent_ids=agent_ids) != requested:
            raise NotFound("Executing Agent is unavailable")

    async def _require(self, tenant_id: UUID, agent_id: UUID) -> AgentRecord:
        record = await self._repository.get(tenant_id, agent_id)
        if record is None:
            raise NotFound("Agent does not exist in this Tenant")
        return record

    async def _flush_or_conflict(self, message: str) -> None:
        try:
            await self._repository.flush()
        except IntegrityError:
            raise Conflict(message) from None


def _required_text(value: str, *, field_name: str, max_length: int) -> str:
    normalized = value.strip()
    if not normalized or len(normalized) > max_length:
        raise InvalidInput(f"{field_name} must contain 1 to {max_length} characters")
    return normalized


def _optional_text(value: str | None, *, field_name: str, max_length: int) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    if not normalized or len(normalized) > max_length:
        raise InvalidInput(f"{field_name} must contain 1 to {max_length} characters")
    return normalized


def _timezone(value: str) -> str:
    normalized = _required_text(value, field_name="timezone", max_length=64)
    try:
        ZoneInfo(normalized)
    except (ZoneInfoNotFoundError, ValueError):
        raise InvalidInput("timezone must be a valid IANA timezone") from None
    return normalized


def _page(*, limit: int, offset: int, maximum: int) -> None:
    if not 1 <= limit <= maximum:
        raise InvalidInput(f"limit must be between 1 and {maximum}")
    if offset < 0:
        raise InvalidInput("offset must be non-negative")


def _view(record: AgentRecord) -> AgentView:
    return AgentView(
        id=record.id,
        tenant_id=record.tenant_id,
        model_id=record.model_id,
        name=record.name,
        avatar=record.avatar,
        description=record.description,
        greeting=record.greeting,
        soul=record.soul,
        timezone=record.timezone,
        enabled=record.enabled,
        archived_at=record.archived_at,
        created_by_membership_id=record.created_by_membership_id,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


def _metadata(record: AgentRecord) -> AgentMetadataView:
    return AgentMetadataView(
        id=record.id,
        tenant_id=record.tenant_id,
        model_id=record.model_id,
        name=record.name,
        enabled=record.enabled,
        archived_at=record.archived_at,
    )
