"""Public Identity and Tenant contracts."""

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Literal, cast
from uuid import UUID, uuid4

from sqlalchemy.exc import IntegrityError

from app.infrastructure.errors import AccessDenied, Conflict, InvalidInput, NotFound
from app.infrastructure.transactions import TransactionContext
from app.modules.identity_tenant.models import AccountRecord, MembershipRecord, TenantRecord
from app.modules.identity_tenant.repository import IdentityTenantRepository

TenantRole = Literal["tenant_admin", "member"]
PlatformRole = Literal["platform_admin"]


@dataclass(frozen=True, slots=True)
class TenantPrincipal:
    """Human authorization captured when a login session is created."""

    account_id: UUID
    membership_id: UUID
    tenant_id: UUID
    role: TenantRole
    allowed_agent_ids: frozenset[UUID] = field(
        default_factory=lambda: frozenset[UUID]()
    )

    @property
    def can_manage_all_agents(self) -> bool:
        return self.role == "tenant_admin"


@dataclass(frozen=True, slots=True)
class PlatformPrincipal:
    """Platform administrator acting against one explicit target Tenant."""

    account_id: UUID
    target_tenant_id: UUID
    platform_role: PlatformRole


@dataclass(frozen=True, slots=True)
class AccountView:
    id: UUID
    enabled: bool
    platform_role: PlatformRole | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class TenantView:
    id: UUID
    name: str
    enabled: bool
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class MembershipView:
    id: UUID
    tenant_id: UUID
    account_id: UUID
    display_name: str
    avatar: str | None
    title: str | None
    role: TenantRole
    enabled: bool
    joined_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class ResolvedIdentity:
    """Enabled login identity facts and their captured initial Principal."""

    principal: TenantPrincipal
    account: AccountView
    tenant: TenantView
    membership: MembershipView


def require_admin(principal: TenantPrincipal | PlatformPrincipal) -> None:
    """Require the administrator role captured in this Principal."""
    if not isinstance(principal, TenantPrincipal) or not principal.can_manage_all_agents:
        raise AccessDenied("tenant administrator access is required")


def require_same_tenant(
    principal: TenantPrincipal | PlatformPrincipal, tenant_id: UUID
) -> None:
    """Require an operation to stay in the Principal's captured Tenant."""
    if not isinstance(principal, TenantPrincipal) or principal.tenant_id != tenant_id:
        raise AccessDenied("cross-Tenant access is denied")


MAX_PAGE_SIZE = 100


class IdentityService:
    """Operate on Identity/Tenant facts inside a caller-owned transaction."""

    def __init__(self, transaction: TransactionContext) -> None:
        self._repository = IdentityTenantRepository(transaction.session)

    async def create_account(
        self,
        *,
        account_id: UUID | None = None,
        enabled: bool = True,
        platform_role: PlatformRole | None = None,
    ) -> AccountView:
        now = datetime.now(UTC)
        record = AccountRecord(
            id=account_id or uuid4(),
            enabled=enabled,
            platform_role=platform_role,
            created_at=now,
            updated_at=now,
        )
        self._repository.add_account(record)
        await self._flush_or_conflict("Account already exists")
        return _account_view(record)

    async def create_tenant(
        self,
        *,
        name: str,
        tenant_id: UUID | None = None,
        enabled: bool = True,
    ) -> TenantView:
        normalized_name = _required_text(name, field_name="name", max_length=200)
        now = datetime.now(UTC)
        record = TenantRecord(
            id=tenant_id or uuid4(),
            name=normalized_name,
            enabled=enabled,
            created_at=now,
            updated_at=now,
        )
        self._repository.add_tenant(record)
        await self._flush_or_conflict("Tenant already exists")
        return _tenant_view(record)

    async def create_membership(
        self,
        *,
        tenant_id: UUID,
        account_id: UUID,
        display_name: str,
        role: TenantRole,
        membership_id: UUID | None = None,
        avatar: str | None = None,
        title: str | None = None,
        enabled: bool = True,
    ) -> MembershipView:
        _validate_tenant_role(role)
        account = await self._repository.get_account(account_id)
        if account is None:
            raise NotFound("Account does not exist")
        tenant = await self._repository.get_tenant(tenant_id)
        if tenant is None:
            raise NotFound("Tenant does not exist")
        now = datetime.now(UTC)
        record = MembershipRecord(
            id=membership_id or uuid4(),
            tenant_id=tenant_id,
            account_id=account_id,
            display_name=_required_text(
                display_name, field_name="display_name", max_length=200
            ),
            avatar=_optional_text(avatar, field_name="avatar", max_length=2048),
            title=_optional_text(title, field_name="title", max_length=200),
            role=role,
            enabled=enabled,
            joined_at=now,
            updated_at=now,
        )
        self._repository.add_membership(record)
        await self._flush_or_conflict(
            "Membership already exists for this Tenant and Account"
        )
        return _membership_view(record)

    async def resolve_identity(
        self, *, account_id: UUID, tenant_id: UUID
    ) -> ResolvedIdentity:
        account = await self._repository.get_account(account_id)
        tenant = await self._repository.get_tenant(tenant_id)
        membership = await self._repository.get_membership_for_account(
            tenant_id, account_id
        )
        if (
            account is None
            or tenant is None
            or membership is None
            or not account.enabled
            or not tenant.enabled
            or not membership.enabled
        ):
            raise AccessDenied("identity is unavailable for login")

        membership_view = _membership_view(membership)
        principal = TenantPrincipal(
            account_id=account.id,
            membership_id=membership.id,
            tenant_id=tenant.id,
            role=membership_view.role,
        )
        return ResolvedIdentity(
            principal=principal,
            account=_account_view(account),
            tenant=_tenant_view(tenant),
            membership=membership_view,
        )

    async def list_memberships(
        self,
        principal: TenantPrincipal,
        *,
        limit: int = MAX_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[MembershipView, ...]:
        require_admin(principal)
        _validate_page(limit=limit, offset=offset)
        records = await self._repository.list_memberships(
            principal.tenant_id, limit=limit, offset=offset
        )
        return tuple(_membership_view(record) for record in records)

    async def require_membership(
        self, *, tenant_id: UUID, membership_id: UUID
    ) -> MembershipView:
        """Return one Membership only when it belongs to the explicit Tenant."""
        record = await self._repository.get_membership(tenant_id, membership_id)
        if record is None:
            raise NotFound("Membership does not exist in this Tenant")
        return _membership_view(record)

    async def update_membership(
        self,
        principal: TenantPrincipal,
        *,
        membership_id: UUID,
        role: TenantRole | None = None,
        enabled: bool | None = None,
    ) -> MembershipView:
        require_admin(principal)
        if role is None and enabled is None:
            raise InvalidInput("role or enabled must be provided")
        if role is not None:
            _validate_tenant_role(role)
        record = await self._repository.get_membership(
            principal.tenant_id, membership_id
        )
        if record is None:
            raise NotFound("Membership does not exist in this Tenant")
        if role is not None:
            record.role = role
        if enabled is not None:
            record.enabled = enabled
        record.updated_at = datetime.now(UTC)
        await self._repository.flush()
        return _membership_view(record)

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


def _optional_text(
    value: str | None, *, field_name: str, max_length: int
) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    if not normalized or len(normalized) > max_length:
        raise InvalidInput(f"{field_name} must contain 1 to {max_length} characters")
    return normalized


def _validate_tenant_role(role: str) -> None:
    if role not in {"tenant_admin", "member"}:
        raise InvalidInput("role must be tenant_admin or member")


def _validate_page(*, limit: int, offset: int) -> None:
    if not 1 <= limit <= MAX_PAGE_SIZE:
        raise InvalidInput(f"limit must be between 1 and {MAX_PAGE_SIZE}")
    if offset < 0:
        raise InvalidInput("offset must be non-negative")


def _account_view(record: AccountRecord) -> AccountView:
    return AccountView(
        id=record.id,
        enabled=record.enabled,
        platform_role=cast(PlatformRole | None, record.platform_role),
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


def _tenant_view(record: TenantRecord) -> TenantView:
    return TenantView(
        id=record.id,
        name=record.name,
        enabled=record.enabled,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


def _membership_view(record: MembershipRecord) -> MembershipView:
    return MembershipView(
        id=record.id,
        tenant_id=record.tenant_id,
        account_id=record.account_id,
        display_name=record.display_name,
        avatar=record.avatar,
        title=record.title,
        role=cast(TenantRole, record.role),
        enabled=record.enabled,
        joined_at=record.joined_at,
        updated_at=record.updated_at,
    )
