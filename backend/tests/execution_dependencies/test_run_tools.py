import asyncio
import json
from dataclasses import replace
from uuid import uuid4

import pytest

from app.execution_dependencies.run_tools import RUN_TOOL_DEFINITIONS, run_tool_bindings
from app.infrastructure.errors import AccessDenied
from app.modules.tool.public import CallScope, ResolvedTool, ToolCall, ToolDefinition


class Operations:
    def __init__(self):
        self.calls = []
        self.child = uuid4()

    async def delegate(self, call_id, work):
        self.calls.append(("delegate", call_id, work))
        return self.child

    async def resume(self, *args):
        self.calls.append(("resume", *args))

    async def inspect(self, *args):
        self.calls.append(("inspect", *args))
        return {"status": "Running", "entries": []}


class Harness:
    def __init__(self, role="main"):
        self.scope = CallScope(uuid4(), uuid4(), uuid4())
        self.operations = Operations()
        self.bindings = run_tool_bindings(scope=self.scope, role=role, operations=self.operations)

    async def call(self, name, arguments, *, scope=None, forged=False):
        binding = next(value for value in self.bindings if value.builtin.name == name)
        definition = binding.builtin
        if forged:
            definition = replace(definition, description="forged")
        tool = ResolvedTool(ToolDefinition(uuid4(), self.scope.tenant_id, definition), None)
        call = ToolCall("call-1", name, json.dumps(arguments))
        return await binding.executor.execute(tool, call, scope or self.scope)


async def test_delegate_accepts_without_waiting_for_child_completion():
    h = Harness()
    result = await asyncio.wait_for(h.call("task", {"action": "delegate", "work": "Compare alternatives"}), 0.1)
    assert result.status == "success"
    assert json.loads(result.content_json) == {"accepted": True, "child_run_id": str(h.operations.child)}
    assert h.operations.calls == [("delegate", "call-1", "Compare alternatives")]


async def test_resume_and_inspect_forward_correlation_and_bounds():
    h = Harness()
    child = str(h.operations.child)
    result = await h.call("task", {"action": "resume", "child_run_id": child, "waiting_reference": "question-1", "answer": "yes"})
    assert result.status == "success"
    assert h.operations.calls[-1] == ("resume", "call-1", h.operations.child, "question-1", "yes")
    await h.call("task", {"action": "inspect", "child_run_id": child})
    assert h.operations.calls[-1] == ("inspect", h.operations.child, 0, 0)


@pytest.mark.parametrize("args", [
    {"action": "delegate", "work": ""}, {"action": "delegate", "work": "x" * 8193},
    {"action": "delegate", "work": "x", "tenant_id": "forged"},
    {"action": "inspect", "child_run_id": "not-uuid"},
    {"action": "inspect", "child_run_id": str(uuid4()), "content_offset": True},
    {"action": "inspect", "child_run_id": str(uuid4()), "content_offset": 17000001},
    {"action": "resume", "child_run_id": str(uuid4()), "answer": "x"},
])
async def test_invalid_task_arguments_have_no_owner_effect(args):
    h = Harness()
    assert (await h.call("task", args)).status == "error"
    assert not h.operations.calls


async def test_scope_and_definition_reject_before_owner_port():
    h = Harness()
    args = {"action": "delegate", "work": "work"}
    assert (await h.call("task", args, scope=replace(h.scope, run_id=uuid4()))).status == "error"
    assert (await h.call("task", args, forged=True)).status == "error"
    assert not h.operations.calls


async def test_role_bindings_and_todo_remain_only_a_planning_result():
    main, sub = Harness(), Harness("sub")
    assert {b.builtin.name for b in main.bindings} == {"task", "need_input", "wait_for_tasks"}
    assert {b.builtin.name for b in sub.bindings} == {"todo", "need_input"}
    items = [{"text": "x" * 512, "status": "pending"}] * 64
    result = await sub.call("todo", {"items": items})
    assert result.status == "success" and json.loads(result.content_json) == {"items": items}
    assert not sub.operations.calls
    assert (await sub.call("todo", {"items": items + items[:1]})).status == "error"
    assert (await sub.call("todo", {"items": [{"text": "x", "status": "done"}]})).status == "error"
    assert (await sub.call("todo", {"items": []})).status == "success"


async def test_wait_markers_do_not_call_lifecycle_ports():
    h = Harness()
    question = "x" * 8192
    assert json.loads((await h.call("need_input", {"question": question})).content_json) == {"need_input": True, "question": question}
    assert json.loads((await h.call("wait_for_tasks", {})).content_json) == {"wait_for_tasks": True}
    assert (await h.call("need_input", {"question": question + "x"})).status == "error"
    assert (await h.call("wait_for_tasks", {"question": "unexpected"})).status == "error"
    assert not h.operations.calls


async def test_domain_denial_is_normalized_and_internal_error_is_not_hidden():
    h = Harness()
    async def denied(*args):
        raise AccessDenied("Child is not delegated by this parent")
    h.operations.delegate = denied
    assert (await h.call("task", {"action": "delegate", "work": "x"})).status == "error"
    async def broken(*args):
        raise RuntimeError("internal defect")
    h.operations.delegate = broken
    with pytest.raises(RuntimeError, match="internal defect"):
        await h.call("task", {"action": "delegate", "work": "x"})


async def test_cancelled_delegation_propagates_without_success_result():
    h = Harness()
    entered = asyncio.Event()
    stopped = asyncio.Event()
    async def blocked(*args):
        entered.set()
        try:
            await asyncio.Future()
        finally:
            stopped.set()
    h.operations.delegate = blocked
    task = asyncio.create_task(h.call("task", {"action": "delegate", "work": "x"}))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert stopped.is_set()


def test_definitions_are_unique_code_owned():
    assert len({d.executor_key for d in RUN_TOOL_DEFINITIONS}) == 4
    assert all(d.source == "builtin" for d in RUN_TOOL_DEFINITIONS)


@pytest.mark.parametrize("name,role,args", [("task", "sub", {"action": "delegate", "work": "x"}),
    ("wait_for_tasks", "sub", {}), ("todo", "main", {"items": []})])
async def test_miscomposed_executor_enforces_role_without_owner_effect(name, role, args):
    from app.execution_dependencies.run_tools import _RunExecutor
    h = Harness()
    definition = next(d for d in RUN_TOOL_DEFINITIONS if d.name == name)
    executor = _RunExecutor(definition, h.scope, role, h.operations)
    tool = ResolvedTool(ToolDefinition(uuid4(), h.scope.tenant_id, definition), None)
    result = await executor.execute(tool, ToolCall("call", name, json.dumps(args)), h.scope)
    assert result.status == "error"
    assert not h.operations.calls


@pytest.mark.parametrize("name,role,arguments", [
    ("task", "sub", {"action": "delegate", "work": "must not execute"}),
    ("wait_for_tasks", "sub", {}),
    ("todo", "main", {"items": []}),
])
async def test_executor_rejects_wrong_role_even_if_ineligible_definition_was_injected(name, role, arguments):
    from app.execution_dependencies.run_tools import _RunExecutor
    scope = CallScope(uuid4(), uuid4(), uuid4())
    operations = Operations()
    definition = next(value for value in RUN_TOOL_DEFINITIONS if value.name == name)
    executor = _RunExecutor(definition, scope, role, operations)
    tool = ResolvedTool(ToolDefinition(uuid4(), scope.tenant_id, definition), None)
    result = await executor.execute(tool, ToolCall("wrong-role", name, json.dumps(arguments)), scope)
    assert result.status == "error"
    assert json.loads(result.content_json)["code"] == "access_denied"
    assert not operations.calls
