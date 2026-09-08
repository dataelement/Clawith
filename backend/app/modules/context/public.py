"""Sourced, disposable model views; Run retains every observed input and summary."""

import asyncio
import json
import re
from dataclasses import asdict, dataclass, field, fields, replace
from datetime import datetime
from hashlib import sha256
from time import perf_counter
from typing import Literal, Protocol
from uuid import UUID

from pydantic import TypeAdapter, ValidationError

from app.infrastructure.transactions import TransactionContext
from app.modules.context.repository import ContextProjectionRepository
from app.modules.model.public import (
    ModelContent,
    ModelContextProfile,
    ModelFailure,
    ModelLimits,
    ModelMessage,
    ModelToolDefinition,
)

MAX_VIEW_BYTES = 16 * 1024 * 1024
MAX_VIEW_ITEMS = 100_000
_DEFAULT_MODEL_LIMITS = ModelLimits()
_ESCAPED_JSON = re.compile(r'[\x00-\x1f"\\]')
_REQUEST_ENVELOPE_BYTES = len(b'{"messages":[],"tools":[]}') + 256


class ModelPreparationFailure(Exception):
    """A normalized Model operation failed while preparing the next Context view."""

    def __init__(self, failure: ModelFailure) -> None:
        super().__init__("Context Model preparation failed")
        self.failure = failure


def _check_request_size(messages: tuple[ModelMessage, ...], tools: tuple[ModelToolDefinition, ...]) -> tuple[int, int]:
    items = len(messages) + len(tools)
    size = 256 + 256 * items
    if items > MAX_VIEW_ITEMS or size > MAX_VIEW_BYTES:
        raise ContextBudgetExceeded("Context view exceeds its physical assembly bound")

    def text(value: str) -> None:
        nonlocal size
        if len(value) > MAX_VIEW_BYTES:
            raise ContextBudgetExceeded("Context view exceeds its physical assembly bound")
        size += len(value.encode("utf-8")) + sum(
            5 if ord(match.group()) < 32 else 1 for match in _ESCAPED_JSON.finditer(value))
        if size > MAX_VIEW_BYTES:
            raise ContextBudgetExceeded("Context view exceeds its physical assembly bound")

    for message in messages:
        items += len(message.content) + len(message.calls)
        size += 256 * (len(message.content) + len(message.calls))
        if items > MAX_VIEW_ITEMS:
            raise ContextBudgetExceeded("Context view exceeds its physical assembly bound")
        for content in message.content:
            text(content.value)
        for call in message.calls:
            text(call.call_id)
            text(call.name)
            text(call.arguments_json)
        if message.call_id is not None:
            text(message.call_id)
        if message.interaction_id is not None:
            text(message.interaction_id)
    for tool in tools:
        text(tool.name)
        text(tool.description)
        text(tool.schema_json)
    if size > MAX_VIEW_BYTES:
        raise ContextBudgetExceeded("Context view exceeds its physical assembly bound")
    return size - 256, items


class ContextBudgetExceeded(ValueError):
    """The complete request cannot fit the fixed model's physical input window."""


@dataclass(frozen=True, slots=True)
class ContextSource:
    label: str
    text: str
    role: Literal["system", "user"]


@dataclass(frozen=True, slots=True)
class ContextUnit:
    """One indivisible interaction, including all results for any contained calls."""

    sequence: int
    messages: tuple[ModelMessage, ...]


@dataclass(frozen=True, slots=True)
class ContextSummary:
    objective: str
    constraints: str
    progress: str
    decisions: str
    unresolved: str
    next_actions: str
    references: str


@dataclass(frozen=True, slots=True)
class ContextState:
    units: tuple[ContextUnit, ...] = ()
    through_sequence: int = 0
    coverage_sequence: int = 0
    summary: ContextSummary | None = None


@dataclass(frozen=True, slots=True)
class ContextBase:
    """Exact replacement view to persist in History before the model request."""

    state: ContextState
    messages: tuple[ModelMessage, ...]


class ContextSummarizer(Protocol):
    async def summarize(
        self, *, previous: ContextSummary | None, units: tuple[ContextUnit, ...],
        sources: tuple[ContextSource, ...], max_tokens: int,
    ) -> ContextSummary: ...


class ContextTokenCounter(Protocol):
    async def __call__(self, messages: tuple[ModelMessage, ...], tools: tuple[ModelToolDefinition, ...]) -> int: ...


@dataclass(frozen=True, slots=True)
class ContextTelemetry:
    assembly_seconds: float
    input_tokens: int
    source_reads: int
    cleared_tool_tokens: int | None
    compactions: int
    coverage_sequence: int
    compaction_seconds: float = 0
    source_snapshot_reuses: int = 0
    validated_units: int = 0
    serialized_messages: int = 0
    reused_units: int = 0
    token_counting_seconds: float = 0
    token_counting_calls: int = 0


@dataclass(frozen=True, slots=True)
class _PreparedEncoding:
    state: ContextState
    payload: bytes
    digest: str


@dataclass(frozen=True, slots=True)
class PreparedContext:
    messages: tuple[ModelMessage, ...]
    input_tokens: int
    output_tokens: int
    state: ContextState
    observation: ContextBase | None
    telemetry: ContextTelemetry
    _encoding: _PreparedEncoding | None = field(default=None, repr=False, compare=False)


def _text(role: Literal["system", "user"], text: str) -> ModelMessage:
    return ModelMessage(role, (ModelContent("text", text),))


def _tokens(messages: tuple[ModelMessage, ...], tools: tuple[ModelToolDefinition, ...]) -> int:
    # Serialized bytes conservatively estimate text tokens, never image tokens.
    _check_request_size(messages, tools)
    value = {"messages": [asdict(m) for m in messages], "tools": [asdict(t) for t in tools]}
    size = len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()) + 256
    if size > MAX_VIEW_BYTES:
        raise ContextBudgetExceeded("Context view exceeds its physical assembly bound")
    return size


def _validate_unit(unit: ContextUnit) -> None:
    if not isinstance(unit.messages, tuple):
        raise TypeError("Context units must contain immutable messages")
    if unit.sequence < 1 or not unit.messages:
        raise ValueError("Context units require a positive sequence and messages")
    pending: set[str] = set()
    seen: set[str] = set()
    for message in unit.messages:
        if not isinstance(message.content, tuple) or not isinstance(message.calls, tuple):
            raise TypeError("Context messages must contain immutable content and calls")
        if message.role not in ("user", "assistant", "tool"):
            raise ValueError("History cannot introduce instruction messages")
        if message.role == "tool":
            if message.call_id not in pending:
                raise ValueError("Context contains an unmatched Tool result")
            pending.remove(message.call_id)
        elif pending:
            raise ValueError("Context contains an incomplete Tool exchange")
        if message.calls and message.role != "assistant":
            raise ValueError("Only assistant messages can call Tools")
        for call in message.calls:
            if call.call_id in seen:
                raise ValueError("Context contains duplicate Tool calls")
            pending.add(call.call_id)
            seen.add(call.call_id)
    if pending:
        raise ValueError("Context contains an incomplete Tool exchange")


@dataclass(frozen=True, slots=True)
class _Cost:
    encoded_bytes: int = 0
    bound_bytes: int = 0
    items: int = 0
    messages: int = 0
    tools: int = 0
    images: int = 0

    def plus(self, other: "_Cost") -> "_Cost":
        return _Cost(self.encoded_bytes + other.encoded_bytes, self.bound_bytes + other.bound_bytes,
            self.items + other.items, self.messages + other.messages, self.tools + other.tools, self.images + other.images)

    def serialized_size(self) -> int:
        size = _REQUEST_ENVELOPE_BYTES + self.encoded_bytes + max(0, self.messages - 1) + max(0, self.tools - 1)
        if self.bound_bytes + 256 > MAX_VIEW_BYTES or self.items > MAX_VIEW_ITEMS or size > MAX_VIEW_BYTES:
            raise ContextBudgetExceeded("Context view exceeds its physical assembly bound")
        return size


def _encoded_messages(messages: tuple[ModelMessage, ...]) -> tuple[_Cost, tuple[bytes, ...]]:
    bound, items = _check_request_size(messages, ())
    encoded = tuple(json.dumps(asdict(message), ensure_ascii=False, separators=(",", ":")).encode() for message in messages)
    images = sum(content.kind == "image" for message in messages for content in message.content)
    return _Cost(sum(map(len, encoded)), bound, items, len(messages), images=images), encoded


def _message_cost(messages: tuple[ModelMessage, ...]) -> _Cost:
    return _encoded_messages(messages)[0]


def _unit_bytes(sequence: int, messages: tuple[bytes, ...]) -> bytes:
    return b'{"sequence":' + str(sequence).encode("ascii") + b',"messages":[' + b','.join(messages) + b']}'


def _tool_cost(tools: tuple[ModelToolDefinition, ...]) -> _Cost:
    bound, items = _check_request_size((), tools)
    size = sum(len(json.dumps(asdict(tool), ensure_ascii=False, separators=(",", ":")).encode()) for tool in tools)
    return _Cost(size, bound, items, tools=len(tools))


@dataclass(frozen=True, slots=True)
class _View:
    state: ContextState
    messages: tuple[ModelMessage, ...]
    cost: _Cost
    units: tuple[_Cost, ...]
    unit_payloads: tuple[bytes, ...]
    summary_payload: bytes


def _summary_messages(summary: ContextSummary | None) -> tuple[ModelMessage, ...]:
    if summary is None:
        return ()
    # Bound the source fields before joining or JSON expansion.
    _check_request_size(tuple(_text("user", getattr(summary, field.name)) for field in fields(ContextSummary)), ())
    return (_text("user", "[Prior work summary]\n" + json.dumps(asdict(summary), ensure_ascii=False)),)


def _view(state: ContextState) -> _View:
    _validate_state(state)
    messages = _summary_messages(state.summary)
    cost = _message_cost(messages)
    units = []
    payloads = []
    for unit in state.units:
        value, encoded = _encoded_messages(unit.messages)
        units.append(value)
        payloads.append(_unit_bytes(unit.sequence, encoded))
        cost = cost.plus(value)
    summary = b'null' if state.summary is None else json.dumps(asdict(state.summary), ensure_ascii=False, separators=(",", ":")).encode()
    return _View(state, messages + tuple(m for unit in state.units for m in unit.messages), cost, tuple(units), tuple(payloads), summary)


def _prepared_encoding(view: _View) -> _PreparedEncoding:
    suffix = (b'],"through_sequence":' + str(view.state.through_sequence).encode("ascii") + b',"coverage_sequence":' +
        str(view.state.coverage_sequence).encode("ascii") + b',"summary":' + view.summary_payload + b'}')
    size = len(b'{"units":[') + sum(map(len, view.unit_payloads)) + max(0, len(view.unit_payloads) - 1) + len(suffix)
    if size > MAX_VIEW_BYTES:
        raise ContextBudgetExceeded("Context projection exceeds its physical storage bound")
    payload = b'{"units":[' + b','.join(view.unit_payloads) + suffix
    return _PreparedEncoding(view.state, payload, _state_digest(payload))


class ContextAssembler:
    """Reuse fixed sources; caller supplies only newly committed complete units.

    The returned state is staged. Persist observation and model input before using
    it; publish it as the current projection only with the caller's commit.
    """

    def __init__(self, *, sources: tuple[ContextSource, ...], profile: ModelContextProfile,
                 summarizer: ContextSummarizer | None = None, model_limits: ModelLimits = _DEFAULT_MODEL_LIMITS,
                 request_overhead_bytes: int = 32768, token_counter: ContextTokenCounter | None = None) -> None:
        if not isinstance(sources, tuple):
            raise TypeError("Fixed Context sources must be immutable")
        if profile.context_limit <= profile.output_limit or profile.output_limit <= 0:
            raise ValueError("Model input budget must be positive")
        if not sources or any(not source.label.strip() for source in sources):
            raise ValueError("Context requires labelled fixed sources")
        self._sources = sources
        self._profile = profile
        self._summarizer = summarizer
        self._token_counter = token_counter
        self._counted_request: tuple[tuple[ModelMessage, ...], tuple[ModelToolDefinition, ...]] | None = None
        self._counted_tokens: int | None = None
        self._summary_origin_state: ContextState | None = None
        self._summary_key: tuple[ContextSummary | None, tuple[ContextUnit, ...], int] | None = None
        self._summary_result: ContextSummary | None = None
        if request_overhead_bytes < 0:
            raise ValueError("Model request overhead must be nonnegative")
        self._model_limits = model_limits
        self._request_overhead_bytes = request_overhead_bytes
        if len(sources) > 1000 or sum(len(s.label) + len(s.text) for s in sources) > MAX_VIEW_BYTES:
            raise ContextBudgetExceeded("Context fixed sources exceed their physical assembly bound")
        instructions = "\n\n".join(f"[{s.label}]\n{s.text}" for s in sources if s.role == "system")
        self._prefix = ((_text("system", instructions),) if instructions else ()) + tuple(
            _text("user", f"[{s.label}]\n{s.text}") for s in sources if s.role == "user")
        self._prefix_cost = _message_cost(self._prefix)
        self._prefix_cost.serialized_size()
        self._cached_view: _View | None = None
        self._cached_tools: tuple[ModelToolDefinition, ...] | None = None
        self._cached_tool_cost = _Cost()

    async def prepare(
        self, *, state: ContextState, additions: tuple[ContextUnit, ...],
        tools: tuple[ModelToolDefinition, ...], todo: str | None = None,
        minute_time: datetime | None = None,
    ) -> PreparedContext:
        started = perf_counter()
        if not isinstance(tools, tuple) or not isinstance(additions, tuple):
            raise TypeError("Tool exposure and Context additions must be immutable")
        if self._summary_origin_state is not state:
            self._summary_origin_state = None
            self._summary_key = None
            self._summary_result = None
        if len(state.units) + len(additions) > MAX_VIEW_ITEMS:
            raise ContextBudgetExceeded("Context view exceeds its physical assembly bound")
        sequence = state.through_sequence
        reused = self._cached_view is not None and self._cached_view.state is state
        previous = self._cached_view if reused else _view(state)
        assert previous is not None
        validated_units = 0 if reused else len(state.units)
        serialized_messages = 0 if reused else len(previous.messages)
        delta_cost = _Cost()
        delta_unit_costs = []
        delta_unit_payloads = []
        for unit in additions:
            _validate_unit(unit)
            validated_units += 1
            if unit.sequence <= sequence:
                raise ValueError("Context additions must advance the History cursor")
            sequence = unit.sequence
            cost, encoded = _encoded_messages(unit.messages)
            delta_unit_payloads.append(_unit_bytes(unit.sequence, encoded))
            serialized_messages += len(unit.messages)
            delta_unit_costs.append(cost)
            delta_cost = delta_cost.plus(cost)
        current = replace(state, units=state.units + additions, through_sequence=sequence)
        view = _View(current, previous.messages + tuple(m for unit in additions for m in unit.messages),
            previous.cost.plus(delta_cost), previous.units + tuple(delta_unit_costs),
            previous.unit_payloads + tuple(delta_unit_payloads), previous.summary_payload)
        original_units = current.units
        tail: tuple[ModelMessage, ...] = ()
        if todo is not None:
            tail += (_text("user", "[Current planning view]\n" + todo),)
        if minute_time is not None:
            if minute_time.tzinfo is None or minute_time.utcoffset() is None:
                raise ValueError("Context time requires a timezone")
            minute = minute_time.replace(second=0, microsecond=0).isoformat(timespec="minutes")
            tail += (_text("user", "[Current time]\n" + minute),)

        def assemble(view: _View) -> tuple[ModelMessage, ...]:
            return self._prefix + view.messages + tail

        budget = self._profile.context_limit - self._profile.output_limit
        byte_budget = self._model_limits.request_bytes - self._request_overhead_bytes
        if len(tools) > self._model_limits.max_tools:
            raise ContextBudgetExceeded("Tool exposure exceeds Model cardinality bounds")
        if tools != self._cached_tools:
            self._cached_tool_cost = _tool_cost(tools)
            self._cached_tools = tools
        tail_cost = _message_cost(tail)
        serialized_messages += len(tail)
        fixed_cost = self._prefix_cost.plus(self._cached_tool_cost).plus(tail_cost)
        token_count_seconds = 0.0
        token_counting_calls = 0

        async def measure(candidate: _View) -> int | None:
            nonlocal token_count_seconds, token_counting_calls
            cost = fixed_cost.plus(candidate.cost)
            estimate = cost.serialized_size()
            if estimate > byte_budget or cost.messages > self._model_limits.max_messages:
                return None
            if not cost.images:
                return estimate
            if not self._profile.supports_images or self._token_counter is None:
                raise ModelPreparationFailure(ModelFailure("unsupported_capability",
                    "Image input requires Model image support and an exact token counter", True))
            key = (assemble(candidate), tools)
            if key == self._counted_request:
                return self._counted_tokens
            at = perf_counter()
            token_counting_calls += 1
            try:
                async with asyncio.timeout(min(10.0, self._model_limits.timeout_seconds)):
                    result = await self._token_counter(*key)
            except TimeoutError:
                raise ModelPreparationFailure(ModelFailure("transport_failed", "Model input token counting timed out", False)) from None
            finally:
                token_count_seconds += perf_counter() - at
            if type(result) is not int or result < 0:
                raise TypeError("Model token count must be a nonnegative integer")
            self._counted_request, self._counted_tokens = key, result
            return result

        def exceeds(messages: tuple[ModelMessage, ...], tokens: int | None) -> bool:
            return tokens is None or tokens > budget or len(messages) > self._model_limits.max_messages
        messages = assemble(view)
        count = await measure(view)
        cleared: int | None = 0
        changed = False
        compactions = 0
        compaction_seconds = 0.0
        if exceeds(messages, count):
            counted_images = bool(view.cost.images)
            units = list(current.units)
            # Keep the latest complete interaction intact for the next decision.
            for index, unit in enumerate(units[:-1]):
                rewritten = tuple(replace(message, content=(ModelContent("text", "[Earlier Tool output omitted; retrieve it again if needed.]"),))
                    if message.role == "tool" and (any(part.kind == "image" for part in message.content)
                        or _tokens((message,), ()) > 1024) else message
                    for message in unit.messages)
                if rewritten != unit.messages:
                    units[index] = replace(unit, messages=rewritten)
                    changed = True
            current = replace(current, units=tuple(units))
            view = _view(current)
            validated_units += len(current.units)
            serialized_messages += len(view.messages)
            messages = assemble(view)
            new_count = await measure(view)
            cleared = (None if count is None or new_count is None or counted_images != bool(view.cost.images)
                else max(0, count - new_count))
            count = new_count
        if exceeds(messages, count) and self._summarizer is not None and len(current.units) > 1:
            older, recent = current.units[:-1], current.units[-1:]
            bare = _View(replace(current, units=recent, summary=None), tuple(m for unit in recent for m in unit.messages),
                view.units[-1], view.units[-1:], view.unit_payloads[-1:], b'null')
            retained_count = await measure(bare)
            remaining = -1 if retained_count is None else budget - retained_count - 512
            if remaining > 0:
                summary_key = (current.summary, original_units[:-1], remaining)
                if summary_key == self._summary_key and self._summary_result is not None:
                    summary = self._summary_result
                else:
                    compaction_started = perf_counter()
                    summary = await self._summarizer.summarize(previous=current.summary, units=original_units[:-1],
                        sources=self._sources, max_tokens=remaining)
                    compaction_seconds = perf_counter() - compaction_started
                if not summary.objective.strip():
                    raise ValueError("Context summary must preserve the work objective")
                current = replace(current, units=recent, summary=summary, coverage_sequence=older[-1].sequence)
                view = _view(current)
                self._summary_origin_state, self._summary_key, self._summary_result = state, summary_key, summary
                validated_units += len(current.units)
                serialized_messages += len(view.messages)
                messages = assemble(view)
                count = await measure(view)
                changed = True
                compactions = 1
        if count is None or exceeds(messages, count):
            raise ContextBudgetExceeded("Context exceeds the fixed model input window after safe compaction")
        base_messages = messages[len(self._prefix):len(messages) - len(tail) if tail else len(messages)]
        encoding = _prepared_encoding(view)
        self._cached_view = view
        return PreparedContext(messages, count, self._profile.output_limit, current,
            ContextBase(current, base_messages) if changed else None,
            ContextTelemetry(max(0.0, perf_counter() - started - compaction_seconds - token_count_seconds), count, 0, cleared, compactions,
                current.coverage_sequence, compaction_seconds, len(self._sources),
                validated_units, serialized_messages, len(state.units) if reused else 0,
                token_count_seconds, token_counting_calls), encoding)


def _validate_state(state: ContextState) -> None:
    if not isinstance(state.units, tuple):
        raise TypeError("Context state must contain immutable units")
    if len(state.units) > MAX_VIEW_ITEMS or sum(len(unit.messages) for unit in state.units) > MAX_VIEW_ITEMS:
        raise ContextBudgetExceeded("Context view exceeds its physical assembly bound")
    summary_messages = () if state.summary is None else tuple(
        _text("user", getattr(state.summary, field.name)) for field in fields(ContextSummary))
    _check_request_size(summary_messages + tuple(m for unit in state.units for m in unit.messages), ())
    if not 0 <= state.coverage_sequence <= state.through_sequence:
        raise ValueError("Invalid Context coverage")
    position = state.coverage_sequence
    for unit in state.units:
        _validate_unit(unit)
        if not position < unit.sequence <= state.through_sequence:
            raise ValueError("Invalid Context view ordering")
        position = unit.sequence
    if state.coverage_sequence and state.summary is None:
        raise ValueError("Covered Context requires a summary")
    if state.summary is not None and not state.summary.objective.strip():
        raise ValueError("Context summary must preserve the work objective")


def restore_base(*, messages: tuple[ModelMessage, ...], coverage_sequence: int,
                 through_sequence: int) -> ContextState:
    """Restore an observed logical base, without calling a summarizer or any source.

    History preserves the exact model messages but need not duplicate original
    interaction boundaries. The retained base is one complete composite unit;
    subsequently appended interactions keep their individual boundaries.
    """
    _check_request_size(messages, ())
    summary = None
    retained = messages
    if coverage_sequence > 0:
        marker = "[Prior work summary]\n"
        if not messages or messages[0].role != "user" or len(messages[0].content) != 1:
            raise ValueError("Observed Context summary is missing")
        content = messages[0].content[0]
        if content.kind != "text" or not content.value.startswith(marker):
            raise ValueError("Observed Context summary is invalid")
        try:
            raw = json.loads(content.value[len(marker):])
            if not isinstance(raw, dict) or set(raw) != {field.name for field in fields(ContextSummary)}:
                raise ValueError("Invalid summary fields")
            summary = TypeAdapter(ContextSummary).validate_python(raw, strict=False)
            if any(not isinstance(value, str) for value in raw.values()):
                raise ValueError("Invalid summary fields")
        except (ValueError, TypeError):
            raise ValueError("Observed Context summary is invalid") from None
        expected = _text("user", marker + json.dumps(asdict(summary), ensure_ascii=False))
        if messages[0] != expected:
            raise ValueError("Observed Context summary representation is invalid")
        retained = messages[1:]
    state = ContextState((ContextUnit(through_sequence, retained),) if retained else (),
        through_sequence, coverage_sequence, summary)
    _validate_state(state)
    return state


def _state_bytes(state: ContextState) -> bytes:
    _validate_state(state)
    payload = TypeAdapter(ContextState).dump_json(state)
    if len(payload) > MAX_VIEW_BYTES:
        raise ContextBudgetExceeded("Context projection exceeds its physical storage bound")
    return payload


def context_state_hash(state: ContextState) -> str:
    """Run History may bind a disposable view to the exact state observed before a Model call."""
    return _state_digest(_state_bytes(state))


def _state_digest(payload: bytes) -> str:
    digest = sha256(b"context_view:v1:")
    digest.update(payload)
    return digest.hexdigest()


class ContextProjectionService:
    """A malformed/version-incompatible projection is a cache miss, never lost History."""

    def __init__(self, transaction: TransactionContext) -> None:
        self._repository = ContextProjectionRepository(transaction)

    async def load(self, *, tenant_id: UUID, run_id: UUID, expected_hash: str | None = None) -> ContextState | None:
        payload = await self._repository.load(tenant_id=tenant_id, run_id=run_id)
        if payload is None:
            return None
        try:
            state = TypeAdapter(ContextState).validate_json(payload, strict=True)
            _validate_state(state)
        except (ValidationError, ValueError):
            await self._repository.discard_observed(tenant_id=tenant_id, run_id=run_id, payload=payload)
            return None
        if expected_hash is not None and context_state_hash(state) != expected_hash:
            await self._repository.discard_observed(tenant_id=tenant_id, run_id=run_id, payload=payload)
            return None
        return state

    async def save(self, *, tenant_id: UUID, run_id: UUID, state: ContextState) -> str:
        payload = _state_bytes(state)
        await self._repository.save(tenant_id=tenant_id, run_id=run_id,
            payload=payload, coverage=state.coverage_sequence, through=state.through_sequence)
        return _state_digest(payload)

    async def save_prepared(self, *, tenant_id: UUID, run_id: UUID, prepared: PreparedContext) -> str:
        """Persist Context's exact prepared bytes; do not re-encode immutable old units."""
        encoding = prepared._encoding
        if encoding is None or encoding.state is not prepared.state:
            raise ValueError("Projection requires an unchanged Context-produced prepared result")
        await self._repository.save(tenant_id=tenant_id, run_id=run_id, payload=encoding.payload,
            coverage=encoding.state.coverage_sequence, through=encoding.state.through_sequence)
        return encoding.digest
