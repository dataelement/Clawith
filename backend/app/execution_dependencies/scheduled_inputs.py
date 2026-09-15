"""Application-owned clock intake; Trigger and Heartbeat retain configuration and occurrence authority."""

import asyncio
import hashlib
import hmac
import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Literal
from uuid import UUID, uuid4

import httpx
from sqlalchemy.exc import SQLAlchemyError

from app.execution_dependencies.resources import ExecutionResources
from app.execution_dependencies.runtime import capture_snapshot
from app.infrastructure.database import DatabaseResources
from app.infrastructure.errors import AccessDenied, DomainError, InvalidInput, NotFound
from app.infrastructure.http import require_stateless_http_client
from app.infrastructure.transactions import TransactionContext, transaction
from app.modules.agent.public import AgentService
from app.modules.group.public import GroupService
from app.modules.heartbeat.public import HeartbeatOccurrence, HeartbeatService
from app.modules.identity_tenant.public import IdentityService, TenantPrincipal
from app.modules.run.public import InputContent, RunRuntime, RunView, SourceSection, TerminalOutcomePayload
from app.modules.tool.public import AgentToolResolutionScope, ToolService
from app.modules.trigger.public import TriggerDue, TriggerOccurrence, TriggerService
from app.modules.workspace.public import WorkspaceScope, WorkspaceSubject

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ScheduledDestination:
    kind: Literal["session", "group"]
    id: UUID
    conversation_id: UUID | None
    origin_kind: Literal["agent", "membership", "group"]
    origin_id: UUID
    origin_conversation_id: UUID | None


class ScheduledInputs:
    def __init__(self, database: DatabaseResources, execution: ExecutionResources, *,
            clock: Callable[[], datetime] | None = None, poll_interval_seconds: float = 1.0) -> None:
        if not 0 < poll_interval_seconds <= 60:
            raise ValueError("Schedule scan interval must be positive and at most one minute")
        self.database, self.execution = database, execution
        self.runtime: RunRuntime | None = None
        self._clock = clock or (lambda: datetime.now(UTC))
        self._interval = poll_interval_seconds
        self._not_before: datetime | None = None
        self._task: asyncio.Task[None] | None = None
        self._tick_lock = asyncio.Lock()
        self._intake_slots = asyncio.Semaphore(8)
        self._stopped = False
        self.errors = 0

    def _now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Scheduling requires an aware clock")
        return value.astimezone(UTC)

    async def start(self) -> None:
        if self._task is not None or self._stopped or self.runtime is None:
            raise RuntimeError("Scheduled Runtime is not ready or already started")
        self._not_before = self._now()
        self._task = asyncio.create_task(self._loop(), name="scheduled-product-inputs")

    async def close(self) -> None:
        self._stopped = True
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)

    async def _loop(self) -> None:
        while not self._stopped:
            try:
                await self.tick()
            except (DomainError, SQLAlchemyError) as error:
                self.errors += 1
                logger.warning("Schedule scan failed: %s", type(error).__name__)
            await asyncio.sleep(self._interval)

    async def tick(self) -> None:
        if self._not_before is None or self._stopped:
            raise RuntimeError("Scheduled inputs are not running")
        async with self._tick_lock, asyncio.TaskGroup() as group:
            group.create_task(self._scan(self._triggers))
            group.create_task(self._scan(self._heartbeats))

    async def _scan(self, scan: Callable[[], Awaitable[None]]) -> None:
        try:
            await scan()
        except (DomainError, SQLAlchemyError) as error:
            self.errors += 1
            logger.warning("Schedule owner scan failed: %s", type(error).__name__)

    async def _triggers(self) -> None:
        assert self._not_before is not None
        after = None
        while not self._stopped:
            async with transaction(self.database.control_sessions) as tx:
                page = await TriggerService(tx).due(now=self._now(), not_before=self._not_before, after_id=after)
                tenants = await IdentityService(tx).filter_enabled_tenant_ids(tenant_ids=tuple({item.trigger.tenant_id for item in page.items}))
            self.errors += len(page.errors)
            async with asyncio.TaskGroup() as group:
                for item in page.items:
                    if item.trigger.tenant_id in tenants:
                        group.create_task(self._trigger_due(item))
            if page.next_after_id is None:
                return
            after = page.next_after_id

    async def _trigger_due(self, item: TriggerDue) -> None:
        async with self._intake_slots:
            try:
                if item.trigger.config.kind == "poll":
                    occurrence = await self._poll(item)
                else:
                    async with transaction(self.database.control_sessions) as tx:
                        occurrence = await TriggerService(tx).accept(tenant_id=item.trigger.tenant_id,
                            trigger_id=item.trigger.id, source_key=item.source_key, due_at=item.due_at,
                            now=self._now(), not_before=self._not_before)
                if occurrence is not None:
                    await self._execute(occurrence)
            except (DomainError, SQLAlchemyError, httpx.HTTPError, TimeoutError) as error:
                self.errors += 1
                logger.warning("Trigger occurrence intake failed: %s", type(error).__name__)

    async def _secret(self, tenant_id: UUID, agent_id: UUID, credential_id: UUID) -> str:
        async with transaction(self.database.control_sessions) as tx:
            credentials = self.execution.credentials(tx)
            try:
                owner = await credentials.require_owner_metadata(tenant_id=tenant_id, credential_id=credential_id,
                    owner_kind="agent", owner_id=agent_id)
            except NotFound:
                owner = await credentials.require_owner_metadata(tenant_id=tenant_id, credential_id=credential_id,
                    owner_kind="tenant", owner_id=tenant_id)
            secret = await credentials.reveal_secret_for_owner(tenant_id=tenant_id, credential_id=credential_id,
                owner_kind=owner.owner_kind, owner_id=owner.owner_id)
            return secret.value

    async def _poll(self, item: TriggerDue) -> TriggerOccurrence | None:
        assert self._not_before is not None
        config = item.trigger.config
        assert config.poll_url is not None
        headers = dict(config.poll_headers)
        if config.poll_credential_id is not None:
            value = await self._secret(item.trigger.tenant_id, item.trigger.agent_id, config.poll_credential_id)
            if not value or "\r" in value or "\n" in value or not value.isascii():
                raise InvalidInput("Poll Credential must contain one complete ASCII Authorization header value")
            headers["Authorization"] = value
        require_stateless_http_client(self.execution.http)
        request = httpx.Request(config.poll_method, config.poll_url, headers=headers)
        async with asyncio.timeout(10):
            response = await self.execution.http.send(request, stream=True, auth=None, follow_redirects=False)
            try:
                response.raise_for_status()
                chunks, size = [], 0
                async for chunk in response.aiter_bytes():
                    size += len(chunk)
                    if size > 128 * 1024:
                        raise InvalidInput("Poll response exceeds 128 KiB")
                    chunks.append(chunk)
                body = b"".join(chunks)
            finally:
                await response.aclose()
        try:
            data = {} if config.poll_method == "HEAD" else json.loads(body)
            selected = data
            if config.poll_json_path != "$":
                if not config.poll_json_path.startswith("$."):
                    raise ValueError
                for part in config.poll_json_path[2:].split("."):
                    if isinstance(selected, dict):
                        selected = selected[part]
                    elif isinstance(selected, list) and part.isdigit():
                        selected = selected[int(part)]
                    else:
                        raise ValueError
            value = selected if isinstance(selected, str) else json.dumps(selected, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
        except (ValueError, TypeError, KeyError, IndexError, RecursionError):
            raise InvalidInput("Poll response or selected JSON path is invalid") from None
        async with transaction(self.database.control_sessions) as tx:
            return await TriggerService(tx).observe_poll(tenant_id=item.trigger.tenant_id, trigger_id=item.trigger.id,
                due_at=item.due_at, now=self._now(), not_before=self._not_before, value=value, expected_config=config)

    async def fire_manual(self, principal: TenantPrincipal, *, trigger_id: UUID, event_id: str,
            input: InputContent | None = None) -> TriggerOccurrence:
        async with transaction(self.database.control_sessions) as tx:
            service = TriggerService(tx)
            config = await service.get(principal, trigger_id=trigger_id)
            if config.delegated_connection_ids:
                owners = await ToolService(tx).personal_connection_owners(tenant_id=principal.tenant_id,
                    connection_ids=config.delegated_connection_ids)
                if set(owners) != set(config.delegated_connection_ids) or set(owners.values()) != {principal.membership_id}:
                    raise AccessDenied("Only the original account owner may manually start this private schedule")
            occurrence = await service.accept(tenant_id=principal.tenant_id, trigger_id=trigger_id,
                source_key="manual:" + event_id, now=self._now(), input=input, event_kind="manual")
            occurrence = await service.get_occurrence(tenant_id=principal.tenant_id, occurrence_id=occurrence.id)
            await self._authorize_result(tx, principal, occurrence)
        await self._execute(occurrence)
        async with transaction(self.database.control_sessions) as tx:
            result = await TriggerService(tx).get_occurrence(tenant_id=principal.tenant_id, occurrence_id=occurrence.id)
            await self._authorize_result(tx, principal, result)
            return result

    @staticmethod
    async def _authorize_result(tx: TransactionContext, principal: TenantPrincipal, occurrence: TriggerOccurrence) -> None:
        if occurrence.origin_kind == "membership" and occurrence.origin_id == principal.membership_id:
            return
        if (occurrence.origin_kind == "group" and occurrence.origin_id is not None
                and occurrence.origin_id in await GroupService(tx).authorized_group_ids(principal, group_ids=(occurrence.origin_id,))):
            return
        if occurrence.origin_kind == "agent" and (principal.can_manage_all_agents or occurrence.origin_id in principal.allowed_agent_ids):
            return
        raise AccessDenied("Scheduled result is outside the requester's original scope")

    async def webhook(self, *, tenant_id: UUID, trigger_id: UUID, event_id: str, signature: str, body: bytes) -> TriggerOccurrence:
        if not event_id or "\n" in event_id or "\r" in event_id or len(event_id.encode()) > 480 or len(body) > 64 * 1024:
            raise InvalidInput("Webhook identity or body exceeds its bound")
        async with transaction(self.database.control_sessions) as tx:
            if tenant_id not in await IdentityService(tx).filter_enabled_tenant_ids(tenant_ids=(tenant_id,)):
                raise AccessDenied("Webhook is unavailable")
            target = await TriggerService(tx).get_for_intake(tenant_id=tenant_id, trigger_id=trigger_id)
        credential_id = target.config.webhook_credential_id
        if target.config.kind != "webhook" or credential_id is None:
            raise AccessDenied("Webhook is unavailable")
        secret = await self._secret(tenant_id, target.agent_id, credential_id)
        expected = hmac.new(secret.encode(), event_id.encode() + b"\n" + body, hashlib.sha256).hexdigest()
        if len(signature) != 64 or any(value not in "0123456789abcdef" for value in signature) or not hmac.compare_digest(signature, expected):
            raise AccessDenied("Webhook signature is invalid")
        try:
            text = body.decode("utf-8")
        except UnicodeError:
            raise InvalidInput("Webhook body must be UTF-8") from None
        async with transaction(self.database.control_sessions) as tx:
            occurrence = await TriggerService(tx).accept(tenant_id=tenant_id, trigger_id=trigger_id,
                source_key="webhook:" + event_id, now=self._now(), input=InputContent(text), event_kind="webhook",
                expected_config=target.config)
        await self._execute(occurrence)
        async with transaction(self.database.control_sessions) as tx:
            return await TriggerService(tx).get_occurrence(tenant_id=tenant_id, occurrence_id=occurrence.id)

    async def on_message(self, *, message_id: UUID, input: InputContent, workspace: WorkspaceScope,
            source_agent_id: UUID | None = None, source_membership_id: UUID | None = None,
            origin_override: WorkspaceSubject | None = None, origin_conversation_id: UUID | None = None) -> int:
        """Receive only an already authorized message addressed to this Agent, never scan other conversations."""
        if workspace.preview_only or (source_agent_id is None) == (source_membership_id is None):
            raise InvalidInput("Message-trigger intake requires its authorized execution scope and one sender")
        async with transaction(self.database.control_sessions) as tx:
            if workspace.tenant_id not in await IdentityService(tx).filter_enabled_tenant_ids(tenant_ids=(workspace.tenant_id,)):
                raise AccessDenied("Message-trigger Tenant is unavailable")
        scope = AgentToolResolutionScope(workspace.tenant_id, workspace.agent_id, "main")
        origin = origin_override or workspace.output
        conversation_id = origin_conversation_id
        if origin.kind == "group" and conversation_id is None and origin_override is None:
            async with transaction(self.database.control_sessions) as tx:
                conversation_id = await GroupService(tx).event_conversation(tenant_id=workspace.tenant_id,
                    group_id=workspace.output.id, event_id=message_id)
        after, accepted = None, 0
        while True:
            async with transaction(self.database.control_sessions) as tx:
                page = await TriggerService(tx).list_for_agent(scope, after_id=after)
            for item in page:
                if (item.config.kind != "on_message" or not item.enabled
                        or item.config.source_agent_id not in (None, source_agent_id)
                        or item.config.source_membership_id not in (None, source_membership_id)):
                    continue
                try:
                    async with transaction(self.database.control_sessions) as tx:
                        occurrence = await TriggerService(tx).accept(tenant_id=workspace.tenant_id, trigger_id=item.id,
                            source_key="message:" + str(message_id), now=self._now(), input=input, event_kind="on_message",
                            source_agent_id=source_agent_id, source_membership_id=source_membership_id, expected_config=item.config,
                            origin=origin, origin_conversation_id=conversation_id)
                    await self._execute(occurrence, workspace=workspace)
                    accepted += 1
                except DomainError as error:
                    self.errors += 1
                    logger.warning("Message Trigger intake failed: %s", type(error).__name__)
            if len(page) < 100:
                return accepted
            after = page[-1].id

    async def _heartbeats(self) -> None:
        assert self._not_before is not None
        after = None
        while not self._stopped:
            async with transaction(self.database.control_sessions) as tx:
                page = await HeartbeatService(tx).due(now=self._now(), not_before=self._not_before, after_id=after)
                tenants = await IdentityService(tx).filter_enabled_tenant_ids(tenant_ids=tuple({item.heartbeat.tenant_id for item in page.items}))
            self.errors += len(page.errors)
            for item in page.items:
                if item.heartbeat.tenant_id not in tenants:
                    continue
                try:
                    async with transaction(self.database.control_sessions) as tx:
                        occurrence = await HeartbeatService(tx).accept(tenant_id=item.heartbeat.tenant_id,
                            heartbeat_id=item.heartbeat.id, source_key=item.source_key, due_at=item.due_at,
                            now=self._now(), not_before=self._not_before)
                    await self._execute(occurrence)
                except (DomainError, SQLAlchemyError) as error:
                    self.errors += 1
                    logger.warning("Heartbeat occurrence intake failed: %s", type(error).__name__)
            if page.next_after_id is None:
                return
            after = page.next_after_id

    async def _execute(self, occurrence: TriggerOccurrence | HeartbeatOccurrence, *, workspace: WorkspaceScope | None = None) -> None:
        if self.runtime is None:
            raise RuntimeError("Scheduled Runtime is not ready")
        if occurrence.run_id is not None:
            return
        try:
            async with transaction(self.database.control_sessions) as tx:
                agent = await AgentService(tx).get_for_agent_execution(tenant_id=occurrence.tenant_id, agent_id=occurrence.agent_id)
            model = await self.execution.model.resolve_configured_policy(tenant_id=occurrence.tenant_id, model_id=agent.model_id)
            if workspace is not None and (workspace.tenant_id, workspace.agent_id) != (occurrence.tenant_id, occurrence.agent_id):
                raise AccessDenied("Scheduled event scope does not match its target Agent")
            scope = (WorkspaceScope(occurrence.tenant_id, occurrence.agent_id, WorkspaceSubject("agent", occurrence.agent_id), uuid4())
                if workspace is None else replace(workspace, run_id=uuid4(), main=True, allow_shared_memory_writes=False))
            if occurrence.origin_kind is not None and (occurrence.origin_kind, occurrence.origin_id) != (scope.output.kind, scope.output.id):
                scope = replace(scope, allow_shared_file_writes=False, allow_shared_memory_writes=False)
            await self.execution.workspace.ensure(scope, scope.output)
            tools = AgentToolResolutionScope(occurrence.tenant_id, occurrence.agent_id, "main",
                frozenset(occurrence.delegated_connection_ids), occurrence.delegated_connection_ids)
            snapshot = await capture_snapshot(self.execution, self.database, agent=agent, model=model, workspace=scope, tools=tools)
            snapshot = replace(snapshot,
                allow_human_input=False,
                initial_direct_names=snapshot.initial_direct_names - {"need_input"},
                sources=snapshot.sources + (SourceSection("product_context", scope.output,
                    f"scheduled:{occurrence.source.kind}:{occurrence.id}:execution-policy",
                    "This is unattended scheduled execution. Do not wait for human answers. "
                    "Subagents may ask their Parent Main for task information. "
                    "If essential information is unavailable, record what is missing in the final execution result and finish. "
                    "Only an explicitly configured destination may receive messages; otherwise retain the execution result. "
                    + json.dumps({"destination_kind": occurrence.destination_kind,
                        "destination_id": str(occurrence.destination_id) if occurrence.destination_id else None,
                        "destination_conversation_id": str(occurrence.destination_conversation_id) if occurrence.destination_conversation_id else None})),))
            await self.runtime.start(snapshot=snapshot, input=occurrence.input, source=occurrence.source)
        except DomainError as error:
            async with transaction(self.database.control_sessions) as tx:
                service = TriggerService(tx) if isinstance(occurrence, TriggerOccurrence) else HeartbeatService(tx)
                await service.fail_admission(tenant_id=occurrence.tenant_id, occurrence_id=occurrence.id, reason=error.code)

    async def record_started(self, transaction: TransactionContext, *, run: RunView) -> None:
        if run.source.kind == "trigger":
            await TriggerService(transaction).record_started(transaction, run=run)
        elif run.source.kind == "heartbeat":
            await HeartbeatService(transaction).record_started(transaction, run=run)

    async def _execution_occurrence(self, transaction: TransactionContext, run: RunView) -> TriggerOccurrence | HeartbeatOccurrence:
        if run.parent_run_id is not None or run.source.kind not in ("trigger", "heartbeat"):
            raise AccessDenied("Only an originating scheduled Main has occurrence provenance")
        owner = TriggerService(transaction) if run.source.kind == "trigger" else HeartbeatService(transaction)
        occurrence = await owner.get_occurrence(tenant_id=run.tenant_id, occurrence_id=run.source.owner_id)
        if (occurrence.run_id, occurrence.agent_id, occurrence.source) != (run.id, run.agent_id, run.source):
            raise AccessDenied("Scheduled execution does not match its accepted occurrence")
        return occurrence

    async def execution_origin(self, transaction: TransactionContext, run: RunView) -> tuple[WorkspaceSubject, UUID | None]:
        """Expose immutable result visibility, not additional Workspace access authority."""
        occurrence = await self._execution_occurrence(transaction, run)
        if occurrence.origin_kind is None or occurrence.origin_id is None:
            raise InvalidInput("Scheduled execution requires its original provenance")
        return WorkspaceSubject(occurrence.origin_kind, occurrence.origin_id), occurrence.origin_conversation_id

    async def execution_destination(self, transaction: TransactionContext, run: RunView) -> ScheduledDestination | None:
        occurrence = await self._execution_occurrence(transaction, run)
        if occurrence.destination_kind is None:
            return None
        assert occurrence.destination_id is not None
        if occurrence.origin_kind is None or occurrence.origin_id is None:
            raise InvalidInput("Scheduled destination requires its original provenance")
        return ScheduledDestination(occurrence.destination_kind, occurrence.destination_id, occurrence.destination_conversation_id,
            occurrence.origin_kind, occurrence.origin_id, occurrence.origin_conversation_id)

    async def record_outcome(self, transaction: TransactionContext, *, run: RunView, outcome: TerminalOutcomePayload) -> None:
        if run.source.kind == "trigger":
            await TriggerService(transaction).record_outcome(transaction, run=run, outcome=outcome)
        elif run.source.kind == "heartbeat":
            await HeartbeatService(transaction).record_outcome(transaction, run=run, outcome=outcome)
