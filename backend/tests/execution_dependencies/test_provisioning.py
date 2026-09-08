import asyncio
from dataclasses import replace
from uuid import uuid4

import pytest
from sqlalchemy import update

from app.execution_dependencies.provisioning import BUILTIN_DEFINITIONS, provision_builtin_tools
from app.infrastructure.errors import AccessDenied, Conflict, InvalidInput, NotFound
from app.modules.agent.public import AgentService
from app.modules.tool.models import AgentToolGrantRecord
from app.modules.tool.public import ToolResolutionScope, ToolService
from app.modules.tool.repository import ToolRepository

from .test_workspace_tools import prepared as prepared  # noqa: PLC0414 - expose the shared pytest fixture.


async def test_explicit_grants_are_idempotent_and_agent_scoped(prepared, transaction_factory):
    _, _, scope, principal, other, _, _ = prepared
    async with transaction_factory() as tx:
        first = await provision_builtin_tools(tx, principal, agent_id=scope.agent_id)
        second = await provision_builtin_tools(tx, principal, agent_id=scope.agent_id)
        assert first == second
    async with transaction_factory() as tx:
        tools = ToolService(tx)
        available = await tools.resolve(ToolResolutionScope(principal, scope.agent_id, "main"),
                                        direct_names=frozenset({"search_tools"}))
        assert {tool.definition.spec for tool in available.tools} == {
            definition for definition in BUILTIN_DEFINITIONS if definition.name != "todo"}
        captured = await tools.capture_authorized(ToolResolutionScope(principal, scope.agent_id, "main"))
        assert {tool.definition.spec for tool in captured.tools} == set(BUILTIN_DEFINITIONS)
        assert available.direct_names == frozenset({"search_tools"})
        sub = await tools.resolve(ToolResolutionScope(principal, scope.agent_id, "sub"))
        assert "distill_memory" not in {tool.definition.spec.name for tool in sub.tools}
        assert "todo" in {tool.definition.spec.name for tool in sub.tools}
        assert not {"task", "wait_for_tasks"} & {tool.definition.spec.name for tool in sub.tools}
        assert (await tools.resolve(ToolResolutionScope(principal, other.id, "main"))).tools == ()


async def test_failed_creation_transaction_leaves_no_default_grants(prepared, transaction_factory):
    _, _, scope, principal, _, _, _ = prepared
    with pytest.raises(RuntimeError, match="abort"):
        async with transaction_factory() as tx:
            agents = AgentService(tx)
            existing = await agents.get(principal, agent_id=scope.agent_id)
            created = await agents.create(principal, name="Atomic creation", soul="Useful", timezone="UTC",
                                          model_id=existing.model_id)
            await provision_builtin_tools(tx, principal, agent_id=created.id)
            raise RuntimeError("abort")
    async with transaction_factory() as tx:
        with pytest.raises(NotFound):
            await AgentService(tx).get(principal, agent_id=created.id)
        assert (await ToolService(tx).resolve(ToolResolutionScope(principal, scope.agent_id, "main"))).tools == ()


async def test_provisioning_cannot_restore_revoked_grants(prepared, transaction_factory):
    _, _, scope, principal, _, _, _ = prepared
    async with transaction_factory() as tx:
        definitions = await provision_builtin_tools(tx, principal, agent_id=scope.agent_id)
    async with transaction_factory() as tx:
        await ToolService(tx).revoke_grant(principal, agent_id=scope.agent_id, definition_id=definitions[0].id)
    with pytest.raises(Conflict):
        async with transaction_factory() as tx:
            await provision_builtin_tools(tx, principal, agent_id=scope.agent_id)
    async with transaction_factory() as tx:
        available = await ToolService(tx).resolve(ToolResolutionScope(principal, scope.agent_id, "main"))
        assert definitions[0].id not in {tool.definition.id for tool in available.tools}


async def test_provisioning_rejects_member_and_wrong_agent(prepared, transaction_factory):
    _, _, scope, principal, _, _, _ = prepared
    async with transaction_factory() as tx:
        with pytest.raises(AccessDenied):
            await provision_builtin_tools(tx, replace(principal, role="member"), agent_id=scope.agent_id)
        with pytest.raises(NotFound):
            await provision_builtin_tools(tx, principal, agent_id=uuid4())


async def test_parallel_agents_share_one_definition_registration(prepared, transaction_factory, monkeypatch):
    _, _, scope, principal, other, _, _ = prepared
    barrier = asyncio.Barrier(2)
    original = ToolRepository.definition_named

    async def synchronized_lookup(repository, tenant_id, name):
        result = await original(repository, tenant_id, name)
        if name == "search_tools" and result is None:
            await barrier.wait()
        return result

    monkeypatch.setattr(ToolRepository, "definition_named", synchronized_lookup)

    async def provision(agent_id):
        async with transaction_factory() as tx:
            return await provision_builtin_tools(tx, principal, agent_id=agent_id)

    async with asyncio.timeout(10):
        first, second = await asyncio.gather(provision(scope.agent_id), provision(other.id))
    assert first == second


async def test_parallel_incompatible_registration_does_not_overwrite_winner(prepared, transaction_factory, monkeypatch):
    _, _, _, principal, _, _, _ = prepared
    barrier = asyncio.Barrier(2)
    original = ToolRepository.definition_named

    async def synchronized_lookup(repository, tenant_id, name):
        result = await original(repository, tenant_id, name)
        if result is None:
            await barrier.wait()
        return result

    monkeypatch.setattr(ToolRepository, "definition_named", synchronized_lookup)

    async def register(spec):
        async with transaction_factory() as tx:
            return await ToolService(tx).register_definition(principal, definition=spec)

    spec = BUILTIN_DEFINITIONS[0]
    async with asyncio.timeout(10):
        results = await asyncio.gather(register(spec), register(replace(spec, description="Different")),
                                       return_exceptions=True)
    assert sum(isinstance(result, Conflict) for result in results) == 1
    winner = next(result for result in results if not isinstance(result, BaseException))
    async with transaction_factory() as tx:
        assert await ToolService(tx).register_definition(principal, definition=winner.spec) == winner


async def test_parallel_provisioning_of_same_agent_reuses_grants(prepared, transaction_factory, monkeypatch):
    _, _, scope, principal, _, _, _ = prepared
    async with transaction_factory() as tx:
        definitions = [await ToolService(tx).register_definition(principal, definition=spec)
                       for spec in BUILTIN_DEFINITIONS]
    barrier = asyncio.Barrier(2)
    original = ToolRepository.grant_for_tool

    async def synchronized_lookup(repository, tenant_id, agent_id, definition_id):
        result = await original(repository, tenant_id, agent_id, definition_id)
        if definition_id == definitions[0].id and result is None:
            await barrier.wait()
        return result

    monkeypatch.setattr(ToolRepository, "grant_for_tool", synchronized_lookup)

    async def provision():
        async with transaction_factory() as tx:
            return await provision_builtin_tools(tx, principal, agent_id=scope.agent_id)

    async with asyncio.timeout(10):
        results = await asyncio.gather(provision(), provision(), return_exceptions=True)
    assert results == [tuple(definitions), tuple(definitions)]


@pytest.mark.parametrize("changes", [{"configuration_version": 2}, {"non_secret_config": {"unsupported": True}}])
async def test_provisioning_rejects_unknown_persisted_grant_configuration(prepared, transaction_factory, changes):
    _, _, scope, principal, _, _, _ = prepared
    async with transaction_factory() as tx:
        definitions = await provision_builtin_tools(tx, principal, agent_id=scope.agent_id)
    async with transaction_factory() as tx:
        await tx.session.execute(update(AgentToolGrantRecord).where(
            AgentToolGrantRecord.tenant_id == principal.tenant_id,
            AgentToolGrantRecord.agent_id == scope.agent_id,
            AgentToolGrantRecord.tool_definition_id == definitions[0].id,
        ).values(**changes))
    with pytest.raises(InvalidInput, match="configuration"):
        async with transaction_factory() as tx:
            await provision_builtin_tools(tx, principal, agent_id=scope.agent_id)
