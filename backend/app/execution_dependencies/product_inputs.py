"""Application orchestration over product and Run public services."""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from uuid import UUID, uuid4

from sqlalchemy.exc import SQLAlchemyError

from app.execution_dependencies.a2a_temp_files import A2ATempFiles
from app.execution_dependencies.a2a_tools import A2AExecutor
from app.execution_dependencies.attachment_inputs import AttachmentInputs
from app.execution_dependencies.attachment_tools import (
    AttachmentBlob,
    attachment_preview_binding,
    attachment_save_binding,
)
from app.execution_dependencies.document_tools import DocumentParser, document_tool_binding
from app.execution_dependencies.goal_inputs import GoalInputs, goal_instructions
from app.execution_dependencies.message_tools import MessageExecutor, WorkspaceMessageFile
from app.execution_dependencies.other_product_inputs import OtherProductInputs
from app.execution_dependencies.resources import ExecutionResources
from app.execution_dependencies.runtime import capture_snapshot
from app.execution_dependencies.schedule_tools import schedule_tool_bindings
from app.execution_dependencies.scheduled_inputs import ScheduledInputs
from app.execution_dependencies.session_streams import SessionExecutionStreams
from app.execution_dependencies.session_tools import session_tool_bindings
from app.execution_dependencies.temp_file_tools import temp_file_binding
from app.infrastructure.database import DatabaseResources
from app.infrastructure.errors import AccessDenied, DomainError
from app.infrastructure.transactions import TransactionContext, transaction
from app.modules.a2a.public import A2AInputVisibility, A2AService
from app.modules.agent.public import AgentService
from app.modules.group.public import AcceptedGroupInput, GroupService
from app.modules.identity_tenant.public import TenantPrincipal
from app.modules.run.public import (
    InputContent,
    OutcomeConsumer,
    RunRuntime,
    RunService,
    RunSnapshot,
    RunView,
    SourceIdentity,
    SourceSection,
    TerminalOutcomePayload,
    WaitingPayload,
)
from app.modules.session.public import AcceptedInput, SessionConsumers, SessionService
from app.modules.tool.public import (
    AgentToolResolutionScope,
    CallScope,
    ExecutorBinding,
    PersonalAccountSelection,
    ToolResolutionScope,
)
from app.modules.workspace.public import WorkspaceScope, WorkspaceSubject

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class SessionIntake:
    accepted: AcceptedInput
    run: RunView | None
    error: str | None = None


class ProductInputs:
    def __init__(self, database: DatabaseResources, execution: ExecutionResources,
                 fallback: OutcomeConsumer | None = None) -> None:
        self.database, self.execution, self.fallback = database, execution, fallback
        self.runtime: RunRuntime | None = None
        self.streams = SessionExecutionStreams(database)
        self.session = SessionConsumers()
        self.attachments = AttachmentInputs(database, execution)
        if execution.temp_files is None:
            raise RuntimeError("A2A temporary file storage is unavailable")
        self.a2a_files = A2ATempFiles(database, execution.temp_files, execution.workspace, self.attachments.read_for_run)
        self.documents = DocumentParser()
        self._preview_slots = asyncio.Semaphore(2)
        self.other = OtherProductInputs(database, execution)
        self.other.attachment_authorizer = self.attachments.authorize_source_reference
        self.other.attachment_binder = self.attachments.bind_group
        self.goal = GoalInputs(database, execution)
        self.scheduled = ScheduledInputs(database, execution)
        self.other.message_hook = self._incoming_message
        self.other.agent_message_hook = self._incoming_agent_message

    async def _incoming_agent_message(self, message_id: UUID, input: InputContent, workspace: WorkspaceScope,
            agent_id: UUID) -> None:
        try:
            async with transaction(self.database.control_sessions) as tx:
                visibility = await A2AService(tx).input_visibility(tenant_id=workspace.tenant_id, request_id=message_id,
                    resolve_product_origin=self._scheduled_visibility)
            await self.scheduled.on_message(message_id=message_id, input=input, workspace=workspace,
                source_agent_id=agent_id, origin_override=visibility.subject,
                origin_conversation_id=visibility.conversation_id)
        except (DomainError, SQLAlchemyError) as error:
            self.scheduled.errors += 1
            logger.warning("Committed A2A message Trigger dispatch failed: %s", type(error).__name__)

    async def _scheduled_visibility(self, transaction: TransactionContext, *, run: RunView) -> A2AInputVisibility | None:
        if run.source.kind not in ("trigger", "heartbeat"):
            return None
        subject, topic = await self.scheduled.execution_origin(transaction, run)
        return A2AInputVisibility(subject, topic)

    async def _incoming_message(self, message_id: UUID, input: InputContent, workspace: WorkspaceScope,
            membership_id: UUID) -> None:
        try:
            await self.scheduled.on_message(message_id=message_id, input=input, workspace=workspace,
                source_membership_id=membership_id)
        except (DomainError, SQLAlchemyError) as error:
            self.scheduled.errors += 1
            logger.warning("Committed message Trigger dispatch failed: %s", type(error).__name__)

    async def _session_result(self, principal: TenantPrincipal, result: SessionIntake) -> SessionIntake:
        await self.after_session_input(principal, result.accepted)
        return result

    async def after_session_input(self, principal: TenantPrincipal, accepted: AcceptedInput) -> None:
        """Notify subscriptions only after this authenticated Session input commits."""
        entry = accepted.entry
        scope = WorkspaceScope(principal.tenant_id, entry.agent_id,
            WorkspaceSubject("membership", principal.membership_id), uuid4(), allow_shared_memory_writes=False)
        await self._incoming_message(entry.id, entry.content, scope, principal.membership_id)

    async def after_group_answer(self, principal: TenantPrincipal, accepted: AcceptedGroupInput,
            *, agent_id: UUID) -> None:
        """An explicit Waiting answer targets only its existing Run's Agent."""
        scope = WorkspaceScope(principal.tenant_id, agent_id,
            WorkspaceSubject("group", accepted.event.group_id), uuid4(), allow_shared_memory_writes=False)
        await self._incoming_message(accepted.event.id, accepted.event.input, scope, principal.membership_id)

    async def bindings(self, snapshot: RunSnapshot, step_id: str) -> tuple[ExecutorBinding, ...]:
        files = (attachment_preview_binding(self.attachments.read_for_run, cpu_slots=self._preview_slots),
            attachment_save_binding(self.attachments.save_for_run),
            document_tool_binding(self._read_document, parser=self.documents),
            temp_file_binding(snapshot, step_id, self.a2a_files))
        if snapshot.role != "main" or snapshot.workspace.run_id is None or self.runtime is None:
            return files
        accounts = ()
        if any(tool.credential is not None and tool.credential.owner_kind == "membership" for tool in snapshot.tools.tools):
            async with transaction(self.database.control_sessions) as tx:
                run = await RunService(tx).get(tenant_id=snapshot.tenant_id, run_id=snapshot.workspace.run_id)
                if run.source.kind == "session":
                    accounts = await SessionService(tx).execution_accounts(run, target_agent_id=run.agent_id)
                elif run.source.kind == "group":
                    accounts = await GroupService(tx).execution_accounts(run, target_agent_id=run.agent_id)
        schedule_scope = AgentToolResolutionScope(snapshot.tenant_id, snapshot.agent_id, "main", frozenset(accounts), accounts)
        return (*files, *schedule_tool_bindings(inputs=self.scheduled, scope=schedule_scope, run_id=snapshot.workspace.run_id, step_id=step_id),
            MessageExecutor(snapshot, step_id, self.send_message).binding(), A2AExecutor(snapshot, step_id, self.other).binding(), *session_tool_bindings(
            sessions=self.database.execution_sessions,
            scope=CallScope(snapshot.tenant_id, snapshot.agent_id, snapshot.workspace.run_id), step_id=step_id,
            runtime=self.runtime, outcome_consumer=self, role=snapshot.role))

    async def _read_document(self, scope: CallScope, *, reference: str) -> AttachmentBlob:
        if reference.startswith("temporary:"):
            data, metadata = await self.a2a_files.read(scope, name=reference.removeprefix("temporary:"))
            return AttachmentBlob(metadata.name, metadata.media_type, data)
        return await self.attachments.read_document_source(scope, reference=reference)

    async def send_message(self, snapshot: RunSnapshot, step_id: str, call_id: str, input: InputContent,
            files: tuple[WorkspaceMessageFile, ...] = ()) -> dict[str, object]:
        if snapshot.role != "main" or snapshot.workspace.run_id is None:
            raise AccessDenied("This execution cannot send conversation messages")
        async with transaction(self.database.execution_sessions) as tx:
            run = await RunService(tx).get(tenant_id=snapshot.tenant_id, run_id=snapshot.workspace.run_id)
            existing = None
            if run.source.kind == "session":
                existing = await SessionService(tx).find_accepted_message(run=run, step_id=step_id, call_id=call_id)
            elif run.source.kind == "group":
                existing = await GroupService(tx).find_accepted_message(run=run, step_id=step_id, call_id=call_id)
            if existing is not None:
                return {"accepted": True, "message_id": str(existing.id), "position": existing.position}
        if run.source.kind not in ("session", "group"):
            return await self._send_scheduled_message(run, step_id, call_id, input, files)
        input = await self.attachments.prepare_message(run=run, step_id=step_id, call_id=call_id, input=input, files=files)
        async with transaction(self.database.execution_sessions) as tx:
            if run.source.kind == "group":
                message = await GroupService(tx).accept_message(tenant_id=run.tenant_id, run_id=run.id,
                    step_id=step_id, call_id=call_id, input=input)
                message_id, position = message.id, message.position
            else:
                accepted = await SessionService(tx).accept_message(run=run, step_id=step_id, call_id=call_id, input=input)
                message_id, position = accepted.entry.id, accepted.entry.position
            await self.attachments.bind_message(tx, run=run, step_id=step_id, call_id=call_id, message_id=message_id, input=input)
        return {"accepted": True, "message_id": str(message_id), "position": position}

    async def _authorize_scheduled_message(self, transaction: TransactionContext, *, run: RunView,
            target_id: UUID, conversation_id: UUID | None, input: InputContent) -> None:
        destination = await self.scheduled.execution_destination(transaction, run)
        if destination is None or (destination.id, destination.conversation_id) != (target_id, conversation_id):
            raise AccessDenied("Message destination differs from the accepted occurrence")
        if destination.origin_kind == "agent" and destination.origin_id != run.agent_id:
            raise AccessDenied("Another Agent's input cannot be published to an unverified audience")
        if destination.kind == "session":
            target = await SessionService(transaction).delivery_membership(tenant_id=run.tenant_id,
                agent_id=run.agent_id, session_id=target_id)
            if destination.origin_kind == "group" or (destination.origin_kind == "membership" and destination.origin_id != target):
                raise AccessDenied("Private scheduled input cannot be forwarded to another destination")
        else:
            if destination.origin_kind == "membership" or (destination.origin_kind == "group" and (
                    destination.origin_id != target_id or destination.origin_conversation_id not in (None, conversation_id))):
                raise AccessDenied("Private scheduled input cannot be forwarded to another Group conversation")

    async def _send_scheduled_message(self, run: RunView, step_id: str, call_id: str, input: InputContent,
            files: tuple[WorkspaceMessageFile, ...]) -> dict[str, object]:
        if run.source.kind not in ("trigger", "heartbeat"):
            raise AccessDenied("This Run has no direct conversation message destination")
        async with transaction(self.database.control_sessions) as tx:
            destination = await self.scheduled.execution_destination(tx, run)
            if destination is None:
                raise AccessDenied("No message destination was configured; retain the result in execution history")
            await self._authorize_scheduled_message(tx, run=run, target_id=destination.id,
                conversation_id=destination.conversation_id, input=input)
            if destination.kind == "session":
                existing = await SessionService(tx).find_external_message(run=run, session_id=destination.id,
                    step_id=step_id, call_id=call_id, authorize=self._authorize_scheduled_message)
            else:
                assert destination.conversation_id is not None
                existing = await GroupService(tx).find_external_message(run=run, group_id=destination.id,
                    conversation_id=destination.conversation_id, step_id=step_id, call_id=call_id,
                    authorize=self._authorize_scheduled_message)
            if existing is not None:
                return {"accepted": True, "message_id": str(existing.id), "position": existing.position}
        target = (destination.kind, destination.id, destination.conversation_id)
        content = await self.attachments.prepare_message(run=run, step_id=step_id, call_id=call_id, input=input,
            files=files, destination=target, authorize=self._authorize_scheduled_message)
        async with transaction(self.database.execution_sessions) as tx:
            if destination.kind == "session":
                accepted = await SessionService(tx).accept_external_message(run=run, session_id=destination.id,
                    step_id=step_id, call_id=call_id, input=content, authorize=self._authorize_scheduled_message)
                message_id, position = accepted.entry.id, accepted.entry.position
            else:
                assert destination.conversation_id is not None
                message = await GroupService(tx).accept_external_message(run=run, group_id=destination.id,
                    conversation_id=destination.conversation_id, step_id=step_id, call_id=call_id,
                    input=content, authorize=self._authorize_scheduled_message)
                message_id, position = message.id, message.position
            await self.attachments.bind_message(tx, run=run, step_id=step_id, call_id=call_id,
                message_id=message_id, input=content, destination=target)
        return {"accepted": True, "message_id": str(message_id), "position": position}

    async def record_started(self, transaction: TransactionContext, *, run: RunView) -> None:
        if run.source.kind == "session":
            await self.session.record_started(transaction, run=run)
        elif run.source.kind in ("a2a", "group"):
            await self.other.record_started(transaction, run=run)
        elif run.source.kind in ("trigger", "heartbeat"):
            await self.scheduled.record_started(transaction, run=run)

    async def record_waiting(self, transaction: TransactionContext, *, run: RunView, waiting: WaitingPayload) -> None:
        if run.source.kind == "session":
            await self.session.record_waiting(transaction, run=run, waiting=waiting)
        elif run.source.kind in ("a2a", "group"):
            await self.other.record_waiting(transaction, run=run, waiting=waiting)

    async def record_outcome(self, transaction: TransactionContext, *, run: RunView, outcome: TerminalOutcomePayload) -> None:
        if run.source.kind == "session":
            await self.session.record_outcome(transaction, run=run, outcome=outcome)
        elif run.source.kind in ("a2a", "group"):
            await self.other.record_outcome(transaction, run=run, outcome=outcome)
        elif run.source.kind in ("trigger", "heartbeat"):
            await self.scheduled.record_outcome(transaction, run=run, outcome=outcome)
        elif self.fallback is not None:
            await self.fallback.record_outcome(transaction, run=run, outcome=outcome)

    async def submit_session(self, principal: TenantPrincipal, *, session_id: UUID,
                             source_key: str, input: InputContent, reply_to_run_id: UUID | None = None,
                             waiting_reference: str | None = None,
                             account_selections: tuple[PersonalAccountSelection, ...] = (),
                             accepted_consumer: Callable[[TransactionContext, AcceptedInput], Awaitable[None]] | None = None) -> SessionIntake:
        if self.runtime is None:
            raise RuntimeError("Product Runtime is not ready")
        async with transaction(self.database.control_sessions) as tx:
            service = SessionService(tx, enabled_sources=self.execution.market.enabled_source_ids)
            accepted = await service.accept_input(principal, session_id=session_id, source_key=source_key,
                input=input, reply_to_run_id=reply_to_run_id, waiting_reference=waiting_reference,
                account_selections=account_selections)
            await self.attachments.bind_session(tx, principal, accepted)
            if accepted.link is not None and accepted.link.run_id is None and accepted.entry.content.text.startswith("/goal "):
                await service.enable_goal(principal, session_id=session_id, input_id=accepted.entry.id,
                    objective=accepted.entry.content.text[len("/goal "):].strip())
            if accepted_consumer is not None:
                await accepted_consumer(tx, accepted)
        entry, link = accepted.entry, accepted.link
        if link is None:
            assert entry.related_waiting_run_id is not None
            try:
                changed = await self.runtime.input(tenant_id=principal.tenant_id, run_id=entry.related_waiting_run_id,
                    input=entry.content, source=SourceIdentity("session_reply", session_id, str(entry.id)),
                    waiting_reference=entry.waiting_reference)
            except DomainError as error:
                return await self._session_result(principal, SessionIntake(accepted, None, error.code))
            return await self._session_result(principal, SessionIntake(accepted, changed.run))
        if link.run_id is not None:
            async with transaction(self.database.control_sessions) as tx:
                run = await RunService(tx).get(tenant_id=principal.tenant_id, run_id=link.run_id)
            return await self._session_result(principal, SessionIntake(accepted, run))
        try:
            async with transaction(self.database.control_sessions) as tx:
                agent = await AgentService(tx).get_for_execution(principal, agent_id=entry.agent_id)
                history = await SessionService(tx).read_context_history(principal, session_id=session_id,
                    through_position=link.history_cutoff - 1)
                goal = await SessionService(tx).get_goal(principal, session_id=session_id, expected_input_id=entry.id)
                accounts = await SessionService(tx).input_accounts(principal, session_id=session_id,
                    input_id=entry.id, target_agent_id=entry.agent_id)
            model = await self.execution.model.resolve_configured_policy(tenant_id=principal.tenant_id,
                model_id=agent.model_id)
            scope = await self.execution.workspace.direct_scope(principal, agent_id=agent.id, run_id=uuid4())
            await self.execution.workspace.ensure(scope, scope.output)
            await self.execution.workspace.ensure(scope, WorkspaceSubject("agent", agent.id))
            snapshot = await capture_snapshot(self.execution, self.database, agent=agent, model=model,
                workspace=scope, tools=ToolResolutionScope(principal, agent.id, "main", frozenset(accounts), accounts))
            context = "\n".join(f"[Session {item.kind} at position {item.position}]\n" +
                (item.content.text + "".join(f"\nReference: {ref.reference}" for ref in item.content.references)
                    if item.content is not None else f"[Read entry {item.id} through Session history]")
                for item in history.entries)
            if context:
                snapshot = replace(snapshot, sources=snapshot.sources + (SourceSection("product_context", scope.output,
                    f"session:{session_id}:through:{link.history_cutoff - 1}", context),))
            if goal is not None and goal.enabled and goal.input_id == entry.id:
                snapshot = replace(snapshot, sources=snapshot.sources + (SourceSection("product_context", scope.output,
                    f"goal:{entry.id}:link:{link.id}", goal_instructions(goal)),))
            started = await self.runtime.start(snapshot=snapshot, input=entry.content,
                source=SourceIdentity("session", session_id, str(link.id)))
        except DomainError as error:
            async with transaction(self.database.control_sessions) as tx:
                await SessionService(tx).admission_failed(principal, session_id=session_id,
                    link_id=link.id, reason=error.code)
            return await self._session_result(principal, SessionIntake(accepted, None, error.code))
        return await self._session_result(principal, SessionIntake(accepted, started.run))
