"""Session work controls over public owners; destinations and authority are injected."""

from hashlib import sha256
from typing import Literal
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.infrastructure.errors import AccessDenied, DomainError, InvalidInput
from app.infrastructure.transactions import transaction
from app.modules.run.public import (
    InputContent,
    InputReference,
    OutcomeConsumer,
    RunRuntime,
    RunService,
    RunView,
    SourceIdentity,
)
from app.modules.session.public import SessionHistoryFragment, SessionRunLink, SessionService
from app.modules.tool.public import (
    CallScope,
    DefinitionSpec,
    ExecutorBinding,
    ResolvedTool,
    ToolCall,
    ToolResult,
    canonical_json,
    json_object,
)

SESSION_TOOL_DEFINITIONS = (
    DefinitionSpec("session_history", "Read earlier messages at this work's fixed conversation cutoff. Continue a large entry with content_offset until next_offset is null, then use next_after_position.",
        canonical_json({"type": "object", "properties": {
            "after_position": {"type": "integer", "minimum": 0},
            "content_offset": {"type": "integer", "minimum": 0}}, "additionalProperties": False}),
        "session.history.v1", "builtin"),
    DefinitionSpec("session_work", "List accepted work or inspect, supplement, or cancel a started Main in this same conversation. Supplements are agent-prepared guidance associated with the original human input; acceptance does not mean completion.",
        canonical_json({"type": "object", "properties": {
            "action": {"type": "string", "enum": ["list", "inspect", "supplement", "cancel"]},
            "run_id": {"type": "string", "format": "uuid"},
            "after_id": {"type": "string", "format": "uuid"},
            "limit": {"type": "integer", "minimum": 1, "maximum": 100},
            "after_sequence": {"type": "integer", "minimum": 0},
            "content_offset": {"type": "integer", "minimum": 0},
            "text": {"type": "string", "minLength": 1, "maxLength": 8192}},
            "required": ["action"], "additionalProperties": False}), "session.work.v1", "builtin"),
)


def _fields(arguments: dict[str, object], required: set[str], optional: set[str]) -> None:
    if not required <= arguments.keys() or arguments.keys() - required - optional:
        raise InvalidInput("Tool arguments have missing or unsupported fields")


def _integer(arguments: dict[str, object], name: str, default: int, *, minimum: int = 0, maximum: int = 2**63 - 1) -> int:
    value = arguments.get(name, default)
    if type(value) is not int or not minimum <= value <= maximum:
        raise InvalidInput(f"{name} is outside its supported range")
    return value


def _uuid(value: object) -> UUID:
    if not isinstance(value, str):
        raise InvalidInput("A work identifier must be a UUID")
    try:
        return UUID(value)
    except ValueError:
        raise InvalidInput("A work identifier must be a UUID") from None


def _link(link: SessionRunLink) -> dict[str, object]:
    return {"association_id": str(link.id), "input_id": str(link.input_id),
        "run_id": str(link.run_id) if link.run_id else None, "admission": link.admission,
        "execution_status": link.result.status if link.result else None}


def _run(view: RunView) -> dict[str, object]:
    return {"run_id": str(view.id), "status": view.status,
        "waiting_reference": view.waiting_reference, "latest_history_sequence": view.latest_history_sequence}


def _history(fragment: SessionHistoryFragment | None) -> dict[str, object]:
    if fragment is None:
        return {"entry": None}
    return {"entry_id": str(fragment.entry_id), "position": fragment.position, "kind": fragment.kind,
        "content_json": fragment.content_json, "next_offset": fragment.next_offset,
        "next_after_position": fragment.next_after_position, "through_position": fragment.through_position}


class _SessionExecutor:
    def __init__(self, *, sessions: async_sessionmaker[AsyncSession], scope: CallScope, step_id: str,
            runtime: RunRuntime, outcome_consumer: OutcomeConsumer, definition: DefinitionSpec) -> None:
        self._sessions, self._scope, self._step_id = sessions, scope, step_id
        self._runtime, self._outcomes, self._definition = runtime, outcome_consumer, definition

    async def execute(self, tool: ResolvedTool, call: ToolCall, scope: CallScope) -> ToolResult:
        try:
            if scope != self._scope or tool.definition.tenant_id != scope.tenant_id or tool.definition.spec != self._definition or call.name != self._definition.name:
                raise AccessDenied("Session Tool binding does not match this execution")
            arguments = json_object(call.arguments_json)
            if call.name == "session_history":
                _fields(arguments, set(), {"after_position", "content_offset"})
                async with transaction(self._sessions) as tx:
                    run = await RunService(tx).verify_main_tool_origin(tenant_id=scope.tenant_id, run_id=scope.run_id,
                        step_id=self._step_id, call_id=call.id, tool_name=call.name)
                    if run.agent_id != scope.agent_id:
                        raise AccessDenied("Session Tool belongs to another Agent")
                    value = await SessionService(tx).read_execution_history_fragment(run,
                        after_position=_integer(arguments, "after_position", 0),
                        content_offset=_integer(arguments, "content_offset", 0, maximum=300000))
                    payload = _history(value)
            else:
                payload = await self._work(call, arguments)
            return ToolResult(call.id, "success", canonical_json(payload, maximum=250000))
        except DomainError as error:
            return ToolResult(call.id, "error", canonical_json({"code": error.code, "message": str(error)[:1024]}))

    async def _work(self, call: ToolCall, arguments: dict[str, object]) -> dict[str, object]:
        action = arguments.get("action")
        if action == "list":
            _fields(arguments, {"action"}, {"after_id", "limit"})
        elif action == "inspect":
            _fields(arguments, {"action", "run_id"}, {"after_sequence", "content_offset"})
        elif action == "supplement":
            _fields(arguments, {"action", "run_id", "text"}, set())
        elif action == "cancel":
            _fields(arguments, {"action", "run_id"}, set())
        else:
            raise InvalidInput("Session work action is unsupported")
        target_id = None if action == "list" else _uuid(arguments["run_id"])
        scope = self._scope
        changed = None
        async with transaction(self._sessions) as tx:
            runs, sessions = RunService(tx), SessionService(tx)
            # No product lock precedes this deterministic order of the two independent Mains.
            for run_id in sorted({scope.run_id} | ({target_id} if target_id is not None else set())):
                await runs.lock_main(tenant_id=scope.tenant_id, run_id=run_id)
            run = await runs.verify_main_tool_origin(tenant_id=scope.tenant_id, run_id=scope.run_id,
                step_id=self._step_id, call_id=call.id, tool_name=call.name)
            if run.agent_id != scope.agent_id:
                raise AccessDenied("Session Tool belongs to another Agent")
            context = await sessions.get_execution_context(run)
            if action == "list":
                page = await sessions.list_work_for_run(run,
                    after_id=_uuid(arguments["after_id"]) if "after_id" in arguments else None,
                    limit=_integer(arguments, "limit", 20, minimum=1, maximum=100))
                return {"work": [_link(item) for item in page.work], "has_more": page.has_more,
                    "next_after_id": str(page.next_after_id) if page.next_after_id else None}
            assert target_id is not None
            link = await sessions.authorize_work(run=run, target_run_id=target_id)
            target = await runs.get(tenant_id=scope.tenant_id, run_id=target_id)
            if action == "inspect":
                fragment = await runs.read_history_fragment(tenant_id=scope.tenant_id, run_id=target_id,
                    after_sequence=_integer(arguments, "after_sequence", 0),
                    content_offset=_integer(arguments, "content_offset", 0, maximum=17000000))
                return {"work": _link(link), "run": _run(target), "history": None if fragment is None else {
                    "sequence": fragment.sequence, "kind": fragment.kind, "version": fragment.version,
                    "content_json_fragment": fragment.content_json_fragment, "next_offset": fragment.next_offset,
                    "next_after_sequence": fragment.next_after_sequence, "through_sequence": fragment.through_sequence}}
            if action == "supplement":
                text = arguments["text"]
                if not isinstance(text, str) or not text.strip() or len(text) > 8192:
                    raise InvalidInput("Supplement text must contain 1 to 8192 characters")
                key = sha256(f"{run.id}\0{self._step_id}\0{call.id}\0{target_id}".encode()).hexdigest()
                content = InputContent("[Agent-prepared supplement associated with the original human input]\n" + text, (
                    InputReference(f"session:{context.session.id}:entry:{context.link.input_id}", "Original human input"),
                    InputReference(f"run:{run.id}:step:{self._step_id}:tool:{call.id}", "Supplement Tool origin")))
                changed = await runs.append_related(tenant_id=scope.tenant_id, run_id=target_id, input=content,
                    source=SourceIdentity("session_input", context.link.input_id, key))
            else:
                changed = await runs.terminate(tenant_id=scope.tenant_id, run_id=target_id, status="Cancelled",
                    reason="session_work_cancel", consumer=self._outcomes)
        await self._runtime.post_commit(changed)
        return {"accepted": True, "changed": changed.changed, "run": _run(changed.run)}


def session_tool_bindings(*, sessions: async_sessionmaker[AsyncSession], scope: CallScope, step_id: str,
        runtime: RunRuntime, outcome_consumer: OutcomeConsumer, role: Literal["main", "sub"]) -> tuple[ExecutorBinding, ...]:
    if role not in ("main", "sub"):
        raise InvalidInput("Session Tools require a valid Run role")
    if role == "sub":
        return ()
    return tuple(ExecutorBinding(definition.executor_key, _SessionExecutor(sessions=sessions, scope=scope,
        step_id=step_id, runtime=runtime, outcome_consumer=outcome_consumer, definition=definition), builtin=definition)
        for definition in SESSION_TOOL_DEFINITIONS)
