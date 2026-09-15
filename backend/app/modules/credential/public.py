"""Public Credential metadata and bounded Secret access contracts."""

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal, cast
from uuid import UUID, uuid4

from sqlalchemy.exc import IntegrityError

from app.infrastructure.errors import AccessDenied, Conflict, InvalidInput, NotFound
from app.infrastructure.transactions import TransactionContext
from app.modules.credential.crypto import CredentialKeyring, Secret
from app.modules.credential.models import CredentialRecord
from app.modules.credential.repository import CredentialRepository
from app.modules.identity_tenant.public import (
    TenantPrincipal,
    require_admin,
    require_same_tenant,
)

CredentialOwnerKind = Literal["tenant", "membership", "agent"]
MAX_PAGE_SIZE = 100


@dataclass(frozen=True, slots=True)
class CredentialMetadataView:
    id: UUID
    tenant_id: UUID
    owner_kind: CredentialOwnerKind
    owner_id: UUID
    kind: str
    provider: str
    label: str
    payload_version: int
    key_version: str
    expires_at: datetime | None
    revoked_at: datetime | None
    created_at: datetime
    updated_at: datetime


class CredentialService:
    """Manage Credential metadata; reveal Secret only at an explicit owner boundary."""

    def __init__(self, transaction: TransactionContext, keyring: CredentialKeyring | None = None) -> None:
        self._repository = CredentialRepository(transaction.session)
        self._keyring = keyring

    async def create(
        self,
        principal: TenantPrincipal,
        *,
        kind: str,
        provider: str,
        label: str,
        secret: Secret,
        owner_kind: CredentialOwnerKind,
        owner_id: UUID | None = None,
        credential_id: UUID | None = None,
        expires_at: datetime | None = None,
    ) -> CredentialMetadataView:
        keyring = self._require_keyring()
        selected_id = credential_id or uuid4()
        membership_owner_id, agent_owner_id = _authorize_owner(principal, owner_kind, owner_id)
        now = datetime.now(UTC)
        _validate_expiry(expires_at, now=now)
        encrypted, payload_version, key_version = keyring.encrypt(
            credential_id=selected_id, tenant_id=principal.tenant_id, secret=secret
        )
        record = CredentialRecord(
            id=selected_id,
            tenant_id=principal.tenant_id,
            membership_owner_id=membership_owner_id,
            agent_owner_id=agent_owner_id,
            kind=_required_text(kind, "kind", 64),
            provider=_required_text(provider, "provider", 128),
            label=_required_text(label, "label", 200),
            encrypted_payload=encrypted,
            payload_version=payload_version,
            key_version=key_version,
            expires_at=expires_at,
            revoked_at=None,
            created_at=now,
            updated_at=now,
        )
        self._repository.add(record)
        await self._flush_or_conflict()
        return _metadata(record)

    async def get_metadata(
        self, principal: TenantPrincipal, *, credential_id: UUID
    ) -> CredentialMetadataView:
        record = await self._require_record(principal, credential_id)
        _require_metadata_access(principal, record)
        return _metadata(record)

    async def list_metadata(
        self, principal: TenantPrincipal, *, limit: int = MAX_PAGE_SIZE, offset: int = 0
    ) -> tuple[CredentialMetadataView, ...]:
        _validate_page(limit, offset)
        records = await self._repository.list_accessible(
            principal.tenant_id,
            membership_id=principal.membership_id,
            manage_all=principal.can_manage_all_agents,
            limit=limit,
            offset=offset,
        )
        return tuple(_metadata(record) for record in records)

    async def update_metadata(
        self,
        principal: TenantPrincipal,
        *,
        credential_id: UUID,
        label: str | None = None,
        expires_at: datetime | None = None,
    ) -> CredentialMetadataView:
        if label is None and expires_at is None:
            raise InvalidInput("label or expires_at must be provided")
        record = await self._require_record(principal, credential_id)
        _require_metadata_access(principal, record)
        if label is not None:
            record.label = _required_text(label, "label", 200)
        if expires_at is not None:
            _validate_expiry(expires_at, now=datetime.now(UTC))
            record.expires_at = expires_at
        record.updated_at = datetime.now(UTC)
        await self._repository.flush()
        await self._repository.refresh(record)
        return _metadata(record)

    async def rotate_secret(
        self, principal: TenantPrincipal, *, credential_id: UUID, secret: Secret
    ) -> CredentialMetadataView:
        keyring = self._require_keyring()
        record = await self._require_record(principal, credential_id)
        _require_metadata_access(principal, record)
        if record.revoked_at is not None:
            raise Conflict("revoked Credential cannot be rotated")
        encrypted, payload_version, key_version = keyring.encrypt(
            credential_id=record.id, tenant_id=record.tenant_id, secret=secret
        )
        record.encrypted_payload = encrypted
        record.payload_version = payload_version
        record.key_version = key_version
        record.updated_at = datetime.now(UTC)
        await self._repository.flush()
        await self._repository.refresh(record)
        return _metadata(record)

    async def revoke(
        self, principal: TenantPrincipal, *, credential_id: UUID
    ) -> CredentialMetadataView:
        record = await self._require_record(principal, credential_id)
        _require_metadata_access(principal, record)
        if record.revoked_at is None:
            now = datetime.now(UTC)
            record.revoked_at = now
            record.updated_at = now
            await self._repository.flush()
            await self._repository.refresh(record)
        return _metadata(record)

    async def require_tenant_owned_metadata(
        self, principal: TenantPrincipal, *, credential_id: UUID
    ) -> CredentialMetadataView:
        require_same_tenant(principal, principal.tenant_id)
        record = await self._require_record(principal, credential_id)
        if record.membership_owner_id is not None or record.agent_owner_id is not None:
            raise AccessDenied("Tenant-owned Credential is required")
        _require_available(record)
        return _metadata(record)

    async def reveal_secret_for_owner(
        self,
        *,
        tenant_id: UUID,
        credential_id: UUID,
        owner_kind: CredentialOwnerKind,
        owner_id: UUID,
    ) -> Secret:
        """Reveal only after a capability owner has resolved its authorized binding."""
        keyring = self._require_keyring()
        record = await self._repository.get(tenant_id, credential_id)
        if record is None or record.owner_kind != owner_kind or record.owner_id != owner_id:
            raise NotFound("Credential is unavailable for this owner")
        _require_available(record)
        return keyring.decrypt(
            credential_id=record.id,
            tenant_id=record.tenant_id,
            encrypted_payload=record.encrypted_payload,
            payload_version=record.payload_version,
            key_version=record.key_version,
        )

    async def require_owner_metadata(
        self,
        *,
        tenant_id: UUID,
        credential_id: UUID,
        owner_kind: CredentialOwnerKind,
        owner_id: UUID,
    ) -> CredentialMetadataView:
        """Validate an already authorized capability binding without exposing its Secret."""
        record = await self._repository.get(tenant_id, credential_id)
        if record is None or record.owner_kind != owner_kind or record.owner_id != owner_id:
            raise NotFound("Credential is unavailable for this owner")
        _require_available(record)
        return _metadata(record)

    async def _require_record(self, principal: TenantPrincipal, credential_id: UUID) -> CredentialRecord:
        record = await self._repository.get(principal.tenant_id, credential_id)
        if record is None:
            raise NotFound("Credential does not exist in this Tenant")
        return record

    def _require_keyring(self) -> CredentialKeyring:
        if self._keyring is None:
            raise InvalidInput("Credential keyring is required")
        return self._keyring

    async def _flush_or_conflict(self) -> None:
        try:
            await self._repository.flush()
        except IntegrityError:
            raise Conflict("Credential conflicts with existing data") from None


def _authorize_owner(
    principal: TenantPrincipal, owner_kind: CredentialOwnerKind, owner_id: UUID | None
) -> tuple[UUID | None, UUID | None]:
    if owner_kind == "tenant":
        require_admin(principal)
        if owner_id not in (None, principal.tenant_id):
            raise InvalidInput("Tenant Credential owner is invalid")
        return None, None
    if owner_kind == "membership":
        selected = owner_id or principal.membership_id
        if selected != principal.membership_id:
            require_admin(principal)
        return selected, None
    if owner_kind == "agent":
        if owner_id is None:
            raise InvalidInput("Agent Credential owner is required")
        require_admin(principal)
        return None, owner_id
    raise InvalidInput("Credential owner kind is invalid")


def _has_metadata_access(principal: TenantPrincipal, record: CredentialRecord) -> bool:
    if principal.can_manage_all_agents:
        return True
    return record.membership_owner_id == principal.membership_id


def _require_metadata_access(principal: TenantPrincipal, record: CredentialRecord) -> None:
    if not _has_metadata_access(principal, record):
        raise AccessDenied("Credential access is denied")


def _require_available(record: CredentialRecord) -> None:
    now = datetime.now(UTC)
    if record.revoked_at is not None or (record.expires_at is not None and record.expires_at <= now):
        raise NotFound("Credential is unavailable")


def _metadata(record: CredentialRecord) -> CredentialMetadataView:
    return CredentialMetadataView(
        id=record.id,
        tenant_id=record.tenant_id,
        owner_kind=cast(CredentialOwnerKind, record.owner_kind),
        owner_id=record.owner_id,
        kind=record.kind,
        provider=record.provider,
        label=record.label,
        payload_version=record.payload_version,
        key_version=record.key_version,
        expires_at=record.expires_at,
        revoked_at=record.revoked_at,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


def _required_text(value: str, field_name: str, max_length: int) -> str:
    normalized = value.strip()
    if not normalized or len(normalized) > max_length:
        raise InvalidInput(f"{field_name} is invalid")
    return normalized


def _validate_page(limit: int, offset: int) -> None:
    if limit < 1 or limit > MAX_PAGE_SIZE or offset < 0:
        raise InvalidInput("Credential page is invalid")


def _validate_expiry(expires_at: datetime | None, *, now: datetime) -> None:
    if expires_at is None:
        return
    if expires_at.tzinfo is None or expires_at.utcoffset() is None:
        raise InvalidInput("Credential expiry must be timezone-aware")
    if expires_at <= now:
        raise InvalidInput("Credential expiry must be in the future")
