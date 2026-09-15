"""Group admission and independent A2A execution through public owner ports."""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from uuid import UUID, uuid4

from sqlalchemy.exc import SQLAlchemyError

from app.execution_dependencies.resources import ExecutionResources
from app.execution_dependencies.runtime import capture_snapshot
from app.infrastructure.database import DatabaseResources
from app.infrastructure.errors import AccessDenied, DomainError, InvalidInput
from app.infrastructure.transactions import TransactionContext, transaction
from app.modules.a2a.public import A2AIntent, A2ARequestView, A2AService, AttachmentSourceAuthorizer
from app.modules.agent.public import AgentService
from app.modules.group.public import AcceptedGroupInput, GroupRunLinkView, GroupService
from app.modules.identity_tenant.public import TenantPrincipal
from app.modules.run.public import (
    InputContent,
    RunRuntime,
    RunService,
    RunSnapshot,
    RunView,
    SourceIdentity,
    SourceSection,
    TerminalOutcomePayload,
    WaitingPayload,
)
from app.modules.session.public import SessionService
from app.modules.tool.public import AgentToolResolutionScope, PersonalAccountSelection, ToolResolutionScope
from app.modules.workspace.public import WorkspaceScope, WorkspaceSubject

logger = logging.getLogger(__name__)
_DELIVERY_CAPACITY = 1024


@dataclass(frozen=True, slots=True)
class GroupAdmissionError:
    agent_id: UUID
    code: str


@dataclass(frozen=True, slots=True)
class GroupIntake:
    accepted: AcceptedGroupInput
    runs: tuple[RunView, ...]
    errors: tuple[GroupAdmissionError, ...]


@dataclass(frozen=True, slots=True)
class A2AIntake:
    request: A2ARequestView
    run: RunView | None
    error: str | None = None


class OtherProductInputs:
    """Application owns startup/close. Durable facts stay in Group, A2A and Run."""

    def __init__(self, database: DatabaseResources, execution: ExecutionResources) -> None:
        self.database, self.execution = database, execution
        self.runtime: RunRuntime | None = None
        self.attachment_authorizer: AttachmentSourceAuthorizer | None = None
        self.attachment_binder: Callable[[TransactionContext, TenantPrincipal, AcceptedGroupInput], Awaitable[None]] | None = None
        self.message_hook: Callable[[UUID, InputContent, WorkspaceScope, UUID], Awaitable[None]] | None = None
        self.agent_message_hook: Callable[[UUID, InputContent, WorkspaceScope, UUID], Awaitable[None]] | None = None
        self._deliveries: dict[tuple[UUID, UUID], object] = {}
        self._starting = 0
        self._changed = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self._closed = False
        self.delivery_failures = 0
        self._group_preparations = asyncio.Semaphore(8)

    async def startup(self) -> None:
        if self._task is not None or self._closed or self.runtime is None:
            raise RuntimeError("Other product inputs require a ready Runtime and one startup")
        self._task = asyncio.create_task(self._delivery_loop(), name="a2a-current-process-delivery")

    async def close(self) -> None:
        self._closed = True
        self._changed.set()
        task = self._task
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._deliveries.clear()

    def _runtime(self) -> RunRuntime:
        if self.runtime is None or self._closed:
            raise RuntimeError("Other product Runtime intake is unavailable")
        return self.runtime

    async def submit_group(self, principal: TenantPrincipal, *, group_id: UUID, source_key: str,
            input: InputContent, agent_ids: tuple[UUID, ...],
            conversation_id: UUID | None = None, mentioned_membership_ids: tuple[UUID, ...] = (),
            account_selections: tuple[PersonalAccountSelection, ...] = (),
            accepted_consumer: Callable[[TransactionContext, AcceptedGroupInput], Awaitable[None]] | None = None) -> GroupIntake:
        runtime = self._runtime()
        async with transaction(self.database.control_sessions) as tx:
            owner = GroupService(tx, enabled_sources=self.execution.market.enabled_source_ids)
            accepted = await owner.accept_input(principal, group_id=group_id, source_key=source_key,
                input=input, agent_ids=agent_ids, account_selections=account_selections,
                conversation_id=conversation_id, mentioned_membership_ids=mentioned_membership_ids)
            if self.attachment_binder is not None:
                await self.attachment_binder(tx, principal, accepted)
            if accepted_consumer is not None:
                await accepted_consumer(tx, accepted)
            group = await owner.get(principal, group_id=group_id)
            context_history = await owner.read_context_history(principal, group_id=group_id,
                conversation_id=accepted.event.conversation_id, through_position=accepted.event.position - 1)
        async def admit(link: GroupRunLinkView) -> RunView | GroupAdmissionError:
            async with self._group_preparations:
                return await prepare(link)

        async def prepare(link: GroupRunLinkView) -> RunView | GroupAdmissionError:
            if link.run_id is not None:
                async with transaction(self.database.control_sessions) as tx:
                    return await RunService(tx).get(tenant_id=principal.tenant_id, run_id=link.run_id)
            try:
                async with transaction(self.database.control_sessions) as tx:
                    agent = await AgentService(tx).get_for_execution(principal, agent_id=link.agent_id)
                    accounts = await GroupService(tx).input_accounts(principal, group_id=group_id,
                        event_id=accepted.event.id, target_agent_id=agent.id)
                model = await self.execution.model.resolve_configured_policy(tenant_id=principal.tenant_id, model_id=agent.model_id)
                scope = WorkspaceScope(principal.tenant_id, agent.id, WorkspaceSubject("group", group_id), uuid4(),
                    allow_shared_memory_writes=False)
                await self.execution.workspace.ensure(scope, scope.output)
                await self.execution.workspace.ensure(scope, WorkspaceSubject("agent", agent.id))
                snapshot = await capture_snapshot(self.execution, self.database, agent=agent, model=model, workspace=scope,
                    tools=ToolResolutionScope(principal, agent.id, "main", frozenset(accounts), accounts))
                if group.announcement:
                    snapshot = replace(snapshot, sources=snapshot.sources + (SourceSection("product_context", scope.output,
                        f"group:{group.id}:announcement", group.announcement),))
                snapshot = replace(snapshot, sources=snapshot.sources + (SourceSection("product_context", scope.output,
                    f"group:{group.id}:conversation:{accepted.event.conversation_id}:through:{accepted.event.position - 1}",
                    context_history),))
                started = await runtime.start(snapshot=snapshot, input=accepted.event.input,
                    source=SourceIdentity("group", accepted.event.id, str(link.agent_id)))
                return started.run
            except (DomainError, OverflowError) as error:
                code = error.code if isinstance(error, DomainError) else "admission_capacity"
                async with transaction(self.database.control_sessions) as tx:
                    await GroupService(tx).mark_admission_failed(tenant_id=principal.tenant_id,
                        event_id=accepted.event.id, agent_id=link.agent_id, reason=code)
                return GroupAdmissionError(link.agent_id, code)
        outcomes = await asyncio.gather(*(admit(link) for link in accepted.links))
        if self.message_hook is not None:
            for link in accepted.links:
                scope = WorkspaceScope(principal.tenant_id, link.agent_id, WorkspaceSubject("group", group_id),
                    uuid4(), allow_shared_memory_writes=False)
                await self.message_hook(accepted.event.id, accepted.event.input, scope, principal.membership_id)
        return GroupIntake(accepted, tuple(value for value in outcomes if isinstance(value, RunView)),
            tuple(value for value in outcomes if isinstance(value, GroupAdmissionError)))

    async def submit_a2a(self, snapshot: RunSnapshot, step_id: str, call_id: str, *, target_agent_id: UUID,
            intent: A2AIntent, input: InputContent) -> A2AIntake:
        runtime = self._runtime()
        if snapshot.role != "main" or snapshot.workspace.run_id is None:
            raise AccessDenied("Only a Main Run may initiate A2A work")
        if self._task is None or self._task.done():
            raise RuntimeError("A2A result delivery is not started")
        if len(self._deliveries) + self._starting >= _DELIVERY_CAPACITY:
            raise InvalidInput("A2A result delivery capacity is exhausted; retry later")
        self._starting += 1
        try:
            async with transaction(self.database.control_sessions) as tx:
                source_run = await RunService(tx).get(tenant_id=snapshot.tenant_id, run_id=snapshot.workspace.run_id)
                if source_run.source.kind == "session":
                    accounts = await SessionService(tx).execution_accounts(source_run, target_agent_id=target_agent_id)
                elif source_run.source.kind == "group":
                    accounts = await GroupService(tx).execution_accounts(source_run, target_agent_id=target_agent_id)
                else:
                    # Receiver account access is not authority to delegate it onwards.
                    accounts = ()
                request = await A2AService(tx).accept(tenant_id=snapshot.tenant_id,
                    source_run_id=snapshot.workspace.run_id, step_id=step_id, call_id=call_id,
                    target_agent_id=target_agent_id, intent=intent, input=input, delegated_connection_ids=accounts,
                    attachment_authorizer=self.attachment_authorizer)
            if request.intent != "notify":
                # This intake already reserved capacity before its first await.
                self._deliveries[(request.tenant_id, request.id)] = object()
                self._changed.set()
            if request.target_run_id is not None:
                async with transaction(self.database.control_sessions) as tx:
                    run = await RunService(tx).get(tenant_id=request.tenant_id, run_id=request.target_run_id)
                    original = await RunService(tx).read_snapshot(tenant_id=request.tenant_id, run_id=request.target_run_id)
                if self.agent_message_hook is not None:
                    await self.agent_message_hook(request.id, request.input, original.workspace, request.source_agent_id)
                return A2AIntake(request, run)
            if request.admission == "failed":
                return A2AIntake(request, None, request.admission_error)
            try:
                async with transaction(self.database.control_sessions) as tx:
                    agent = await AgentService(tx).get_for_agent_execution(tenant_id=request.tenant_id, agent_id=request.target_agent_id)
                model = await self.execution.model.resolve_configured_policy(tenant_id=request.tenant_id, model_id=agent.model_id)
                own = WorkspaceSubject("agent", agent.id)
                allow_shared = (not request.delegated_connection_ids and snapshot.workspace.output.kind == "agent"
                    and snapshot.workspace.allow_shared_memory_writes)
                scope = WorkspaceScope(request.tenant_id, agent.id, own, uuid4(), allow_shared_memory_writes=allow_shared,
                    allow_shared_file_writes=False)
                await self.execution.workspace.ensure(scope, own)
                target = await capture_snapshot(self.execution, self.database, agent=agent, model=model, workspace=scope,
                    tools=AgentToolResolutionScope(request.tenant_id, agent.id, "main",
                        frozenset(request.delegated_connection_ids), request.delegated_connection_ids))
                started = await runtime.start(snapshot=target, input=request.input, source=SourceIdentity("a2a", request.id, "target"))
            except (DomainError, OverflowError) as error:
                code = error.code if isinstance(error, DomainError) else "admission_capacity"
                async with transaction(self.database.control_sessions) as tx:
                    request = await A2AService(tx).mark_admission_failed(tenant_id=request.tenant_id, request_id=request.id, reason=code)
                return A2AIntake(request, None, code)
            async with transaction(self.database.control_sessions) as tx:
                request = await A2AService(tx).get(tenant_id=request.tenant_id, request_id=request.id)
            if self.agent_message_hook is not None:
                await self.agent_message_hook(request.id, request.input, scope, request.source_agent_id)
            return A2AIntake(request, started.run)
        finally:
            self._starting -= 1

    async def record_started(self, transaction: TransactionContext, *, run: RunView) -> None:
        if run.source.kind == "group":
            await GroupService(transaction).record_started(transaction, run=run)
        elif run.source.kind == "a2a":
            await A2AService(transaction).record_started(transaction, run=run)

    async def record_waiting(self, transaction: TransactionContext, *, run: RunView, waiting: WaitingPayload) -> None:
        if run.source.kind == "group":
            await GroupService(transaction).record_waiting(transaction, run=run, waiting=waiting)
        elif run.source.kind == "a2a":
            await A2AService(transaction).record_waiting(transaction, run=run, waiting=waiting)

    async def record_outcome(self, transaction: TransactionContext, *, run: RunView, outcome: TerminalOutcomePayload) -> None:
        if run.source.kind == "group":
            await GroupService(transaction).record_outcome(transaction, run=run, outcome=outcome)
        elif run.source.kind == "a2a":
            await A2AService(transaction).record_outcome(transaction, run=run, outcome=outcome)

    def track_request(self, *, tenant_id: UUID, request_id: UUID) -> None:
        """Track one explicitly accepted or claimed request in this process, without replaying work."""
        key = (tenant_id, request_id)
        if self._closed or (key not in self._deliveries and len(self._deliveries) + self._starting >= _DELIVERY_CAPACITY):
            raise InvalidInput("A2A result tracking capacity is unavailable")
        self._deliveries[key] = object()
        self._changed.set()

    async def _delivery_loop(self) -> None:
        while not self._closed:
            self._changed.clear()
            tracked = dict(self._deliveries)
            tenants: dict[UUID, list[UUID]] = {}
            for tenant, request in tuple(self._deliveries):
                tenants.setdefault(tenant, []).append(request)
            for tenant, ids in tenants.items():
                for offset in range(0, len(ids), 100):
                    try:
                        async with transaction(self.database.control_sessions) as tx:
                            states = await A2AService(tx).delivery_states(tenant_id=tenant, request_ids=tuple(ids[offset:offset + 100]))
                    except (DomainError, SQLAlchemyError) as error:
                        self.delivery_failures += 1
                        logger.warning("A2A result delivery requires retry: %s", type(error).__name__)
                        continue
                    for state in states:
                        try:
                            if state.source_delivery == "pending":
                                async with transaction(self.database.execution_sessions) as tx:
                                    changed = await A2AService(tx).deliver_pending(tenant_id=tenant, request_id=state.request_id)
                                if changed is not None:
                                    await self._runtime().post_commit(changed)
                            key = (tenant, state.request_id)
                            if state.result_kind in ("terminal", "admission_failed") and self._deliveries.get(key) is tracked.get(key):
                                self._deliveries.pop(key, None)
                        except (DomainError, SQLAlchemyError) as error:
                            self.delivery_failures += 1
                            logger.warning("A2A result delivery requires retry: %s", type(error).__name__)
            try:
                await asyncio.wait_for(self._changed.wait(), timeout=0.2 if self._deliveries else 60.0)
            except TimeoutError:
                pass
