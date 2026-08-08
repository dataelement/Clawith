"""Deterministic LangGraph control flow for the durable Agent Runtime."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, cast

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.runtime import Runtime
from langgraph.types import RetryPolicy, interrupt

from app.config import Settings, get_settings
from app.services.agent_runtime.state import (
    ControlRoute,
    JsonValue,
    RuntimeContext,
    RuntimeGraphState,
    RuntimeNodeName,
    RuntimeStateUpdate,
    runtime_messages_as_json,
)
from app.services.agent_runtime.tool_execution import (
    RetryableToolNodeError,
    SAFE_READ_MAX_ATTEMPTS,
)


CONTROL_GUARD_NODE = "control_guard"
COMPACT_NODE = "compact_run_if_needed"
MODEL_NODE = "model"
TOOL_NODE = "tool"
VERIFY_NODE = "verify"
WAIT_NODE = "wait"
TERMINAL_NODE = "terminal"

_WAITING_STATUSES = frozenset({"waiting_user", "waiting_external", "waiting_agent"})
_TERMINAL_STATUSES = frozenset({"completed", "failed", "cancelled"})
_ROUTE_STATUSES = {
    "compact": frozenset({"running", *_WAITING_STATUSES}),
    "model": frozenset({"running"}),
    "tool": frozenset({"running"}),
    "verify": frozenset({"verifying"}),
    "wait": _WAITING_STATUSES,
    "terminal": _TERMINAL_STATUSES,
}


def _retry_transient_compact_error(error: Exception) -> bool:
    """Retry only errors explicitly classified as transient by Compact."""
    return bool(getattr(error, "is_transient_compact_error", False))


COMPACT_RETRY_POLICY = RetryPolicy(
    max_attempts=3,
    retry_on=_retry_transient_compact_error,
)


def _retry_safe_read_tool_error(error: Exception) -> bool:
    """Retry only failures already qualified by the durable Tool Ledger."""
    return isinstance(error, RetryableToolNodeError)


TOOL_RETRY_POLICY = RetryPolicy(
    max_attempts=SAFE_READ_MAX_ATTEMPTS,
    retry_on=_retry_safe_read_tool_error,
)


class RuntimeGraphContractError(RuntimeError):
    """Checkpoint state or invocation context violates the graph contract."""


@dataclass(frozen=True, slots=True)
class RuntimeGraphIdentity:
    """Observational identity for the currently deployed Runtime graph."""

    name: str
    version: str

    @property
    def compiled_name(self) -> str:
        return f"{self.name}@{self.version}"

    @classmethod
    def from_settings(cls, settings: Settings | None = None) -> "RuntimeGraphIdentity":
        runtime_settings = settings or get_settings()
        return cls(
            name=runtime_settings.AGENT_RUNTIME_GRAPH_NAME,
            version=runtime_settings.AGENT_RUNTIME_GRAPH_VERSION,
        )

    @classmethod
    def planning_from_settings(
        cls,
        settings: Settings | None = None,
    ) -> "RuntimeGraphIdentity":
        """Name the current Planning topology on the shared Checkpointer."""
        runtime_settings = settings or get_settings()
        return cls(
            name=f"{runtime_settings.AGENT_RUNTIME_GRAPH_NAME}_group_planning",
            version=runtime_settings.AGENT_RUNTIME_GRAPH_VERSION,
        )


@dataclass(frozen=True, slots=True)
class AgentRuntimeGraph:
    """Currently deployed compiled graph plus its trace identity."""

    identity: RuntimeGraphIdentity
    compiled: CompiledStateGraph


def _require_invocation_scope(
    context: RuntimeContext | None,
) -> RuntimeContext:
    if context is None:
        raise RuntimeGraphContractError("RuntimeContext is required")

    if not context.tenant_id or not context.run_id or not context.command_id:
        raise RuntimeGraphContractError(
            "RuntimeContext must carry tenant, Run, and Command identity"
        )
    return context


async def _execute_node(
    node: RuntimeNodeName,
    state: RuntimeGraphState,
    runtime: Runtime[RuntimeContext],
    identity: RuntimeGraphIdentity,
    *,
    resume_value: JsonValue | None = None,
) -> RuntimeStateUpdate:
    context = _require_invocation_scope(runtime.context)
    update = await context.executor.execute(
        node,
        state,
        context,
        resume_value=resume_value,
    )
    unexpected_keys = set(update) - {
        "lifecycle",
        "messages",
        "thread_summary",
        "summary_covered_through_message_id",
    }
    if unexpected_keys:
        raise RuntimeGraphContractError(
            "Runtime nodes returned unsupported Thread state fields: "
            + ", ".join(sorted(unexpected_keys))
        )
    lifecycle_update = update.get("lifecycle", {})
    if not isinstance(lifecycle_update, dict):
        raise RuntimeGraphContractError("Runtime node lifecycle update must be an object")

    lifecycle = {
        **state["lifecycle"],
        **lifecycle_update,
    }
    # Older checkpoints carried a bounded Command-ID receipt list. Command
    # ownership now lives in native checkpoint metadata and the durable inbox;
    # drop the legacy field whenever an old checkpoint advances.
    lifecycle.pop("last_applied_command_ids", None)
    lifecycle.pop("run_messages", None)
    lifecycle.pop("run_summary", None)
    lifecycle.pop("covered_through_run_message_id", None)
    lifecycle.pop("run_compact_error", None)
    lifecycle.pop("compact_forced", None)
    lifecycle.pop("compact_return_route", None)
    if node == "terminal":
        if lifecycle.get("status") not in _TERMINAL_STATUSES or lifecycle.get("next_route") != "terminal":
            raise RuntimeGraphContractError("terminal node must preserve a terminal lifecycle")
    normalized_update = dict(update)
    if not state.get("messages"):
        legacy_messages = runtime_messages_as_json(state)
        if legacy_messages:
            normalized_update["messages"] = [
                *legacy_messages,
                *cast(list, update.get("messages", [])),
            ]
    return cast(
        RuntimeStateUpdate,
        {
            **normalized_update,
            "lifecycle": lifecycle,
        },
    )


def _make_node(
    node: RuntimeNodeName,
    identity: RuntimeGraphIdentity,
) -> Callable[[RuntimeGraphState, Runtime[RuntimeContext]], Any]:
    async def execute(
        state: RuntimeGraphState,
        runtime: Runtime[RuntimeContext],
    ) -> RuntimeStateUpdate:
        return await _execute_node(node, state, runtime, identity)

    return execute


def _make_wait_node(
    identity: RuntimeGraphIdentity,
) -> Callable[[RuntimeGraphState, Runtime[RuntimeContext]], Any]:
    async def wait_for_resume(
        state: RuntimeGraphState,
        runtime: Runtime[RuntimeContext],
    ) -> RuntimeStateUpdate:
        _require_invocation_scope(runtime.context)
        waiting_request = state["lifecycle"].get("waiting_request")
        if not isinstance(waiting_request, dict):
            raise RuntimeGraphContractError("wait route requires a serializable waiting_request")
        resume_value = cast(JsonValue, interrupt(waiting_request))
        return await _execute_node(
            "wait",
            state,
            runtime,
            identity,
            resume_value=resume_value,
        )

    return wait_for_resume


def route_after_control(state: RuntimeGraphState) -> ControlRoute:
    """Route exclusively from authoritative lifecycle values in the checkpoint."""
    lifecycle = state["lifecycle"]
    route = lifecycle.get("next_route")
    status = lifecycle.get("status")
    if route not in _ROUTE_STATUSES:
        raise RuntimeGraphContractError(f"Unsupported control route: {route!r}")
    if status not in _ROUTE_STATUSES[route]:
        raise RuntimeGraphContractError(f"Lifecycle status {status!r} cannot use route {route!r}")
    return cast(ControlRoute, route)


def build_agent_runtime_graph(
    *,
    checkpointer: BaseCheckpointSaver[Any],
    settings: Settings | None = None,
    identity: RuntimeGraphIdentity | None = None,
) -> AgentRuntimeGraph:
    """Compile the current reusable graph for new and compatible old checkpoints."""
    identity = identity or RuntimeGraphIdentity.from_settings(settings)
    builder = StateGraph(RuntimeGraphState, context_schema=RuntimeContext)

    builder.add_node(CONTROL_GUARD_NODE, _make_node("control_guard", identity))
    builder.add_node(
        COMPACT_NODE,
        _make_node("compact", identity),
        retry_policy=COMPACT_RETRY_POLICY,
    )
    builder.add_node(MODEL_NODE, _make_node("model", identity))
    builder.add_node(
        TOOL_NODE,
        _make_node("tool", identity),
        retry_policy=TOOL_RETRY_POLICY,
    )
    builder.add_node(VERIFY_NODE, _make_node("verify", identity))
    builder.add_node(WAIT_NODE, _make_wait_node(identity))
    builder.add_node(TERMINAL_NODE, _make_node("terminal", identity))

    builder.add_edge(START, CONTROL_GUARD_NODE)
    builder.add_conditional_edges(
        CONTROL_GUARD_NODE,
        route_after_control,
        {
            "compact": COMPACT_NODE,
            "model": MODEL_NODE,
            "tool": TOOL_NODE,
            "verify": VERIFY_NODE,
            "wait": WAIT_NODE,
            "terminal": TERMINAL_NODE,
        },
    )
    builder.add_edge(COMPACT_NODE, CONTROL_GUARD_NODE)
    builder.add_edge(MODEL_NODE, CONTROL_GUARD_NODE)
    builder.add_edge(TOOL_NODE, CONTROL_GUARD_NODE)
    builder.add_edge(VERIFY_NODE, CONTROL_GUARD_NODE)
    builder.add_edge(WAIT_NODE, CONTROL_GUARD_NODE)
    builder.add_edge(TERMINAL_NODE, END)

    compiled = builder.compile(
        checkpointer=checkpointer,
        name=identity.compiled_name,
    )
    return AgentRuntimeGraph(identity=identity, compiled=compiled)
