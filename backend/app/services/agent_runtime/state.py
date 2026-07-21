"""Serializable state and transient context contracts for Agent Runtime graphs."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Literal, NotRequired, Protocol, TypeAlias, TypedDict, cast

from langchain_core.messages import AnyMessage, BaseMessage, convert_to_openai_messages
from langgraph.graph.message import add_messages


JsonScalar: TypeAlias = str | int | float | bool | None
JsonValue: TypeAlias = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]
JsonObject: TypeAlias = dict[str, JsonValue]

LifecycleStatus: TypeAlias = Literal[
    "created",
    "queued",
    "running",
    "waiting_user",
    "waiting_external",
    "waiting_agent",
    "verifying",
    "completed",
    "failed",
    "cancelled",
]
ControlRoute: TypeAlias = Literal[
    "compact",
    "model",
    "tool",
    "verify",
    "wait",
    "terminal",
]
RuntimeNodeName: TypeAlias = Literal[
    "control_guard",
    "compact",
    "model",
    "tool",
    "verify",
    "wait",
    "terminal",
]


@dataclass(frozen=True, slots=True)
class RunRegistrySnapshot:
    """Compatibility shape for legacy checkpoint decoding only.

    New Thread State does not persist this value. Invocation services receive
    the required immutable Run facts as flattened ``RuntimeContext`` fields.
    """

    tenant_id: str
    run_id: str
    goal: str
    run_kind: str
    source_type: str
    model_id: str
    graph_name: str
    graph_version: str
    agent_id: str | None = None
    session_id: str | None = None
    system_role: str | None = None
    parent_run_id: str | None = None
    root_run_id: str | None = None


@dataclass(frozen=True, slots=True)
class RunInputSnapshots:
    """Versioned inputs fixed when a Run starts and reused when it resumes."""

    session_context: JsonObject
    session_context_version: int
    recent_session_messages: tuple[JsonObject, ...]
    related_run_summaries: tuple[JsonObject, ...]
    initial_input: JsonObject
    pending_session_messages: tuple[JsonObject, ...] = ()

    def __post_init__(self) -> None:
        """Restore tuple boundaries after msgpack decodes arrays as lists."""
        object.__setattr__(
            self,
            "recent_session_messages",
            tuple(self.recent_session_messages),
        )
        object.__setattr__(
            self,
            "related_run_summaries",
            tuple(self.related_run_summaries),
        )
        object.__setattr__(
            self,
            "pending_session_messages",
            tuple(self.pending_session_messages),
        )


class RuntimeLifecycle(TypedDict):
    """Authoritative, checkpointed lifecycle and resumable execution data."""

    status: LifecycleStatus
    next_route: ControlRoute
    reason: NotRequired[str | None]
    model_step_count: NotRequired[int]
    model_protocol_repairs: NotRequired[JsonObject]
    verification_attempt_count: NotRequired[int]
    pending_tool_calls: NotRequired[list[JsonObject]]
    deferred_resume_messages: NotRequired[list[JsonObject]]
    waiting_request: NotRequired[JsonObject | None]
    verification_result: NotRequired[JsonObject | None]
    final_answer: NotRequired[str | None]
    finish_delivery_intent: NotRequired[JsonObject | None]
    result_summary: NotRequired[JsonObject | None]
    session_context_delta: NotRequired[JsonObject | None]
    delivery_request: NotRequired[JsonObject | None]
    error: NotRequired[JsonObject | None]
    planning: NotRequired[JsonObject | None]
    planning_attempt_count: NotRequired[int]


class RuntimeGraphState(TypedDict):
    """LangGraph Thread state with one native, reducer-backed message history."""

    # Compatibility-only: older checkpoints may still contain this field.
    # New invocations carry immutable Run identity in RuntimeContext instead.
    registry: NotRequired[RunRegistrySnapshot]
    snapshots: RunInputSnapshots
    messages: Annotated[list[AnyMessage], add_messages]
    thread_summary: NotRequired[JsonObject | None]
    summary_covered_through_message_id: NotRequired[str | None]
    lifecycle: RuntimeLifecycle


class RuntimeStateUpdate(TypedDict, total=False):
    """Node updates use native message reduction plus narrow mutable state."""

    lifecycle: RuntimeLifecycle
    messages: list[AnyMessage | JsonObject]
    thread_summary: JsonObject | None
    summary_covered_through_message_id: str | None


def runtime_message_to_json(message: AnyMessage | MappingMessage) -> JsonObject:
    """Normalize a LangChain message without inventing a second reducer."""
    if isinstance(message, dict):
        return cast(JsonObject, dict(message))
    if not isinstance(message, BaseMessage):
        raise TypeError("Runtime messages must be LangChain messages or objects")
    converted = convert_to_openai_messages([message])
    if len(converted) != 1 or not isinstance(converted[0], dict):
        raise TypeError("Runtime message cannot be normalized")
    result = cast(JsonObject, dict(converted[0]))
    if message.id is not None:
        result["id"] = message.id
    for key, value in message.additional_kwargs.items():
        if key not in result:
            result[key] = cast(JsonValue, value)
    return result


MappingMessage: TypeAlias = dict[str, object]


def runtime_messages_as_json(state: RuntimeGraphState) -> tuple[JsonObject, ...]:
    """Read the reducer-backed Thread history through one canonical adapter."""
    messages = state.get("messages", [])
    if not isinstance(messages, list):
        raise TypeError("Runtime State messages must be a list")
    if not messages:
        # Backward-compatible checkpoint upgrade path. The graph migrates this
        # legacy value into the native channel on the first subsequent write.
        legacy = state.get("lifecycle", {}).get("run_messages", [])  # type: ignore[typeddict-item]
        if isinstance(legacy, list) and legacy:
            return tuple(runtime_message_to_json(message) for message in legacy)
    return tuple(runtime_message_to_json(message) for message in messages)


class RuntimeNodeExecutor(Protocol):
    """Application services behind deterministic graph nodes."""

    async def execute(
        self,
        node: RuntimeNodeName,
        state: RuntimeGraphState,
        context: "RuntimeContext",
        *,
        resume_value: JsonValue | None = None,
    ) -> RuntimeStateUpdate: ...


@dataclass(frozen=True, slots=True)
class RuntimeContext:
    """Per-invocation dependencies and authorization scope, never checkpointed."""

    tenant_id: str
    run_id: str
    command_id: str
    executor: RuntimeNodeExecutor
    goal: str = ""
    run_kind: str = ""
    source_type: str = ""
    model_id: str = ""
    graph_name: str = ""
    graph_version: str = ""
    agent_id: str | None = None
    session_id: str | None = None
    system_role: str | None = None
    parent_run_id: str | None = None
    root_run_id: str | None = None
    model_turn_limit: int | None = None
    actor_user_id: str | None = None
    actor_agent_id: str | None = None
