"""A2A requests and delivery receipts; execution belongs to Run."""

import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from hashlib import sha256
from typing import Literal, Protocol, cast
from uuid import UUID, uuid4

from sqlalchemy import select

from app.infrastructure.errors import AccessDenied, Conflict, InvalidInput, NotFound
from app.infrastructure.transactions import TransactionContext
from app.modules.a2a.models import A2ARequestRecord
from app.modules.a2a.temp_files import (
    A2ATempFileService,
    A2ATempStorage,
    Publication,
    ReturnedFile,
    SaveReceipt,
    TempFilePlan,
    TempFileView,
    TempStoredFile,
)
from app.modules.group.public import GroupService
from app.modules.permission.public import PermissionService
from app.modules.run.public import (
    HistoryFragment,
    InputContent,
    InputReference,
    RunService,
    RunView,
    SourceIdentity,
    TerminalOutcomePayload,
    TransitionResult,
    WaitingPayload,
)
from app.modules.session.public import SessionService
from app.modules.tool.public import PersonalAccountSelection, decode_personal_selections, encode_personal_selections
from app.modules.workspace.public import WorkspaceSubject

A2AIntent = Literal["notify", "consult", "task_delegate"]
__all__ = ["A2ADeliveryState", "A2AInputVisibility", "A2AInputVisibilityResolver", "A2AIntent", "A2ARequestView", "A2AService", "A2ATempFileService", "A2ATempStorage",
    "AttachmentSourceAuthorizer", "Publication", "ReturnedFile", "SaveReceipt", "TempFilePlan", "TempFileView", "TempStoredFile"]
_INTENTS = ("notify", "consult", "task_delegate")
_DELIVERY = ("not_required", "awaiting_result", "pending", "accepted", "source_terminal")


class AttachmentSourceAuthorizer(Protocol):
    async def __call__(self, transaction: TransactionContext, *, run: RunView, reference: str) -> None: ...


@dataclass(frozen=True, slots=True)
class A2AInputVisibility:
    """Source visibility metadata, never a grant to access the source Workspace."""

    subject: WorkspaceSubject
    conversation_id: UUID | None = None


class A2AInputVisibilityResolver(Protocol):
    async def __call__(self, transaction: TransactionContext, *, run: RunView) -> A2AInputVisibility | None: ...


@dataclass(frozen=True, slots=True)
class A2ARequestView:
    id: UUID
    tenant_id: UUID
    source_agent_id: UUID
    source_run_id: UUID
    source_call_id: str
    target_agent_id: UUID
    target_run_id: UUID | None
    intent: A2AIntent
    input: InputContent
    admission: str
    admission_error: str | None
    result: dict[str, object] | None
    source_delivery: str
    delegated_connection_ids: tuple[UUID, ...] = ()
    delivery_run_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class A2ADeliveryState:
    request_id: UUID
    source_delivery: Literal["not_required", "awaiting_result", "pending", "accepted", "source_terminal"]
    result_kind: Literal["needs_input", "terminal", "admission_failed"] | None


def _input(value: object) -> InputContent:
    if not isinstance(value, dict) or set(value) != {"text", "references"}:
        raise InvalidInput("A2A input has an unsupported shape")
    text, references = value["text"], value["references"]
    if not isinstance(text, str) or not isinstance(references, (list, tuple)) or len(references) > 100:
        raise InvalidInput("A2A input is invalid")
    parsed = []
    for reference in references:
        if not isinstance(reference, dict) or set(reference) != {"reference", "name", "media_type"}:
            raise InvalidInput("A2A reference is invalid")
        if not isinstance(reference["reference"], str) or not reference["reference"]:
            raise InvalidInput("A2A reference requires an identity")
        if any(v is not None and not isinstance(v, str) for v in reference.values()):
            raise InvalidInput("A2A reference fields are invalid")
        parsed.append(InputReference(**reference))
    if len(json.dumps(value, ensure_ascii=False).encode()) > 256 * 1024:
        raise InvalidInput("A2A input exceeds its byte bound")
    return InputContent(text, tuple(parsed))


def _view(row: A2ARequestRecord) -> A2ARequestView:
    if (row.payload_version != 1 or row.delegation_version != 1 or row.result_version != 1
            or row.intent not in _INTENTS or row.source_delivery not in _DELIVERY
            or row.admission not in ("pending", "started", "failed")):
        raise InvalidInput("A2A request has an unsupported persisted format")
    if not isinstance(row.payload, dict) or set(row.payload) != {"input", "step_id", "call_id"} or not all(
            isinstance(row.payload[key], str) and 0 < len(row.payload[key]) <= 256 for key in ("step_id", "call_id")):
        raise InvalidInput("A2A source correlation is invalid")
    if row.result is not None:
        if not isinstance(row.result, dict):
            raise InvalidInput("A2A result must be an object")
        expected = ({"kind", "text", "waiting_reference", "delivery_key", "history_sequence"} if row.result.get("kind") == "needs_input"
            else {"kind", "status", "text", "delivery_key"} if row.result.get("kind") == "admission_failed"
            else {"kind", "status", "text", "delivery_key", "run_id", "history_sequence"})
        if set(row.result) != expected or not all(isinstance(v, str) for k, v in row.result.items() if k != "history_sequence"):
            raise InvalidInput("A2A result has an unsupported shape")
        if "history_sequence" in expected and (type(row.result["history_sequence"]) is not int or row.result["history_sequence"] < 1):
            raise InvalidInput("A2A result History reference is invalid")
        if row.result["kind"] not in ("needs_input", "terminal", "admission_failed") or len(json.dumps(row.result).encode()) > 128 * 1024:
            raise InvalidInput("A2A result exceeds its supported format")
        if row.result["kind"] == "terminal" and row.result["status"] not in ("Completed", "Failed", "Cancelled", "Interrupted"):
            raise InvalidInput("A2A outcome status is unsupported")
    delegated = decode_personal_selections({"version": row.delegation_version, "targets": row.delegated_connections})
    if len(delegated) > 1 or any(item.target_agent_id != row.target_agent_id for item in delegated):
        raise InvalidInput("A2A account delegation belongs to another target")
    return A2ARequestView(row.id, row.tenant_id, row.source_agent_id, row.source_run_id, row.source_call_id,
        row.target_agent_id, row.target_run_id, cast(A2AIntent, row.intent), _input(row.payload["input"]), row.admission,
        row.admission_error, json.loads(json.dumps(row.result)) if row.result is not None else None, row.source_delivery,
        delegated[0].connection_ids if delegated else (), row.delivery_run_id)


class A2AService:
    """Caller commits; target settlement never acquires the source Run lock."""

    def __init__(self, transaction: TransactionContext) -> None:
        self.tx = transaction
        self.session = transaction.session

    async def input_visibility(self, *, tenant_id: UUID, request_id: UUID,
            resolve_product_origin: A2AInputVisibilityResolver | None = None) -> A2AInputVisibility:
        """Trace immutable source metadata, bounded to sixteen requests without public fallback."""
        seen: set[UUID] = set()
        runs = RunService(self.tx)
        for _ in range(16):
            if request_id in seen:
                raise InvalidInput("A2A visibility source contains a cycle")
            seen.add(request_id)
            row = await self._row(tenant_id, request_id)
            _view(row)
            source = await runs.get(tenant_id=tenant_id, run_id=row.source_run_id)
            if source.parent_run_id is not None or source.agent_id != row.source_agent_id:
                raise InvalidInput("A2A visibility source is not its recorded Main")
            snapshot = await runs.read_snapshot(tenant_id=tenant_id, run_id=source.id)
            if source.source.kind not in ("session", "group", "a2a") and resolve_product_origin is not None:
                resolved = await resolve_product_origin(self.tx, run=source)
                if resolved is not None:
                    return resolved
            output = snapshot.workspace.output
            if output.kind == "membership":
                return A2AInputVisibility(output)
            if output.kind == "group":
                conversation_id = None
                if source.source.kind == "group":
                    group_id, conversation_id = await GroupService(self.tx).execution_conversation(source)
                    if group_id != output.id:
                        raise InvalidInput("A2A source Group differs from its captured output")
                return A2AInputVisibility(output, conversation_id)
            members = {tool.credential.owner_id for tool in snapshot.tools.tools
                if tool.credential is not None and tool.credential.owner_kind == "membership"}
            if len(members) > 1:
                raise InvalidInput("A2A private input has multiple Membership visibility owners")
            if members:
                return A2AInputVisibility(WorkspaceSubject("membership", next(iter(members))))
            if source.source.kind != "a2a":
                if not snapshot.workspace.allow_shared_memory_writes or not snapshot.workspace.allow_shared_file_writes:
                    raise AccessDenied("Private A2A input origin requires an authorized product resolver")
                return A2AInputVisibility(WorkspaceSubject("agent", source.agent_id))
            parent = await self._row(tenant_id, source.source.owner_id)
            if parent.target_run_id != source.id or parent.target_agent_id != source.agent_id:
                raise InvalidInput("A2A visibility ancestry does not match its receiver")
            request_id = parent.id
        raise InvalidInput("A2A input visibility exceeds sixteen request hops")

    async def accept(self, *, tenant_id: UUID, source_run_id: UUID, step_id: str, call_id: str,
            target_agent_id: UUID, intent: A2AIntent, input: InputContent,
            delegated_connection_ids: tuple[UUID, ...] = (),
            attachment_authorizer: AttachmentSourceAuthorizer | None = None) -> A2ARequestView:
        """Delegated IDs are resolved by trusted product intake from the source's original human input, never model arguments."""
        if intent not in _INTENTS or not all(isinstance(key, str) and 0 < len(key) <= 256 for key in (step_id, call_id)):
            raise InvalidInput("A2A intent or call identity is invalid")
        _input(asdict(input))
        payload = {"input": asdict(input), "step_id": step_id, "call_id": call_id}
        source_call_id = sha256(f"{step_id}\0{call_id}".encode()).hexdigest()
        source = await RunService(self.tx).lock_main(tenant_id=tenant_id, run_id=source_run_id)
        existing = await self.session.scalar(select(A2ARequestRecord).where(
            A2ARequestRecord.tenant_id == tenant_id, A2ARequestRecord.source_run_id == source_run_id,
            A2ARequestRecord.source_call_id == source_call_id))
        if existing is not None:
            return _view(existing)
        if source.status not in ("Running", "Waiting"):
            raise Conflict("A2A source execution is terminal")
        await RunService(self.tx).verify_main_tool_origin(tenant_id=tenant_id, run_id=source_run_id,
            step_id=step_id, call_id=call_id, tool_name="send_message_to_agent")
        attachment_refs = tuple(dict.fromkeys(item.reference for item in input.references if item.reference.startswith("attachment:")))
        if len(attachment_refs) > 64:
            raise InvalidInput("A2A attachment selection exceeds its bound")
        if attachment_refs and attachment_authorizer is None:
            raise AccessDenied("A2A file delegation requires source attachment authorization")
        for reference in attachment_refs:
            assert attachment_authorizer is not None
            await attachment_authorizer(self.tx, run=source, reference=reference)
        if source.agent_id == target_agent_id:
            raise InvalidInput("Use the current Agent's Task tool for same-Agent work")
        await PermissionService(self.tx).require_autonomous_access(tenant_id=tenant_id,
            source_agent_id=source.agent_id, target_agent_id=target_agent_id)
        now = datetime.now(UTC)
        row = A2ARequestRecord(id=uuid4(), tenant_id=tenant_id, source_agent_id=source.agent_id,
            source_run_id=source_run_id, source_call_id=source_call_id, target_agent_id=target_agent_id,
            target_run_id=None, intent=intent, payload_version=1, payload=payload, delegation_version=1,
            delegated_connections=encode_personal_selections((PersonalAccountSelection(target_agent_id, delegated_connection_ids),)
                if delegated_connection_ids else ())["targets"], admission="pending", admission_error=None, result_version=1, result=None,
            source_delivery="not_required" if intent == "notify" else "awaiting_result", created_at=now, updated_at=now)
        self.session.add(row)
        await self.session.flush()
        return _view(row)

    async def get(self, *, tenant_id: UUID, request_id: UUID) -> A2ARequestView:
        return _view(await self._row(tenant_id, request_id))

    async def authorize_attachment_reference(self, transaction: TransactionContext, *, run: RunView, reference: str) -> None:
        """Only the accepted request's explicit file subset is delegated to its target Main."""
        if run.parent_run_id is not None or run.source.kind != "a2a":
            raise AccessDenied("Attachment delegation requires an A2A target Main")
        request = await A2AService(transaction).get(tenant_id=run.tenant_id, request_id=run.source.owner_id)
        if (request.target_run_id, request.target_agent_id) != (run.id, run.agent_id):
            raise AccessDenied("Attachment was not explicitly delegated to this A2A execution")
        if any(item.reference == reference for item in request.input.references):
            return
        if not await RunService(transaction).has_input_reference(tenant_id=run.tenant_id, run_id=run.id,
                reference=reference, source_kind="a2a_answer", source_owner_id=request.id):
            raise AccessDenied("Attachment was not explicitly delegated to this A2A execution")

    async def delivery_states(self, *, tenant_id: UUID, request_ids: tuple[UUID, ...]) -> tuple[A2ADeliveryState, ...]:
        """Bounded metadata-only lookup; delivery polling never loads request input or output bodies."""
        if not isinstance(request_ids, tuple) or len(request_ids) > 100 or len(set(request_ids)) != len(request_ids):
            raise InvalidInput("A2A delivery metadata batch is invalid")
        if not request_ids:
            return ()
        rows = (await self.session.execute(select(A2ARequestRecord.id, A2ARequestRecord.source_delivery,
            A2ARequestRecord.result["kind"].as_string()).where(A2ARequestRecord.tenant_id == tenant_id,
                A2ARequestRecord.id.in_(request_ids)))).all()
        if len(rows) != len(request_ids):
            raise NotFound("A2A delivery request is unavailable in this Tenant")
        states = []
        for request_id, delivery, kind in rows:
            if delivery not in _DELIVERY or kind not in (None, "needs_input", "terminal", "admission_failed"):
                raise InvalidInput("A2A delivery metadata has an unsupported value")
            states.append(A2ADeliveryState(request_id, delivery, kind))
        return tuple(states)

    async def read_result(self, *, tenant_id: UUID, source_run_id: UUID, request_id: UUID,
            content_offset: int = 0) -> HistoryFragment | None:
        """Read only the associated target's result/wait fact, never its private execution History."""
        row = await self._row(tenant_id, request_id)
        await self._authorize_source(row, source_run_id)
        if row.target_run_id is None or row.result is None:
            return None
        if row.result["kind"] == "admission_failed":
            return None
        fragment = await RunService(self.tx).read_history_fragment(tenant_id=tenant_id, run_id=row.target_run_id,
            after_sequence=row.result["history_sequence"] - 1, content_offset=content_offset)
        expected = "waiting" if row.result["kind"] == "needs_input" else "terminal_outcome"
        if fragment is None or fragment.kind != expected:
            raise InvalidInput("A2A result does not reference the expected Run fact")
        return fragment

    async def _authorize_source(self, row: A2ARequestRecord, caller_run_id: UUID) -> RunView:
        runs = RunService(self.tx)
        caller = await runs.get(tenant_id=row.tenant_id, run_id=caller_run_id)
        if caller.parent_run_id is not None or caller.agent_id != row.source_agent_id:
            raise AccessDenied("A2A access requires the source Agent's Main")
        if caller.id == row.source_run_id:
            return caller
        original = await runs.get(tenant_id=row.tenant_id, run_id=row.source_run_id)
        if caller.source.kind == original.source.kind == "session":
            owner = SessionService(self.tx)
            previous = await owner.get_execution_context(original)
            current = await owner.get_execution_context(caller)
            if previous.session.id == current.session.id:
                return caller
        elif caller.source.kind == original.source.kind == "group":
            owner = GroupService(self.tx)
            if await owner.execution_conversation(original) == await owner.execution_conversation(caller):
                return caller
        raise AccessDenied("A2A request belongs to another conversation source")

    async def takeover(self, *, tenant_id: UUID, request_id: UUID, source_run_id: UUID,
            step_id: str, call_id: str) -> A2ARequestView:
        """Explicitly choose a same-conversation Main after the previous recipient ended."""
        observed = await self._row(tenant_id, request_id)
        recipient_id = observed.delivery_run_id or observed.source_run_id
        runs = RunService(self.tx)
        locked: dict[UUID, RunView] = {}
        for run_id in sorted({source_run_id, recipient_id}):
            locked[run_id] = await runs.lock_main(tenant_id=tenant_id, run_id=run_id)
        return await self._takeover_locked(observed, locked[recipient_id], source_run_id, step_id, call_id)

    async def _takeover_locked(self, observed: A2ARequestRecord, recipient: RunView,
            source_run_id: UUID, step_id: str, call_id: str) -> A2ARequestView:
        await RunService(self.tx).verify_main_tool_origin(tenant_id=observed.tenant_id, run_id=source_run_id,
            step_id=step_id, call_id=call_id, tool_name="send_message_to_agent")
        await self._authorize_source(observed, source_run_id)
        row = await self._row(observed.tenant_id, observed.id, lock=True)
        if (row.delivery_run_id or row.source_run_id) != recipient.id:
            raise Conflict("A2A delivery recipient changed; inspect and retry")
        if recipient.id != source_run_id:
            if recipient.status in ("Running", "Waiting"):
                raise Conflict("An active Main already receives this A2A request")
            row.delivery_run_id = source_run_id
            if row.intent != "notify":
                row.source_delivery = "pending" if row.result is not None else "awaiting_result"
            row.updated_at = datetime.now(UTC)
            await self.session.flush()
        return _view(row)

    async def prepare_wait(self, *, tenant_id: UUID, request_id: UUID, source_run_id: UUID,
            step_id: str, call_id: str) -> tuple[A2ARequestView, bool, TransitionResult | None]:
        """Return a wait marker only for an unfinished request; caller settles Tools before Waiting."""
        observed = await self._row(tenant_id, request_id)
        if observed.intent == "notify":
            raise InvalidInput("One-way notification does not wait for a result")
        request = await self.takeover(tenant_id=tenant_id, request_id=request_id,
            source_run_id=source_run_id, step_id=step_id, call_id=call_id)
        ready = request.result is not None and request.source_delivery != "awaiting_result"
        if ready:
            changed = await self.deliver_pending(tenant_id=tenant_id, request_id=request_id)
            return request, False, changed
        return request, True, None

    async def mark_admission_failed(self, *, tenant_id: UUID, request_id: UUID, reason: str) -> A2ARequestView:
        if not reason or len(reason) > 512:
            raise InvalidInput("A2A admission reason is invalid")
        row = await self._row(tenant_id, request_id, lock=True)
        if row.admission == "pending":
            row.admission, row.admission_error = "failed", reason
            row.result = {"kind": "admission_failed", "status": "Failed", "text": reason, "delivery_key": "admission_failed"}
            if row.intent != "notify":
                row.source_delivery = "pending"
            row.updated_at = datetime.now(UTC)
            await self.session.flush()
        return _view(row)

    async def answer(self, *, tenant_id: UUID, request_id: UUID, source_run_id: UUID,
            step_id: str, call_id: str, waiting_reference: str, input: InputContent,
            attachment_authorizer: AttachmentSourceAuthorizer | None = None) -> TransitionResult:
        """Resume only this request's independent target; lock both Main Runs in UUID order."""
        _input(asdict(input))
        initial = await self._row(tenant_id, request_id)
        source = await self._authorize_source(initial, source_run_id)
        references = tuple(dict.fromkeys(item.reference for item in input.references if item.reference.startswith("attachment:")))
        if len(references) > 64:
            raise InvalidInput("A2A answer attachment selection exceeds its bound")
        if references and attachment_authorizer is None:
            raise AccessDenied("A2A answer files require source attachment authorization")
        for reference in references:
            assert attachment_authorizer is not None
            await attachment_authorizer(self.tx, run=source, reference=reference)
        if initial.target_run_id is None:
            raise AccessDenied("A2A target has not started")
        target_run_id = initial.target_run_id
        runs = RunService(self.tx)
        recipient_id = initial.delivery_run_id or initial.source_run_id
        locked: dict[UUID, RunView] = {}
        for run_id in sorted({source_run_id, target_run_id, recipient_id}):
            locked[run_id] = await runs.lock_main(tenant_id=tenant_id, run_id=run_id)
        await self._takeover_locked(initial, locked[recipient_id], source_run_id, step_id, call_id)
        row = await self._row(tenant_id, request_id, lock=True)
        if row.target_run_id != target_run_id:
            raise Conflict("A2A target association changed")
        key = sha256(f"{source_run_id}\0{step_id}\0{call_id}".encode()).hexdigest()
        changed = await runs.append_related(tenant_id=tenant_id, run_id=target_run_id, input=input,
            source=SourceIdentity("a2a_answer", request_id, key), waiting_reference=waiting_reference)
        if changed.changed and row.intent != "notify":
            row.source_delivery = "awaiting_result"
            row.result = None
            row.updated_at = datetime.now(UTC)
            await self.session.flush()
        return changed

    async def record_started(self, transaction: TransactionContext, *, run: RunView) -> None:
        owner = A2AService(transaction)
        row = await owner._for_run(run, starting=True)
        if row.target_run_id not in (None, run.id):
            raise Conflict("A2A request already has another execution")
        row.target_run_id, row.admission, row.admission_error = run.id, "started", None
        row.result = None
        row.source_delivery = "not_required" if row.intent == "notify" else "awaiting_result"
        row.updated_at = datetime.now(UTC)
        await transaction.session.flush()

    async def record_waiting(self, transaction: TransactionContext, *, run: RunView, waiting: WaitingPayload) -> None:
        row = await A2AService(transaction)._for_run(run)
        if row.intent == "notify":
            return
        question = waiting.question.encode()[:8192].decode(errors="ignore")
        if question != waiting.question:
            question += " [Question preview truncated; retrieve the target Run Waiting fact.]"
        row.result = {"kind": "needs_input", "text": question, "waiting_reference": waiting.reference,
            "delivery_key": f"waiting:{waiting.reference}", "history_sequence": run.latest_history_sequence}
        row.source_delivery, row.updated_at = "pending", datetime.now(UTC)
        await transaction.session.flush()

    async def record_outcome(self, transaction: TransactionContext, *, run: RunView,
            outcome: TerminalOutcomePayload) -> None:
        row = await A2AService(transaction)._for_run(run)
        text = outcome.output or outcome.reason or ""
        preview = text.encode()[:8192].decode(errors="ignore")
        if preview != text:
            preview += " [Preview truncated; retrieve the referenced Run outcome.]"
        row.result = {"kind": "terminal", "status": outcome.status, "text": preview,
            "delivery_key": "terminal", "run_id": str(run.id), "history_sequence": run.latest_history_sequence}
        if row.intent != "notify":
            row.source_delivery = "pending"
        row.updated_at = datetime.now(UTC)
        await transaction.session.flush()

    async def pending_deliveries(self, *, tenant_id: UUID, after_id: UUID | None = None,
            limit: int = 100) -> tuple[A2ARequestView, ...]:
        if type(limit) is not int or not 1 <= limit <= 100:
            raise InvalidInput("A2A delivery page is invalid")
        query = select(A2ARequestRecord).where(A2ARequestRecord.tenant_id == tenant_id,
            A2ARequestRecord.source_delivery == "pending")
        if after_id is not None:
            query = query.where(A2ARequestRecord.id > after_id)
        return tuple(_view(row) for row in (await self.session.scalars(query.order_by(A2ARequestRecord.id).limit(limit))).all())

    async def mark_delivery(self, *, tenant_id: UUID, request_id: UUID, delivery_key: str,
            source_terminal: bool, recipient_run_id: UUID | None = None) -> bool:
        """Call after source Run input acceptance, within that same transaction."""
        row = await self._row(tenant_id, request_id, lock=True)
        if (row.delivery_run_id or row.source_run_id) != (recipient_run_id or row.source_run_id):
            return False
        if row.source_delivery != "pending" or row.result is None or row.result.get("delivery_key") != delivery_key:
            return False
        row.source_delivery = "source_terminal" if source_terminal else "accepted"
        row.updated_at = datetime.now(UTC)
        await self.session.flush()
        return True

    async def deliver_pending(self, *, tenant_id: UUID, request_id: UUID) -> TransitionResult | None:
        """Use in a separate transaction after target settlement; schedule only after this commits."""
        observed = await self._row(tenant_id, request_id)
        recipient_id = observed.delivery_run_id or observed.source_run_id
        source = await RunService(self.tx).lock_main(tenant_id=tenant_id, run_id=recipient_id)
        row = await self._row(tenant_id, request_id, lock=True)
        if (row.delivery_run_id or row.source_run_id) != recipient_id:
            raise Conflict("A2A delivery recipient changed; retry delivery")
        if row.source_delivery != "pending" or row.result is None:
            return None
        key = row.result["delivery_key"]
        if source.status not in ("Running", "Waiting"):
            await self.mark_delivery(tenant_id=tenant_id, request_id=request_id, delivery_key=key,
                source_terminal=True, recipient_run_id=source.id)
            return None
        text = f"A2A {request_id} {row.result['kind']}: {row.result['text']}"
        if row.result["kind"] == "needs_input":
            text += f"\nWaiting reference: {row.result['waiting_reference']}"
        elif row.result["kind"] == "terminal":
            files = await A2ATempFileService(self.tx).returned_file_info(tenant_id=tenant_id, request_id=request_id)
            if files:
                text += "\nReturned files (use a2a_file with this request_id and name): " + json.dumps(
                    [asdict(file) for file in files], ensure_ascii=False, separators=(",", ":"))
        references = (InputReference(f"run:{row.target_run_id}"),) if row.target_run_id is not None else ()
        changed = await RunService(self.tx).append_related(tenant_id=tenant_id, run_id=source.id,
            input=InputContent(text, references),
            source=SourceIdentity("a2a_result", request_id, key))
        await self.mark_delivery(tenant_id=tenant_id, request_id=request_id, delivery_key=key,
            source_terminal=False, recipient_run_id=source.id)
        return changed

    async def returned_files_for_source(self, *, tenant_id: UUID, source_run_id: UUID,
            request_id: UUID) -> tuple[ReturnedFile, ...]:
        row = await self._row(tenant_id, request_id)
        await self._authorize_source(row, source_run_id)
        return await A2ATempFileService(self.tx).returned_file_info(tenant_id=tenant_id, request_id=request_id)

    async def _for_run(self, run: RunView, *, starting: bool = False) -> A2ARequestRecord:
        if run.parent_run_id is not None or run.source.kind != "a2a":
            raise AccessDenied("A2A callback requires its own Main execution")
        row = await self._row(run.tenant_id, run.source.owner_id, lock=True)
        if row.target_agent_id != run.agent_id or (not starting and row.target_run_id != run.id):
            raise AccessDenied("A2A execution does not match its request")
        return row

    async def _row(self, tenant: UUID, request: UUID, *, lock: bool = False) -> A2ARequestRecord:
        query = select(A2ARequestRecord).where(A2ARequestRecord.tenant_id == tenant, A2ARequestRecord.id == request)
        if lock:
            query = query.with_for_update()
        row = await self.session.scalar(query.execution_options(populate_existing=True))
        if row is None:
            raise NotFound("A2A request does not exist")
        _view(row)
        return row
