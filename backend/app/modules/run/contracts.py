"""Closed Run History payloads and bounded, lossless persistence codecs."""

import json
import math
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Annotated, Literal, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.infrastructure.errors import InvalidInput
from app.modules.model.public import ModelContent, ModelMessage, ModelStepResult, ModelToolCall, ModelUsage
from app.modules.tool.public import ToolResult

HISTORY_VERSION = 1
MAX_RECORD_BYTES = 16 * 1024 * 1024
MAX_INPUT_BYTES = 256 * 1024
MAX_DEPTH = 32
MAX_NODES = 100000
HistoryKind = Literal["initial_input", "related_input", "model_step", "tool_result", "waiting", "terminal_outcome", "context_base", "model_input"]
TerminalStatus = Literal["Completed", "Failed", "Cancelled", "Interrupted"]


class InvalidHistory(ValueError):
    """Authoritative History cannot be decoded; do not skip or substitute defaults."""


@dataclass(frozen=True, slots=True)
class InputReference:
    reference: str
    name: str | None = None
    media_type: str | None = None


@dataclass(frozen=True, slots=True)
class InputContent:
    text: str
    references: tuple[InputReference, ...] = ()


@dataclass(frozen=True, slots=True)
class InitialInputPayload:
    input: InputContent


@dataclass(frozen=True, slots=True)
class RelatedInputPayload:
    input: InputContent


@dataclass(frozen=True, slots=True)
class ModelStepPayload:
    step_id: str
    read_through_sequence: int
    result: ModelStepResult


@dataclass(frozen=True, slots=True)
class ToolResultPayload:
    step_id: str
    tool_name: str
    result: ToolResult


@dataclass(frozen=True, slots=True)
class WaitingPayload:
    step_id: str
    reference: str
    question: str
    read_through_sequence: int


@dataclass(frozen=True, slots=True)
class TerminalOutcomePayload:
    status: TerminalStatus
    output: str = ""
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class ContextBasePayload:
    messages: tuple[ModelMessage, ...]
    coverage_sequence: int
    through_sequence: int


@dataclass(frozen=True, slots=True)
class ModelInputPayload:
    step_id: str
    base_sequence: int | None
    read_through_sequence: int
    visible_tool_names: tuple[str, ...]
    minute_time: str | None


HistoryPayload: TypeAlias = InitialInputPayload | RelatedInputPayload | ModelStepPayload | ToolResultPayload | WaitingPayload | TerminalOutcomePayload | ContextBasePayload | ModelInputPayload


@dataclass(frozen=True, slots=True)
class EncodedHistory:
    kind: HistoryKind
    version: int
    payload: dict[str, object]


class _Record(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)


Identifier = Annotated[str, Field(min_length=1, max_length=256)]
Sequence = Annotated[int, Field(ge=0, le=2**63 - 1)]
TokenCount = Annotated[int, Field(ge=0, le=2**63 - 1)]


class _Reference(_Record):
    reference: Annotated[str, Field(min_length=1, max_length=4096)]
    name: Annotated[str, Field(max_length=512)] | None
    media_type: Annotated[str, Field(max_length=256)] | None


class _Input(_Record):
    text: str
    references: Annotated[list[_Reference], Field(max_length=64)]


class _InputPayload(_Record):
    input: _Input


class _Usage(_Record):
    input_tokens: TokenCount | None
    output_tokens: TokenCount | None
    cache_read_tokens: TokenCount | None
    cache_write_tokens: TokenCount | None
    reasoning_tokens: TokenCount | None


class _Call(_Record):
    call_id: Identifier
    name: Annotated[str, Field(min_length=1, max_length=256)]
    arguments_json: str


class _ModelResult(_Record):
    content: str
    calls: Annotated[list[_Call], Field(max_length=128)]
    finish_reason: Literal["stop", "tool_calls", "length", "content_filter", "refusal"]
    usage: _Usage
    interaction_id: Identifier
    requires_continuation: bool


class _ModelPayload(_Record):
    step_id: Identifier
    read_through_sequence: Sequence
    result: _ModelResult


class _ToolResult(_Record):
    call_id: Identifier
    status: Literal["success", "error", "uncertain"]
    content_json: str


class _ToolPayload(_Record):
    step_id: Identifier
    tool_name: Annotated[str, Field(min_length=1, max_length=256)]
    result: _ToolResult


class _WaitingPayload(_Record):
    step_id: Identifier
    reference: Identifier
    question: Annotated[str, Field(max_length=65536)]
    read_through_sequence: Sequence


class _TerminalPayload(_Record):
    status: TerminalStatus
    output: str
    reason: Annotated[str, Field(max_length=65536)] | None


class _Content(_Record):
    kind: Literal["text", "image"]
    value: str


class _Message(_Record):
    role: Literal["system", "user", "assistant", "tool"]
    content: Annotated[list[_Content], Field(max_length=MAX_NODES)]
    calls: Annotated[list[_Call], Field(max_length=128)]
    call_id: Identifier | None
    is_error: bool
    interaction_id: Identifier | None
    requires_continuation: bool
    cache_boundary: bool


class _ContextBase(_Record):
    messages: Annotated[list[_Message], Field(max_length=2048)]
    coverage_sequence: Sequence
    through_sequence: Sequence


class _ModelInput(_Record):
    step_id: Identifier
    base_sequence: Annotated[int, Field(ge=1, le=2**63 - 1)] | None
    read_through_sequence: Sequence
    visible_tool_names: Annotated[list[Annotated[str, Field(min_length=1, max_length=64)]], Field(max_length=128)]
    minute_time: Annotated[str, Field(max_length=22)] | None


def _minute_time(value: str | None) -> None:
    if value is None:
        return
    if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}(?:Z|[+-][0-9]{2}:[0-5][0-9])", value):
        raise InvalidHistory("History request time must have minute precision and timezone")
    parsed = datetime.fromisoformat(value)
    if parsed.utcoffset() is None:
        raise InvalidHistory("History request time requires a timezone")


def _message(value: _Message) -> ModelMessage:
    for call in value.calls:
        _json_object(call.arguments_json)
    if len({call.call_id for call in value.calls}) != len(value.calls):
        raise InvalidHistory("History Context calls contain duplicate identities")
    return ModelMessage(value.role, tuple(ModelContent(part.kind, part.value) for part in value.content),
        tuple(ModelToolCall(call.call_id, call.name, call.arguments_json) for call in value.calls),
        value.call_id, value.is_error, value.interaction_id, value.requires_continuation, value.cache_boundary)


def _context_messages(messages: tuple[ModelMessage, ...]) -> list[dict[str, object]]:
    if len(messages) > 2048:
        raise InvalidHistory("History Context message count exceeds its bound")
    # Reserve the complete envelope and message/member nodes before transforming collections.
    nodes = 13 + 17 * len(messages)
    for message in messages:
        if len(message.calls) > 128:
            raise InvalidHistory("History Context call count exceeds its bound")
        nodes += 5 * len(message.content) + 7 * len(message.calls)
        if nodes > MAX_NODES:
            raise InvalidHistory("History JSON exceeds structural bounds")
    return [{"role": message.role,
        "content": [{"kind": part.kind, "value": part.value} for part in message.content],
        "calls": [{"call_id": call.call_id, "name": call.name, "arguments_json": call.arguments_json} for call in message.calls],
        "call_id": message.call_id, "is_error": message.is_error, "interaction_id": message.interaction_id,
        "requires_continuation": message.requires_continuation, "cache_boundary": message.cache_boundary}
        for message in messages]


def _check_tree(value: object, *, maximum: int | None = None) -> None:
    byte_limit = MAX_RECORD_BYTES if maximum is None else min(maximum, MAX_RECORD_BYTES)
    pending = [(value, 0)]
    nodes = 0
    minimum_bytes = 0
    while pending:
        item, depth = pending.pop()
        nodes += 1
        if nodes > MAX_NODES or depth > MAX_DEPTH:
            raise InvalidHistory("History JSON exceeds structural bounds")
        if item is None:
            minimum_bytes += 4
        elif type(item) is bool:
            minimum_bytes += 4 if item else 5
        elif type(item) is int:
            minimum_bytes += len(str(item))
        elif type(item) is float and math.isfinite(item):
            minimum_bytes += len(repr(item))
        elif type(item) is str:
            minimum_bytes += len(item.encode("utf-8")) + 2
            minimum_bytes += item.count('"') + item.count("\\")
            minimum_bytes += sum(item.count(chr(code)) * (1 if chr(code) in "\b\f\n\r\t" else 5) for code in range(32))
        elif type(item) is dict:
            if len(item) > MAX_NODES or any(type(key) is not str for key in item):
                raise InvalidHistory("History JSON object is invalid")
            if nodes + len(pending) + 2 * len(item) > MAX_NODES:
                raise InvalidHistory("History JSON exceeds structural bounds")
            minimum_bytes += 2 + len(item) + max(0, len(item) - 1)
            pending.extend((key, depth + 1) for key in item)
            pending.extend((child, depth + 1) for child in item.values())
        elif type(item) is list:
            if nodes + len(pending) + len(item) > MAX_NODES:
                raise InvalidHistory("History JSON exceeds structural bounds")
            minimum_bytes += 2 + max(0, len(item) - 1)
            pending.extend((child, depth + 1) for child in item)
        else:
            raise InvalidHistory("History payload is not finite JSON")
        if minimum_bytes > byte_limit:
            raise InvalidHistory("History record exceeds its byte limit")


def _json(value: object, maximum: int = MAX_RECORD_BYTES) -> str:
    _check_tree(value, maximum=maximum)
    encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    if len(encoded.encode("utf-8")) > maximum:
        raise InvalidHistory("History record exceeds its byte limit")
    return encoded


def _json_object(encoded: str) -> None:
    value = json.loads(encoded)
    if type(value) is not dict:
        raise InvalidHistory("History Tool content must be a JSON object")
    _check_tree(value)


def _input(value: _Input) -> InputContent:
    _json(value.model_dump(), MAX_INPUT_BYTES)
    return InputContent(value.text, tuple(InputReference(item.reference, item.name, item.media_type) for item in value.references))


def decode_history(kind: str, version: int, payload: object) -> HistoryPayload:
    """Validate authoritative persisted JSON without exposing invalid source values."""
    try:
        if type(version) is not int or version != HISTORY_VERSION:
            raise InvalidHistory("Unsupported History version")
        if kind not in ("initial_input", "related_input", "model_step", "tool_result", "waiting", "terminal_outcome", "context_base", "model_input"):
            raise InvalidHistory("Unsupported History kind")
        _json({"kind": kind, "version": version, "payload": payload})
        if kind in ("initial_input", "related_input"):
            value = _input(_InputPayload.model_validate(payload).input)
            return InitialInputPayload(value) if kind == "initial_input" else RelatedInputPayload(value)
        if kind == "model_step":
            model = _ModelPayload.model_validate(payload)
            calls = model.result.calls
            if len({call.call_id for call in calls}) != len(calls):
                raise InvalidHistory("History Model calls contain duplicate identities")
            for call in calls:
                _json_object(call.arguments_json)
            usage = model.result.usage
            return ModelStepPayload(model.step_id, model.read_through_sequence, ModelStepResult(
                model.result.content, tuple(ModelToolCall(call.call_id, call.name, call.arguments_json) for call in calls),
                model.result.finish_reason, ModelUsage(usage.input_tokens, usage.output_tokens, usage.cache_read_tokens,
                    usage.cache_write_tokens, usage.reasoning_tokens), model.result.interaction_id, model.result.requires_continuation))
        if kind == "tool_result":
            tool = _ToolPayload.model_validate(payload)
            _json_object(tool.result.content_json)
            return ToolResultPayload(tool.step_id, tool.tool_name,
                ToolResult(tool.result.call_id, tool.result.status, tool.result.content_json))
        if kind == "waiting":
            waiting = _WaitingPayload.model_validate(payload)
            return WaitingPayload(waiting.step_id, waiting.reference, waiting.question, waiting.read_through_sequence)
        if kind == "context_base":
            base = _ContextBase.model_validate(payload)
            if base.through_sequence < base.coverage_sequence:
                raise InvalidHistory("History Context coverage exceeds its source boundary")
            return ContextBasePayload(tuple(_message(message) for message in base.messages),
                base.coverage_sequence, base.through_sequence)
        if kind == "model_input":
            request = _ModelInput.model_validate(payload)
            if len(set(request.visible_tool_names)) != len(request.visible_tool_names):
                raise InvalidHistory("History visible Tool names must be unique")
            _minute_time(request.minute_time)
            return ModelInputPayload(request.step_id, request.base_sequence, request.read_through_sequence,
                tuple(request.visible_tool_names), request.minute_time)
        terminal = _TerminalPayload.model_validate(payload)
        return TerminalOutcomePayload(terminal.status, terminal.output, terminal.reason)
    except InvalidHistory:
        raise
    except (ValueError, TypeError, RecursionError, UnicodeError, ValidationError, InvalidInput):
        raise InvalidHistory("History payload is invalid") from None


def encode_history(payload: HistoryPayload) -> EncodedHistory:
    """Return detached JSON; exact embedded Tool JSON strings remain unchanged."""
    try:
        if isinstance(payload, (InitialInputPayload, RelatedInputPayload)):
            if len(payload.input.references) > 64:
                raise InvalidHistory("History input reference count exceeds its bound")
            kind: HistoryKind = "initial_input" if isinstance(payload, InitialInputPayload) else "related_input"
            data: dict[str, object] = {"input": {"text": payload.input.text, "references": [
                {"reference": ref.reference, "name": ref.name, "media_type": ref.media_type} for ref in payload.input.references]}}
        elif isinstance(payload, ModelStepPayload):
            if len(payload.result.calls) > 128:
                raise InvalidHistory("History Model call count exceeds its bound")
            kind = "model_step"
            result, usage = payload.result, payload.result.usage
            data = {"step_id": payload.step_id, "read_through_sequence": payload.read_through_sequence,
                "result": {"content": result.content, "calls": [{"call_id": call.call_id, "name": call.name,
                    "arguments_json": call.arguments_json} for call in result.calls], "finish_reason": result.finish_reason,
                    "usage": {"input_tokens": usage.input_tokens, "output_tokens": usage.output_tokens,
                        "cache_read_tokens": usage.cache_read_tokens, "cache_write_tokens": usage.cache_write_tokens,
                        "reasoning_tokens": usage.reasoning_tokens}, "interaction_id": result.interaction_id,
                    "requires_continuation": result.requires_continuation}}
        elif isinstance(payload, ToolResultPayload):
            kind = "tool_result"
            data = {"step_id": payload.step_id, "tool_name": payload.tool_name, "result": {
                "call_id": payload.result.call_id, "status": payload.result.status, "content_json": payload.result.content_json}}
        elif isinstance(payload, WaitingPayload):
            kind = "waiting"
            data = {"step_id": payload.step_id, "reference": payload.reference, "question": payload.question,
                "read_through_sequence": payload.read_through_sequence}
        elif isinstance(payload, TerminalOutcomePayload):
            kind = "terminal_outcome"
            data = {"status": payload.status, "output": payload.output, "reason": payload.reason}
        elif isinstance(payload, ContextBasePayload):
            kind = "context_base"
            data = {"messages": _context_messages(payload.messages), "coverage_sequence": payload.coverage_sequence,
                "through_sequence": payload.through_sequence}
        elif isinstance(payload, ModelInputPayload):
            if len(payload.visible_tool_names) > 128:
                raise InvalidHistory("History visible Tool name count exceeds its bound")
            kind = "model_input"
            data = {"step_id": payload.step_id, "base_sequence": payload.base_sequence,
                "read_through_sequence": payload.read_through_sequence,
                "visible_tool_names": list(payload.visible_tool_names), "minute_time": payload.minute_time}
        else:
            raise InvalidHistory("Unsupported History payload type")
        decode_history(kind, HISTORY_VERSION, data)
        return EncodedHistory(kind, HISTORY_VERSION, data)
    except InvalidHistory:
        raise
    except (ValueError, TypeError, RecursionError, UnicodeError, AttributeError):
        raise InvalidHistory("History payload is invalid") from None
