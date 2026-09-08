from uuid import uuid4

import pytest
from modules.tool.test_service import enabled_sources, setup

from app.infrastructure.errors import AccessDenied, InvalidInput
from app.modules.tool.public import (
    AgentToolResolutionScope,
    AuthorizedToolSet,
    CallScope,
    DefinitionSpec,
    ResolvedTool,
    ToolCall,
    ToolDefinition,
    ToolRegistry,
    ToolResolutionScope,
    ToolScheduler,
    ToolService,
)


async def test_capture_preserves_child_tools_without_live_grant_expansion(transaction_factory, model_acceptance):
    principal, agent, _, _, _ = await setup(transaction_factory, model_acceptance)
    names = frozenset({"task", "todo", "read", "send_message_to_agent", "distill_memory", "wait_for_tasks"})
    async with transaction_factory() as tx:
        service = ToolService(tx, enabled_sources=enabled_sources)
        definitions = {}
        for name in sorted(names):
            definition = await service.register_definition(
                principal, definition=DefinitionSpec(name, name, '{"type":"object"}', name + ".v1", "product")
            )
            await service.grant(principal, agent_id=agent, definition_id=definition.id)
            definitions[name] = definition
        scope = AgentToolResolutionScope(principal.tenant_id, agent, "main")
        captured = await service.capture_authorized(scope)
        assert {tool.definition.spec.name for tool in captured.tools} == names
        main = captured.for_role("main", direct_names=names)
        child = captured.for_role("sub", direct_names=names)
        assert {tool.spec.name for tool in main.visible()} == names - {"todo"}
        assert {tool.spec.name for tool in child.visible()} == {"todo", "read"}
        assert await service.resolve(scope, direct_names=names) == main
        assert await service.capture_authorized(ToolResolutionScope(principal, agent, "sub")) == captured
        await service.revoke_grant(principal, agent_id=agent, definition_id=definitions["read"].id)
        installed = await service.register_definition(
            principal, definition=DefinitionSpec("new", "New", '{"type":"object"}', "new.v1", "product")
        )
        await service.grant(principal, agent_id=agent, definition_id=installed.id)
    # Role derivation needs no transaction or live service after capture.
    assert captured.for_role("main", direct_names=names) == main
    assert captured.for_role("sub", direct_names=names) == child
    assert not child.search("task")
    with pytest.raises(InvalidInput):
        child.expose(frozenset({"task"}))
    async with transaction_factory() as tx:
        fresh = await ToolService(tx).capture_authorized(scope)
        assert {tool.definition.spec.name for tool in fresh.tools} == (names - {"read"}) | {"new"}


def test_capture_rejects_cross_tenant_duplicate_oversized_and_invalid_role():
    tenant, agent = uuid4(), uuid4()
    tool = ResolvedTool(
        ToolDefinition(uuid4(), tenant, DefinitionSpec("read", "Read", '{"type":"object"}', "read.v1", "product")),
        None,
    )
    with pytest.raises(AccessDenied):
        AuthorizedToolSet(uuid4(), agent, (tool,))
    with pytest.raises(InvalidInput):
        AuthorizedToolSet(tenant, agent, (tool, tool))
    with pytest.raises(InvalidInput):
        AuthorizedToolSet(tenant, agent, (tool,) * 129)
    captured = AuthorizedToolSet(tenant, agent, (tool,))
    with pytest.raises(InvalidInput):
        captured.for_role("invalid")
    assert not captured.for_role("main", direct_names=frozenset({"ungranted"})).visible()
    assert captured.for_role("main").tools == (tool,)
    assert AuthorizedToolSet(tenant, agent, ()).for_role("sub").tools == ()


async def test_child_role_view_denies_main_only_calls_at_scheduler():
    tenant, agent = uuid4(), uuid4()
    tools = tuple(
        ResolvedTool(ToolDefinition(uuid4(), tenant,
            DefinitionSpec(name, name, '{"type":"object"}', name + ".v1", "product")), None)
        for name in ("task", "send_message_to_agent", "distill_memory", "wait_for_tasks")
    )
    child = AuthorizedToolSet(tenant, agent, tools).for_role(
        "sub", direct_names=frozenset(tool.definition.spec.name for tool in tools)
    )
    scheduler = ToolScheduler(ToolRegistry(()), max_parallel=1, timeout_seconds=1)
    results = await scheduler.execute(
        child, tuple(ToolCall(str(index), tool.definition.spec.name, "{}") for index, tool in enumerate(tools)),
        CallScope(tenant, agent, uuid4()),
    )
    assert len(results) == len(tools)
    assert all(result.status == "error" for result in results)
