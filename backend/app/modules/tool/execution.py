"""Fixed Tool execution bindings and bounded ordered scheduling."""

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Protocol

from app.infrastructure.errors import DomainError, InvalidInput
from app.modules.tool.contracts import (
    AvailableToolSet,
    CallScope,
    DefinitionSpec,
    ResolvedTool,
    ToolCall,
    ToolResult,
    canonical_json,
)

__all__ = ["CallScope", "ToolCall", "ToolResult"]


class ToolExecutor(Protocol):
    async def execute(self, tool: ResolvedTool, call: ToolCall, scope: CallScope) -> ToolResult: ...


@dataclass(frozen=True, slots=True)
class ExecutorBinding:
    key: str
    executor: ToolExecutor
    safe_parallel: bool = False
    # Builtin metadata is code-owned and cannot be overwritten by database data.
    builtin: DefinitionSpec | None = None


class ToolRegistry:
    def __init__(self, bindings: tuple[ExecutorBinding, ...]) -> None:
        if len(bindings) > 128 or len({binding.key for binding in bindings}) != len(bindings):
            raise InvalidInput("Executor registry identities are invalid")
        self._bindings: Mapping[str, ExecutorBinding] = MappingProxyType({binding.key: binding for binding in bindings})

    def bind(self, tool: ResolvedTool) -> ExecutorBinding:
        binding = self._bindings.get(tool.definition.spec.executor_key)
        if binding is None:
            raise InvalidInput("Tool executor is not installed")
        if tool.definition.spec.source == "builtin" and binding.builtin != tool.definition.spec:
            raise InvalidInput("Builtin Tool definition differs from its code-owned contract")
        if binding.builtin is not None and binding.builtin != tool.definition.spec:
            raise InvalidInput("Builtin executor cannot execute another definition")
        return binding


class ToolScheduler:
    def __init__(self, registry: ToolRegistry, *, max_parallel: int, timeout_seconds: float) -> None:
        if not 1 <= max_parallel <= 32 or not 0 < timeout_seconds <= 600:
            raise InvalidInput("Tool scheduling limits are invalid")
        self._registry = registry
        self._semaphore = asyncio.Semaphore(max_parallel)
        self._timeout = timeout_seconds

    async def execute(
        self, available: AvailableToolSet, calls: tuple[ToolCall, ...], scope: CallScope
    ) -> tuple[ToolResult, ...]:
        if (
            scope.tenant_id != available.tenant_id
            or scope.agent_id != available.agent_id
            or len(calls) > 128
            or len({call.id for call in calls}) != len(calls)
        ):
            raise InvalidInput("Tool batch identity or scope is invalid")
        by_name = {tool.definition.spec.name: tool for tool in available.tools}
        results: list[ToolResult] = []
        pending: list[tuple[ResolvedTool, ToolCall, ExecutorBinding]] = []

        async def drain() -> None:
            if not pending:
                return
            tasks: list[asyncio.Task[ToolResult]] = []
            # TaskGroup cancels and awaits siblings on defects/cancellation; no orphan work.
            async with asyncio.TaskGroup() as group:
                for tool, call, binding in pending:
                    tasks.append(group.create_task(self._one(tool, call, binding, scope)))
            results.extend(task.result() for task in tasks)
            pending.clear()

        for call in calls:
            tool = by_name.get(call.name)
            if tool is None or call.name not in available.direct_names:
                await drain()
                results.append(_error(call.id, "Tool is not exposed in this Run"))
                continue
            try:
                binding = self._registry.bind(tool)
            except InvalidInput:
                await drain()
                results.append(_error(call.id, "Tool executor is unavailable"))
                continue
            if binding.safe_parallel:
                pending.append((tool, call, binding))
            else:
                await drain()
                results.append(await self._one(tool, call, binding, scope))
        await drain()
        return tuple(results)

    async def _one(self, tool: ResolvedTool, call: ToolCall, binding: ExecutorBinding, scope: CallScope) -> ToolResult:
        async with self._semaphore:
            try:
                async with asyncio.timeout(self._timeout):
                    result = await binding.executor.execute(tool, call, scope)
            except TimeoutError:
                return ToolResult(
                    call.id,
                    "uncertain",
                    canonical_json(
                        {"message": "Tool timed out; its external effect is unknown. Verify before repeating."}
                    ),
                )
            except DomainError:
                return _error(call.id, "Tool could not complete; check its input and authorized resources")
            if result.call_id != call.id:
                raise RuntimeError("Executor returned a different Tool Call identity")
            return result


def _error(call_id: str, message: str) -> ToolResult:
    return ToolResult(call_id, "error", canonical_json({"message": message}))
