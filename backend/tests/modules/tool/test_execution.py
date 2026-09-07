import asyncio
from uuid import uuid4

import pytest

from app.infrastructure.errors import InvalidInput
from app.modules.tool.execution import CallScope, ExecutorBinding, ToolCall, ToolRegistry, ToolResult, ToolScheduler
from app.modules.tool.public import AvailableToolSet, DefinitionSpec, ResolvedTool, ToolDefinition, role_eligible


def setup_tools(*names):
    tenant, agent = uuid4(), uuid4()
    tools = tuple(
        ResolvedTool(
            ToolDefinition(
                uuid4(), tenant, DefinitionSpec(name, "Search reports", '{"type":"object"}', name + ".v1", "product")
            ),
            None,
        )
        for name in names
    )
    return AvailableToolSet(tenant, agent, tools, frozenset(names)), CallScope(tenant, agent, uuid4())


async def test_parallel_calls_keep_result_order_and_serial_barriers():
    available, scope = setup_tools("read", "write")
    events = []
    active = 0
    maximum = 0

    class Executor:
        async def execute(self, tool, call, scope):
            nonlocal active, maximum
            events.append(("start", call.id))
            active += 1
            maximum = max(active, maximum)
            await asyncio.sleep(0.01 if call.id == "a" else 0)
            active -= 1
            events.append(("end", call.id))
            return ToolResult(call.id, "success", "{}")

    executor = Executor()
    scheduler = ToolScheduler(
        ToolRegistry((ExecutorBinding("read.v1", executor, True), ExecutorBinding("write.v1", executor))),
        max_parallel=2,
        timeout_seconds=1,
    )
    result = await scheduler.execute(
        available,
        tuple(ToolCall(i, name, "{}") for i, name in (("a", "read"), ("b", "read"), ("c", "write"), ("d", "read"))),
        scope,
    )
    assert [r.call_id for r in result] == ["a", "b", "c", "d"]
    assert maximum == 2
    assert events.index(("start", "c")) > events.index(("end", "a"))
    assert events.index(("start", "d")) > events.index(("end", "c"))


async def test_unexposed_tool_does_not_execute_and_search_only_exposes_fixed_set():
    available, scope = setup_tools("read")
    hidden = AvailableToolSet(available.tenant_id, available.agent_id, available.tools, frozenset())

    class Executor:
        async def execute(self, *args):
            pytest.fail("unexposed tool ran")

    scheduler = ToolScheduler(
        ToolRegistry((ExecutorBinding("read.v1", Executor()),)), max_parallel=1, timeout_seconds=1
    )
    assert (await scheduler.execute(hidden, (ToolCall("x", "read", "{}"),), scope))[0].status == "error"
    assert hidden.search("reports")[0].spec.name == "read"
    assert hidden.expose(frozenset({"read"})).visible()[0].spec.name == "read"
    assert hidden.visible() == ()
    with pytest.raises(InvalidInput):
        hidden.expose(frozenset({"newly_installed"}))


async def test_timeout_is_uncertain_and_cancel_releases_all_children():
    available, scope = setup_tools("read")
    active = set()
    started = asyncio.Event()

    class Executor:
        async def execute(self, tool, call, scope):
            active.add(call.id)
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                active.remove(call.id)

    scheduler = ToolScheduler(
        ToolRegistry((ExecutorBinding("read.v1", Executor(), True),)), max_parallel=2, timeout_seconds=0.01
    )
    assert (await scheduler.execute(available, (ToolCall("x", "read", "{}"),), scope))[0].status == "uncertain"
    assert not active
    started.clear()
    task = asyncio.create_task(
        scheduler.execute(available, (ToolCall("a", "read", "{}"), ToolCall("b", "read", "{}")), scope)
    )
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not active


def test_builtin_cannot_be_redefined_and_roles_are_enforced():
    available, _ = setup_tools("read")
    original = available.tools[0].definition.spec
    builtin = DefinitionSpec(
        original.name, original.description, original.input_schema_json, original.executor_key, "builtin"
    )

    class Executor:
        async def execute(self, *args):
            raise AssertionError

    registry = ToolRegistry((ExecutorBinding("read.v1", Executor(), builtin=builtin),))
    with pytest.raises(InvalidInput):
        registry.bind(available.tools[0])
    assert not role_eligible("task", "sub")
    assert not role_eligible("todo", "main")
    assert role_eligible("todo", "sub")


def test_call_and_result_byte_bounds_and_invalid_json():
    with pytest.raises(InvalidInput):
        ToolCall("x", "read", '{"x":NaN}')
    with pytest.raises(InvalidInput):
        ToolCall("x", "read", '{"x":"' + "中" * 22000 + '"}')
    with pytest.raises(InvalidInput):
        ToolResult("x", "success", '{"x":"' + "中" * 88000 + '"}')
