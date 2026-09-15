import asyncio

import pytest
from modules.capability_market.test_service import Observations, seed, spec

from app.infrastructure.errors import InvalidInput
from app.infrastructure.object_storage.local import LocalStorageBackend
from app.modules.capability_market.public import CapabilityMarketService
from app.modules.identity_tenant.public import PlatformPrincipal
from app.modules.tool.public import AgentInstallScope
from app.modules.workspace.public import WorkspaceService


async def setup(test_database, tmp_path):
    principal, first, second = await seed(test_database.sessions)
    audit = Observations()
    storage = LocalStorageBackend(str(tmp_path))
    workspace = WorkspaceService(test_database.sessions, storage, audit)
    market = CapabilityMarketService(test_database.sessions, audit, workspace)
    return principal, first, second, storage, workspace, market


@pytest.mark.parametrize("agent_owned", [False, True])
async def test_disabled_tenant_registration_shadows_enabled_platform_skill(test_database, tmp_path, agent_owned):
    principal, agent, _, storage, workspace, market = await setup(test_database, tmp_path)
    platform = PlatformPrincipal(principal.account_id, principal.tenant_id, "platform_admin")
    template = await market.register_platform(platform, spec=spec("skill"))
    tenant_source = await market.materialize(principal, item_id=template.item.id)
    await market.set_enabled(principal, item_id=tenant_source.item.id, enabled=False)
    prepared = await workspace.prepare_skill_package({"SKILL.md": b"Must not install"})
    if agent_owned:
        result = await market.install_skill_for_agent(AgentInstallScope(principal.tenant_id, agent.id),
            item_id=template.item.id, skill_name="blocked", prepared=prepared, shared=True)
    else:
        result = await market.install_skill(principal, agent_id=agent.id, item_id=template.item.id,
            skill_name="blocked", prepared=prepared, shared=True)
    assert not result.activated and result.activation_error == "not_found"
    assert result.source.item.id == tenant_source.item.id and not result.source.item.enabled
    assert await workspace.lookup_shared_skill(principal, catalog_item_id=tenant_source.item.id) is None
    assert not await storage.exists(prepared.storage_key)


async def test_disable_after_package_validation_prevents_publication(test_database, tmp_path, monkeypatch):
    principal, agent, _, storage, workspace, market = await setup(test_database, tmp_path)
    item = await market.register(principal, spec=spec("skill"))
    prepared = await workspace.prepare_skill_package({"SKILL.md": b"Race"})
    original = workspace._read_package
    async def disable_after_validation(*args, **kwargs):
        result = await original(*args, **kwargs)
        await market.set_enabled(principal, item_id=item.item.id, enabled=False)
        return result
    monkeypatch.setattr(workspace, "_read_package", disable_after_validation)
    result = await asyncio.wait_for(market.install_skill(principal, agent_id=agent.id,
        item_id=item.item.id, skill_name="race", prepared=prepared, shared=True), 2)
    assert not result.activated
    assert await workspace.lookup_shared_skill(principal, catalog_item_id=item.item.id) is None
    assert not await storage.exists(prepared.storage_key)


@pytest.mark.parametrize("reuse_shared", [False, True])
async def test_disable_serializes_after_successful_publication_or_shared_binding(test_database, tmp_path, monkeypatch, reuse_shared):
    principal, first, second, storage, workspace, market = await setup(test_database, tmp_path)
    item = await market.register(principal, spec=spec("skill"))
    if reuse_shared:
        initial = await market.install_skill(principal, agent_id=first.id, item_id=item.item.id,
            skill_name="race", prepared=await workspace.prepare_skill_package({"SKILL.md": b"Shared"}), shared=True)
        assert initial.activated
    entered, release = asyncio.Event(), asyncio.Event()
    original = market.assert_active_skill_source
    async def held_guard(*args, **kwargs):
        await original(*args, **kwargs)
        entered.set()
        await release.wait()
    monkeypatch.setattr(market, "assert_active_skill_source", held_guard)
    prepared = await workspace.prepare_skill_package({"SKILL.md": b"Publish"})
    install = asyncio.create_task(market.install_skill(principal, agent_id=second.id,
        item_id=item.item.id, skill_name="race", prepared=prepared, shared=True))
    disable = None
    try:
        await asyncio.wait_for(entered.wait(), 2)
        disable = asyncio.create_task(market.set_enabled(principal, item_id=item.item.id, enabled=False))
        await asyncio.sleep(.03)
        assert not disable.done()
    finally:
        release.set()
        result = await install
        if disable is not None:
            disabled = await disable
    assert result.activated
    assert not disabled.enabled
    assert await workspace.lookup_shared_skill(principal, catalog_item_id=item.item.id) is not None
    if not reuse_shared:
        assert await storage.exists(prepared.storage_key)


async def test_source_backed_binding_cannot_bypass_publication_admission(test_database, tmp_path):
    principal, first, second, _, workspace, market = await setup(test_database, tmp_path)
    item = await market.register(principal, spec=spec("skill"))
    initial = await market.install_skill(principal, agent_id=first.id, item_id=item.item.id,
        skill_name="shared", prepared=await workspace.prepare_skill_package({"SKILL.md": b"Shared"}), shared=True)
    with pytest.raises(InvalidInput, match="source guard"):
        await workspace.bind_skill(principal, agent_id=second.id, skill_name="shared",
            package_id=initial.skill_binding.package_id)
