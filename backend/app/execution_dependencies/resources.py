"""Application-owned execution services and transport resources."""

from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass, field
from uuid import UUID

import httpx
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.infrastructure.config import Settings, reveal_database_url
from app.infrastructure.database import DatabaseResources
from app.infrastructure.errors import AccessDenied
from app.infrastructure.execution_config import ExecutionSettings, LocalStorageSettings
from app.infrastructure.http import create_stateless_http_client
from app.infrastructure.object_storage.base import StorageBackend
from app.infrastructure.object_storage.local import LocalStorageBackend
from app.infrastructure.object_storage.s3 import S3StorageBackend
from app.infrastructure.resource_locks import PostgresResourceLocks
from app.infrastructure.transactions import TransactionContext, transaction
from app.modules.audit.public import AuditSink
from app.modules.capability_market.public import CapabilityMarketService
from app.modules.context.public import ContextTelemetry
from app.modules.credential.public import CredentialKeyring, CredentialService, Secret
from app.modules.model.public import ModelExecutionService
from app.modules.tool.public import CallScope, CredentialBinding, ToolService
from app.modules.workspace.public import WorkspaceService


class ContextStatistics:
    """Fixed-cardinality application observations; no content, identities or execution decisions."""

    def __init__(self) -> None:
        self._totals: dict[str, int | float] = {"preparations": 0}

    def observe(self, telemetry: ContextTelemetry) -> None:
        self._totals["preparations"] += 1
        for name in ("assembly_seconds", "input_tokens", "source_reads", "source_snapshot_reuses", "cleared_tool_tokens",
                     "compactions", "compaction_seconds", "validated_units", "serialized_messages", "reused_units",
                     "token_counting_calls", "token_counting_seconds"):
            value = getattr(telemetry, name)
            if value is None:
                self._totals[name + "_unknown"] = self._totals.get(name + "_unknown", 0) + 1
            else:
                self._totals[name] = self._totals.get(name, 0) + value
        self._totals["last_coverage_sequence"] = telemetry.coverage_sequence

    def snapshot(self) -> dict[str, int | float]:
        return dict(self._totals)


@dataclass(frozen=True, slots=True)
class ExecutionResources:
    workspace: WorkspaceService
    market: CapabilityMarketService
    model: ModelExecutionService
    http: httpx.AsyncClient = field(repr=False)
    _sessions: async_sessionmaker[AsyncSession] = field(repr=False)
    _credential_keys: CredentialKeyring = field(repr=False)
    context_statistics: ContextStatistics = field(default_factory=ContextStatistics)

    def tools(self, transaction_context: TransactionContext) -> ToolService:
        return ToolService(transaction_context, enabled_sources=self.market.enabled_source_ids)

    def credentials(self, transaction_context: TransactionContext) -> CredentialService:
        return CredentialService(transaction_context, self._credential_keys)

    async def resolve_credential(self, binding: CredentialBinding, scope: CallScope) -> Secret:
        """Only resolved authorized bindings enter this port; no transaction reaches HTTP."""
        if (
            binding.owner_kind == "agent" and binding.owner_id != scope.agent_id
            or binding.owner_kind == "tenant" and binding.owner_id != scope.tenant_id
        ):
            raise AccessDenied("Credential binding does not belong to the resolved execution scope")
        async with transaction(self._sessions) as tx:
            return await self.credentials(tx).reveal_secret_for_owner(
                tenant_id=scope.tenant_id, credential_id=binding.id,
                owner_kind=binding.owner_kind, owner_id=binding.owner_id,
            )


@asynccontextmanager
async def open_execution_resources(
    settings: ExecutionSettings, database: DatabaseResources, audit: AuditSink,
) -> AsyncIterator[ExecutionResources]:
    """Construct once per application; the application drains consumers before exit."""
    credential_keys = CredentialKeyring(
        active_key_version=settings.credential_keys.active_version,
        keys=settings.credential_keys.decoded_keys(),
    )
    continuation_keys = settings.continuation_keys.decoded_keys()
    async with AsyncExitStack() as cleanup:
        http = create_stateless_http_client(
            limits=httpx.Limits(max_connections=settings.http.max_connections,
                               max_keepalive_connections=settings.http.max_keepalive_connections),
        )
        cleanup.push_async_callback(http.aclose)
        storage: StorageBackend
        configured_storage = settings.storage
        if isinstance(configured_storage, LocalStorageSettings):
            storage = LocalStorageBackend(str(configured_storage.root))
        else:
            lock_url = Settings._complete_async_postgres_url(configured_storage.lock_database_url)
            lock_engine = create_async_engine(
                reveal_database_url(lock_url), pool_size=configured_storage.lock_pool_size, max_overflow=0,
            )
            cleanup.push_async_callback(lock_engine.dispose)
            storage = S3StorageBackend(
                bucket=configured_storage.bucket, prefix=configured_storage.prefix,
                region=configured_storage.region, endpoint_url=configured_storage.endpoint or "",
                access_key_id=(configured_storage.access_key_id.get_secret_value()
                               if configured_storage.access_key_id is not None else ""),
                secret_access_key=(configured_storage.secret_access_key.get_secret_value()
                                   if configured_storage.secret_access_key is not None else ""),
                lock_provider=PostgresResourceLocks(lock_engine, timeout_seconds=configured_storage.lock_timeout_seconds),
            )
        cleanup.push_async_callback(storage.aclose)

        async def enabled_sources(
            *, transaction_context: TransactionContext, tenant_id: UUID, requested_ids: frozenset[UUID],
        ) -> frozenset[UUID]:
            return await market.enabled_source_ids(
                transaction_context=transaction_context, tenant_id=tenant_id, requested_ids=requested_ids,
            )

        workspace = WorkspaceService(database.execution_sessions, storage, audit, enabled_skill_sources=enabled_sources)
        market = CapabilityMarketService(database.execution_sessions, audit, workspace)
        model = ModelExecutionService(
            database.execution_sessions, http_client=http, credential_keyring=credential_keys,
            continuation_keys=continuation_keys, active_continuation_key=settings.continuation_keys.active_version,
        )
        yield ExecutionResources(workspace, market, model, http, database.execution_sessions, credential_keys)
