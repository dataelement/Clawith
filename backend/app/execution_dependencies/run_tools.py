"""Run-scoped orchestration adapters; lifecycle commits remain with Run."""

from collections.abc import Set as AbstractSet
from typing import Literal, Protocol
from uuid import UUID

from app.infrastructure.errors import AccessDenied, DomainError, InvalidInput
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


class TaskOperations(Protocol):
    """Trusted per-Run/per-Step owner ports; never wait for Child completion."""

    async def delegate(self, call_id: str, work: str) -> UUID: ...

    async def resume(self, call_id: str, childrun_id: UUID, waiting_reference: str, answer: str) -> None: ...

    async def inspect(self, childrun_id: UUID, after_sequence: int, content_offset: int) -> dict[str, object]: ...


def _definition(name: str, description: str, properties: dict[str, object], required: list[str]) -> DefinitionSpec:
    return DefinitionSpec(name, description, canonical_json({"type": "object", "properties": properties,
        "required": required, "additionalProperties": False}), f"run.{name}.v1", "builtin")


_TEXT = {"type": "string", "minLength": 1, "maxLength": 8192}
RUN_TOOL_DEFINITIONS = (
    _definition("task", "Delegate complex work while remaining available for conversation. Inspect reads a bounded JSON text fragment: continue with next_offset and the same after_sequence until next_offset is null, then use next_after_sequence. Resume answers a child's input request. Simple work can be done directly.", {
        "action": {"type": "string", "enum": ["delegate", "resume", "inspect"]},
        "work": _TEXT,
        "child_run_id": {"type": "string", "format": "uuid"},
        "waiting_reference": {"type": "string", "minLength": 1, "maxLength": 256},
        "answer": _TEXT,
        "after_sequence": {"type": "integer", "minimum": 0, "maximum": 9223372036854775807},
        "content_offset": {"type": "integer", "minimum": 0, "maximum": 17000000},
    }, ["action"]),
    _definition("todo", "Replace your current working plan. This list helps organize work; it does not complete the work for you.", {
        "items": {"type": "array", "maxItems": 64, "items": {"type": "object", "properties": {
            "text": {"type": "string", "minLength": 1, "maxLength": 512},
            "status": {"type": "string", "enum": ["pending", "in_progress", "completed"]}},
            "required": ["text", "status"], "additionalProperties": False}},
    }, ["items"]),
    _definition("need_input", "Ask for missing information only when available context and tools cannot resolve it. Your work waits for an answer without losing its context.", {"question": _TEXT}, ["question"]),
    _definition("wait_for_tasks", "Wait for delegated work when no useful independent work remains. New child information can resume your work.", {}, []),
)


def _text(arguments: dict[str, object], name: str, maximum: int = 8192) -> str:
    value = arguments.get(name)
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise InvalidInput(f"{name} requires nonempty bounded text")
    return value


def _fields(arguments: dict[str, object], required: set[str], optional: AbstractSet[str] = frozenset()) -> None:
    if not required <= arguments.keys() or arguments.keys() - required - optional:
        raise InvalidInput("Tool arguments have missing or unsupported fields")


def _integer(arguments: dict[str, object], name: str, default: int, minimum: int, maximum: int) -> int:
    value = arguments.get(name, default)
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise InvalidInput(f"{name} is outside its supported range")
    return value


class _RunExecutor:
    def __init__(self, definition: DefinitionSpec, scope: CallScope, role: Literal["main", "sub"],
                 operations: TaskOperations | None, allow_human_input: bool = True) -> None:
        self._definition, self._scope, self._role, self._operations = definition, scope, role, operations
        self._allow_human_input = allow_human_input

    async def execute(self, tool: ResolvedTool, call: ToolCall, scope: CallScope) -> ToolResult:
        try:
            if scope != self._scope or tool.definition.tenant_id != scope.tenant_id:
                raise AccessDenied("This Tool belongs to another execution")
            if tool.definition.spec != self._definition or call.name != self._definition.name:
                raise AccessDenied("Tool binding does not match its definition")
            name = self._definition.name
            if (name in ("task", "wait_for_tasks") and self._role != "main") or (name == "todo" and self._role != "sub"):
                raise AccessDenied("This Tool is unavailable for the current worker")
            arguments = json_object(call.arguments_json)
            if name == "task":
                payload = await self._task(call.id, arguments)
            elif name == "todo":
                _fields(arguments, {"items"})
                items = arguments["items"]
                if not isinstance(items, list) or len(items) > 64:
                    raise InvalidInput("Planning items must be a list of at most 64 entries")
                normalized: list[dict[str, str]] = []
                for item in items:
                    if not isinstance(item, dict) or set(item) != {"text", "status"}:
                        raise InvalidInput("Each planning item requires text and status")
                    text = item["text"]
                    status = item["status"]
                    if not isinstance(text, str) or not text.strip() or len(text) > 512:
                        raise InvalidInput("Planning text must contain 1 to 512 characters")
                    if status not in ("pending", "in_progress", "completed"):
                        raise InvalidInput("Planning status is unsupported")
                    normalized.append({"text": text, "status": status})
                payload = {"items": normalized}
            elif name == "need_input":
                if self._role == "main" and not self._allow_human_input:
                    raise AccessDenied("This unattended work cannot wait for human input; report the limitation and finish")
                _fields(arguments, {"question"})
                payload = {"need_input": True, "question": _text(arguments, "question")}
            else:
                _fields(arguments, set())
                payload = {"wait_for_tasks": True}
            return ToolResult(call.id, "success", canonical_json(payload, maximum=250000))
        except DomainError as error:
            return ToolResult(call.id, "error", canonical_json({"code": error.code, "message": str(error)[:1024]}))

    async def _task(self, call_id: str, arguments: dict[str, object]) -> dict[str, object]:
        if self._operations is None:
            raise RuntimeError("Main orchestration requires Task owner operations")
        action = arguments.get("action")
        if action == "delegate":
            _fields(arguments, {"action", "work"})
            child = await self._operations.delegate(call_id, _text(arguments, "work"))
            return {"accepted": True, "child_run_id": str(child)}
        if action not in ("resume", "inspect"):
            raise InvalidInput("Task action must be delegate, resume or inspect")
        if action == "resume":
            _fields(arguments, {"action", "child_run_id", "waiting_reference", "answer"})
        else:
            _fields(arguments, {"action", "child_run_id"}, {"after_sequence", "content_offset"})
        try:
            child = UUID(_text(arguments, "child_run_id", 36))
        except ValueError:
            raise InvalidInput("child_run_id must be a UUID") from None
        if action == "resume":
            await self._operations.resume(call_id, child, _text(arguments, "waiting_reference", 256), _text(arguments, "answer"))
            return {"resumed": True, "child_run_id": str(child)}
        return await self._operations.inspect(child,
            _integer(arguments, "after_sequence", 0, 0, 9223372036854775807),
            _integer(arguments, "content_offset", 0, 0, 17000000))


def run_tool_bindings(*, scope: CallScope, role: Literal["main", "sub"],
                      operations: TaskOperations | None = None, allow_human_input: bool = True) -> tuple[ExecutorBinding, ...]:
    """Compose fixed Run-local executors; publication and Waiting belong to the caller."""
    if role not in ("main", "sub") or (role == "main" and operations is None):
        raise InvalidInput("Run Tool bindings require a valid role and Main Task operations")
    return tuple(ExecutorBinding(definition.executor_key, _RunExecutor(definition, scope, role, operations, allow_human_input), builtin=definition)
        for definition in RUN_TOOL_DEFINITIONS
        if (definition.name != "todo" or role == "sub") and
           (definition.name not in ("task", "wait_for_tasks") or role == "main"))
