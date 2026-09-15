"""Public local-login service with login-scoped authorization snapshots."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.infrastructure.errors import AccessDenied, Conflict, InvalidInput
from app.infrastructure.transactions import transaction
from app.modules.auth.crypto import (
    KDF_NAME,
    KDF_VERSION,
    create_password_verifier,
    issue_token,
    token_digest,
    verify_login_password,
)
from app.modules.auth.models import LoginSessionRecord, LoginVerifierRecord
from app.modules.auth.repository import AuthRepository
from app.modules.identity_tenant.public import IdentityService, TenantPrincipal
from app.modules.permission.public import MAX_CAPTURED_AGENT_IDS, PermissionService

AUTHORIZATION_SCHEMA_VERSION = 1
MAX_LOGIN_NAME_LENGTH = 320


@dataclass(frozen=True, slots=True)
class AuthenticatedSession:
    principal: TenantPrincipal
    expires_at: datetime


class AuthService:
    """Own short login transactions while password KDF work stays outside them."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        session_ttl: timedelta,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if session_ttl <= timedelta(0):
            raise InvalidInput("login session lifetime must be positive")
        self._sessions = session_factory
        self._session_ttl = session_ttl
        self._clock = clock or (lambda: datetime.now(UTC))

    async def provision_trusted_verifier(
        self, *, account_id: UUID, login_name: str, password: str
    ) -> None:
        """Explicit trusted setup path; application startup never calls this implicitly."""
        normalized = _normalize_login_name(login_name)
        password_hash = await create_password_verifier(password)
        now = self._now()
        async with transaction(self._sessions) as tx:
            repository = AuthRepository(tx.session)
            existing = await repository.get_verifier_for_account(account_id)
            if existing is None:
                repository.add_verifier(
                    LoginVerifierRecord(
                        id=uuid4(),
                        account_id=account_id,
                        login_name=normalized,
                        password_hash=password_hash,
                        kdf_name=KDF_NAME,
                        kdf_version=KDF_VERSION,
                        created_at=now,
                        updated_at=now,
                    )
                )
            else:
                existing.login_name = normalized
                existing.password_hash = password_hash
                existing.kdf_name = KDF_NAME
                existing.kdf_version = KDF_VERSION
                existing.updated_at = now
            try:
                await repository.flush()
            except IntegrityError:
                raise Conflict("login verifier conflicts with existing data") from None

    async def login(self, login_name: str, password: str, tenant_id: UUID) -> tuple[str, TenantPrincipal]:
        normalized = _normalize_login_name(login_name)
        async with transaction(self._sessions) as tx:
            verifier = await AuthRepository(tx.session).get_verifier_by_login_name(normalized)
            verifier_snapshot = (
                verifier.account_id,
                verifier.password_hash,
                verifier.kdf_name,
                verifier.kdf_version,
            ) if verifier is not None else None
        if verifier_snapshot is None:
            await create_password_verifier("invalid-login")  # equalize the expensive failure path
            raise AccessDenied("invalid login credentials")
        account_id, password_hash, kdf_name, kdf_version = verifier_snapshot
        if (
            kdf_name != KDF_NAME
            or kdf_version != KDF_VERSION
            or not await verify_login_password(password, password_hash)
        ):
            raise AccessDenied("invalid login credentials")

        token = issue_token()
        now = self._now()
        try:
            async with transaction(self._sessions) as tx:
                await tx.session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ"))
                current = await AuthRepository(tx.session).get_verifier_by_login_name(
                    normalized, for_update=True
                )
                if (
                    current is None
                    or current.account_id != account_id
                    or current.password_hash != password_hash
                    or current.kdf_name != kdf_name
                    or current.kdf_version != kdf_version
                ):
                    raise AccessDenied("invalid login credentials")
                resolved = await IdentityService(tx).resolve_identity(
                    account_id=account_id, tenant_id=tenant_id
                )
                principal = await PermissionService(tx).freeze_principal(resolved.principal)
                repository = AuthRepository(tx.session)
                repository.add_session(
                    LoginSessionRecord(
                        id=uuid4(),
                        tenant_id=principal.tenant_id,
                        account_id=principal.account_id,
                        membership_id=principal.membership_id,
                        token_hash=token_digest(token),
                        frozen_authorization=_encode_authorization(principal),
                        authorization_schema_version=AUTHORIZATION_SCHEMA_VERSION,
                        created_at=now,
                        expires_at=now + self._session_ttl,
                        logged_out_at=None,
                    )
                )
                try:
                    await repository.flush()
                except IntegrityError:
                    raise Conflict("login session could not be created") from None
        except DBAPIError as error:
            if getattr(error.orig, "sqlstate", None) == "40001":
                raise Conflict("login authorization changed during capture") from None
            raise
        return token, principal

    async def authenticate(self, token: str) -> TenantPrincipal:
        return (await self.authenticate_session(token)).principal

    async def authenticate_session(self, token: str) -> AuthenticatedSession:
        """Validate access and expose its fixed deadline for HTTP/WebSocket consumers."""
        digest = _safe_token_digest(token)
        async with transaction(self._sessions) as tx:
            record = await AuthRepository(tx.session).get_session_by_token_hash(digest)
            if record is None or record.logged_out_at is not None or record.expires_at <= self._now():
                raise AccessDenied("login session is invalid")
            principal = _decode_authorization(
                record.frozen_authorization,
                schema_version=record.authorization_schema_version,
                account_id=record.account_id,
                membership_id=record.membership_id,
                tenant_id=record.tenant_id,
            )
            return AuthenticatedSession(principal, record.expires_at)

    async def logout(self, token: str) -> None:
        digest = _safe_token_digest(token)
        async with transaction(self._sessions) as tx:
            repository = AuthRepository(tx.session)
            record = await repository.get_session_by_token_hash(digest)
            if record is None or record.logged_out_at is not None or record.expires_at <= self._now():
                raise AccessDenied("login session is invalid")
            record.logged_out_at = self._now()
            await repository.flush()

    def _now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise InvalidInput("Auth clock must return a timezone-aware datetime")
        return value


def _normalize_login_name(login_name: str) -> str:
    normalized = login_name.strip().casefold()
    if not normalized or len(normalized) > MAX_LOGIN_NAME_LENGTH:
        raise InvalidInput("login name is invalid")
    return normalized


def _encode_authorization(principal: TenantPrincipal) -> dict[str, Any]:
    return {
        "account_id": str(principal.account_id),
        "allowed_agent_ids": sorted(str(agent_id) for agent_id in principal.allowed_agent_ids),
        "membership_id": str(principal.membership_id),
        "role": principal.role,
        "schema_version": AUTHORIZATION_SCHEMA_VERSION,
        "tenant_id": str(principal.tenant_id),
    }


def _decode_authorization(
    payload: object,
    *,
    schema_version: int,
    account_id: UUID,
    membership_id: UUID,
    tenant_id: UUID,
) -> TenantPrincipal:
    required = {"account_id", "allowed_agent_ids", "membership_id", "role", "schema_version", "tenant_id"}
    if type(payload) is not dict:
        raise AccessDenied("login authorization snapshot is invalid")
    data = cast(dict[str, object], payload)
    if set(data) != required:
        raise AccessDenied("login authorization snapshot is invalid")
    if schema_version != AUTHORIZATION_SCHEMA_VERSION or data["schema_version"] != schema_version:
        raise AccessDenied("login authorization snapshot is invalid")
    if data["role"] not in ("tenant_admin", "member") or type(data["allowed_agent_ids"]) is not list:
        raise AccessDenied("login authorization snapshot is invalid")
    if len(cast(list[object], data["allowed_agent_ids"])) > MAX_CAPTURED_AGENT_IDS:
        raise AccessDenied("login authorization snapshot is invalid")
    try:
        decoded_account = UUID(cast(str, data["account_id"]))
        decoded_membership = UUID(cast(str, data["membership_id"]))
        decoded_tenant = UUID(cast(str, data["tenant_id"]))
        allowed = frozenset(UUID(cast(str, value)) for value in cast(list[object], data["allowed_agent_ids"]))
    except (TypeError, ValueError, AttributeError):
        raise AccessDenied("login authorization snapshot is invalid") from None
    if decoded_account != account_id or decoded_membership != membership_id or decoded_tenant != tenant_id:
        raise AccessDenied("login authorization snapshot is invalid")
    return TenantPrincipal(
        account_id=account_id,
        membership_id=membership_id,
        tenant_id=tenant_id,
        role=cast(Any, data["role"]),
        allowed_agent_ids=allowed,
    )


def _safe_token_digest(token: str) -> str:
    try:
        return token_digest(token)
    except InvalidInput:
        raise AccessDenied("login session is invalid") from None
