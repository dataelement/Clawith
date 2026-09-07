"""Sourced, disposable model views; Run retains every observed input and summary."""

import json
import re
from dataclasses import asdict, dataclass, fields, replace
from datetime import datetime
from time import perf_counter
from typing import Literal, Protocol
from uuid import UUID

from pydantic import TypeAdapter, ValidationError

from app.infrastructure.transactions import TransactionContext
from app.modules.context.repository import ContextProjectionRepository
from app.modules.model.public import ModelContent, ModelContextProfile, ModelLimits, ModelMessage, ModelToolDefinition

MAX_VIEW_BYTES = 16 * 1024 * 1024
MAX_VIEW_ITEMS = 100_000
_DEFAULT_MODEL_LIMITS = ModelLimits()
_ESCAPED_JSON = re.compile(r'[\x00-\x1f"\\]')


def _check_request_size(messages: tuple[ModelMessage, ...], tools: tuple[ModelToolDefinition, ...]) -> None:
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


@dataclass(frozen=True, slots=True)
class ContextTelemetry:
    assembly_seconds: float
    input_tokens: int
    source_reads: int
    cleared_tool_tokens: int
    compactions: int
    coverage_sequence: int
    compaction_seconds: float = 0
    source_snapshot_reuses: int = 0


@dataclass(frozen=True, slots=True)
class PreparedContext:
    messages: tuple[ModelMessage, ...]
    input_tokens: int
    output_tokens: int
    state: ContextState
    observation: ContextBase | None
    telemetry: ContextTelemetry


def _text(role: Literal["system", "user"], text: str) -> ModelMessage:
    return ModelMessage(role, (ModelContent("text", text),))


def _tokens(messages: tuple[ModelMessage, ...], tools: tuple[ModelToolDefinition, ...]) -> int:
    # UTF-8 bytes plus explicit framing is conservative for text tokenizers. Image
    # transport has independent provider accounting and is rejected below.
    _check_request_size(messages, tools)
    value = {"messages": [asdict(m) for m in messages], "tools": [asdict(t) for t in tools]}
    size = len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()) + 256
    if size > MAX_VIEW_BYTES:
        raise ContextBudgetExceeded("Context view exceeds its physical assembly bound")
    return size


def _validate_unit(unit: ContextUnit) -> None:
    if unit.sequence < 1 or not unit.messages:
        raise ValueError("Context units require a positive sequence and messages")
    pending: set[str] = set()
    seen: set[str] = set()
    for message in unit.messages:
        if message.role not in ("user", "assistant", "tool"):
            raise ValueError("History cannot introduce instruction messages")
        if any(content.kind == "image" for content in message.content):
            raise ContextBudgetExceeded("Image input requires explicit model image budgeting")
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


class ContextAssembler:
    """Reuse fixed sources; caller supplies only newly committed complete units.

    The returned state is staged. Persist observation and model input before using
    it; publish it as the current projection only with the caller's commit.
    """

    def __init__(self, *, sources: tuple[ContextSource, ...], profile: ModelContextProfile,
                 summarizer: ContextSummarizer | None = None, model_limits: ModelLimits = _DEFAULT_MODEL_LIMITS,
                 request_overhead_bytes: int = 32768) -> None:
        if profile.context_limit <= profile.output_limit or profile.output_limit <= 0:
            raise ValueError("Model input budget must be positive")
        if not sources or any(not source.label.strip() for source in sources):
            raise ValueError("Context requires labelled fixed sources")
        self._sources = sources
        self._profile = profile
        self._summarizer = summarizer
        if request_overhead_bytes < 0:
            raise ValueError("Model request overhead must be nonnegative")
        self._model_limits = model_limits
        self._request_overhead_bytes = request_overhead_bytes
        if len(sources) > 1000 or sum(len(s.label) + len(s.text) for s in sources) > MAX_VIEW_BYTES:
            raise ContextBudgetExceeded("Context fixed sources exceed their physical assembly bound")
        instructions = "\n\n".join(f"[{s.label}]\n{s.text}" for s in sources if s.role == "system")
        self._prefix = ((_text("system", instructions),) if instructions else ()) + tuple(
            _text("user", f"[{s.label}]\n{s.text}") for s in sources if s.role == "user")
        _tokens(self._prefix, ())

    async def prepare(
        self, *, state: ContextState, additions: tuple[ContextUnit, ...],
        tools: tuple[ModelToolDefinition, ...], todo: str | None = None,
        minute_time: datetime | None = None,
    ) -> PreparedContext:
        started = perf_counter()
        if len(state.units) + len(additions) > MAX_VIEW_ITEMS:
            raise ContextBudgetExceeded("Context view exceeds its physical assembly bound")
        sequence = state.through_sequence
        _validate_state(state)
        for unit in additions:
            _validate_unit(unit)
            if unit.sequence <= sequence:
                raise ValueError("Context additions must advance the History cursor")
            sequence = unit.sequence
        current = replace(state, units=state.units + additions, through_sequence=sequence)
        original_units = current.units
        tail: tuple[ModelMessage, ...] = ()
        if todo is not None:
            tail += (_text("user", "[Current planning view]\n" + todo),)
        if minute_time is not None:
            if minute_time.tzinfo is None or minute_time.utcoffset() is None:
                raise ValueError("Context time requires a timezone")
            minute = minute_time.replace(second=0, microsecond=0).isoformat(timespec="minutes")
            tail += (_text("user", "[Current time]\n" + minute),)

        def assemble(view: ContextState) -> tuple[ModelMessage, ...]:
            summary = () if view.summary is None else (
                _text("user", "[Prior work summary]\n" + json.dumps(asdict(view.summary), ensure_ascii=False)),)
            return self._prefix + summary + tuple(m for u in view.units for m in u.messages) + tail

        budget = min(self._profile.context_limit - self._profile.output_limit,
            self._model_limits.request_bytes - self._request_overhead_bytes)
        if len(tools) > self._model_limits.max_tools:
            raise ContextBudgetExceeded("Tool exposure exceeds Model cardinality bounds")

        def exceeds(messages: tuple[ModelMessage, ...], tokens: int) -> bool:
            return tokens > budget or len(messages) > self._model_limits.max_messages
        messages = assemble(current)
        count = _tokens(messages, tools)
        cleared = 0
        changed = False
        compactions = 0
        compaction_seconds = 0.0
        if exceeds(messages, count):
            units = list(current.units)
            # Keep the latest complete interaction intact for the next decision.
            for index, unit in enumerate(units[:-1]):
                rewritten = tuple(replace(message, content=(ModelContent("text", "[Earlier Tool output omitted; retrieve it again if needed.]"),))
                    if message.role == "tool" and _tokens((message,), ()) > 1024 else message
                    for message in unit.messages)
                if rewritten != unit.messages:
                    units[index] = replace(unit, messages=rewritten)
                    changed = True
            current = replace(current, units=tuple(units))
            messages = assemble(current)
            new_count = _tokens(messages, tools)
            cleared = max(0, count - new_count)
            count = new_count
        if exceeds(messages, count) and self._summarizer is not None and len(current.units) > 1:
            older, recent = current.units[:-1], current.units[-1:]
            bare = replace(current, units=recent, summary=None)
            remaining = budget - _tokens(assemble(bare), tools) - 512
            if remaining > 0:
                compaction_started = perf_counter()
                summary = await self._summarizer.summarize(previous=current.summary, units=original_units[:-1],
                    sources=self._sources, max_tokens=remaining)
                compaction_seconds = perf_counter() - compaction_started
                if not summary.objective.strip():
                    raise ValueError("Context summary must preserve the work objective")
                current = replace(current, units=recent, summary=summary, coverage_sequence=older[-1].sequence)
                _validate_state(current)
                messages = assemble(current)
                count = _tokens(messages, tools)
                changed = True
                compactions = 1
        if exceeds(messages, count):
            raise ContextBudgetExceeded("Context exceeds the fixed model input window after safe compaction")
        base_messages = messages[len(self._prefix):len(messages) - len(tail) if tail else len(messages)]
        return PreparedContext(messages, count, self._profile.output_limit, current,
            ContextBase(current, base_messages) if changed else None,
            ContextTelemetry(perf_counter() - started, count, 0, cleared, compactions,
                current.coverage_sequence, compaction_seconds, len(self._sources)))


def _validate_state(state: ContextState) -> None:
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


class ContextProjectionService:
    """A malformed/version-incompatible projection is a cache miss, never lost History."""

    def __init__(self, transaction: TransactionContext) -> None:
        self._repository = ContextProjectionRepository(transaction)

    async def load(self, *, tenant_id: UUID, run_id: UUID) -> ContextState | None:
        payload = await self._repository.load(tenant_id=tenant_id, run_id=run_id)
        if payload is None:
            return None
        try:
            state = TypeAdapter(ContextState).validate_json(payload, strict=True)
            _validate_state(state)
        except (ValidationError, ValueError):
            await self._repository.discard_observed(tenant_id=tenant_id, run_id=run_id, payload=payload)
            return None
        return state

    async def save(self, *, tenant_id: UUID, run_id: UUID, state: ContextState) -> None:
        _validate_state(state)
        payload = TypeAdapter(ContextState).dump_json(state)
        await self._repository.save(tenant_id=tenant_id, run_id=run_id,
            payload=payload, coverage=state.coverage_sequence, through=state.through_sequence)
