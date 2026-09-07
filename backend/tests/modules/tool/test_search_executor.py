import json
from dataclasses import replace
from uuid import uuid4

import pytest

from app.modules.tool.public import (
    SEARCH_TOOLS_DEFINITION,
    AvailableToolSet,
    CallScope,
    DefinitionSpec,
    ExecutorBinding,
    ResolvedTool,
    ToolCall,
    ToolDefinition,
    ToolRegistry,
    ToolResult,
    ToolScheduler,
    ToolSearchExecutor,
)


def setup():
    scope = CallScope(uuid4(), uuid4(), uuid4())
    definitions = (SEARCH_TOOLS_DEFINITION, DefinitionSpec(
        "read_report", "Read a report", '{"type":"object"}', "read_report.v1", "builtin",
    ))
    tools = tuple(ResolvedTool(ToolDefinition(uuid4(), scope.tenant_id, definition), None) for definition in definitions)
    available = AvailableToolSet(scope.tenant_id, scope.agent_id, tools, frozenset({"search_tools"}))
    exposure = ToolSearchExecutor(available, scope)

    class Reader:
        async def execute(self, tool, call, scope):
            return ToolResult(call.id, "success", '{"report":"actual executor ran"}')

    scheduler = ToolScheduler(ToolRegistry((exposure.binding(), ExecutorBinding(
        "read_report.v1", Reader(), builtin=definitions[1],
    ))), max_parallel=2, timeout_seconds=1)
    return available, exposure, scheduler, scope


async def test_search_exposes_only_fixed_authorized_tools_to_next_batch():
    original, exposure, scheduler, scope = setup()
    read = ToolCall("read", "read_report", "{}")
    assert (await scheduler.execute(exposure.available, (read,), scope))[0].status == "error"
    found = (await scheduler.execute(exposure.available, (
        ToolCall("search", "search_tools", '{"query":"report"}'),
    ), scope))[0]
    assert json.loads(found.content_json) == {"tools": ["read_report"]}
    assert original.direct_names == frozenset({"search_tools"})
    assert exposure.available.tools is original.tools
    assert len(exposure.available.visible()) == 2
    assert (await scheduler.execute(exposure.available, (read,), scope))[0].status == "success"
    unknown = (await scheduler.execute(exposure.available, (
        ToolCall("unknown", "search_tools", '{"query":"newly_installed"}'),
    ), scope))[0]
    assert json.loads(unknown.content_json) == {"tools": []}


@pytest.mark.parametrize("arguments", [
    '{"query":"report","limit":true}', '{"query":"report","limit":0}',
    '{"query":"report","limit":21}', '{"query":""}', '{"query":[]}',
    '{"query":"report","tenant_id":"other"}',
])
async def test_invalid_search_returns_error_without_changing_exposure(arguments):
    original, exposure, scheduler, scope = setup()
    result = await scheduler.execute(exposure.available, (ToolCall("search", "search_tools", arguments),), scope)
    assert result[0].status == "error" and exposure.available is original


async def test_search_cannot_change_another_runs_exposure():
    original, exposure, scheduler, scope = setup()
    result = await scheduler.execute(exposure.available, (
        ToolCall("search", "search_tools", '{"query":"report"}'),
    ), replace(scope, run_id=uuid4()))
    assert result[0].status == "error" and exposure.available is original


async def test_search_does_not_expand_a_batch_already_submitted():
    original, exposure, scheduler, scope = setup()
    results = await scheduler.execute(original, (
        ToolCall("search", "search_tools", '{"query":"report"}'),
        ToolCall("read", "read_report", "{}"),
    ), scope)
    assert [result.status for result in results] == ["success", "error"]
    assert "read_report" in exposure.available.direct_names
