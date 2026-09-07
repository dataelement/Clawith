import asyncio
import os
from dataclasses import replace
from uuid import uuid4

import pytest
from model_support import validate_draft_model
from sqlalchemy import func, select, text

from app.infrastructure.errors import AccessDenied, Conflict, InvalidInput, NotFound
from app.infrastructure.object_storage.local import LocalStorageBackend
from app.infrastructure.transactions import transaction
from app.modules.agent.public import AgentService
from app.modules.capability_market.models import CapabilityCatalogItemRecord
from app.modules.capability_market.public import CapabilityMarketService, CatalogSpec
from app.modules.credential.crypto import CredentialKeyring, Secret
from app.modules.credential.public import CredentialService
from app.modules.identity_tenant.public import IdentityService, PlatformPrincipal, TenantPrincipal
from app.modules.model.public import ModelService
from app.modules.tool.public import (
    AgentInstallScope,
    AgentToolResolutionScope,
    DefinitionSpec,
    MCPTool,
    ToolResolutionScope,
    ToolService,
)
from app.modules.workspace.public import WorkspaceService


class Observations:
    def __init__(self):
        self.items = []

    def emit(self, observation):
        self.items.append(observation)


async def seed(sessions):
    keyring = CredentialKeyring(active_key_version="v1", keys={"v1": os.urandom(32)})
    async with transaction(sessions) as tx:
        identities = IdentityService(tx)
        account = await identities.create_account(platform_role="platform_admin")
        tenant = await identities.create_tenant(name="Tenant")
        member = await identities.create_membership(
            tenant_id=tenant.id, account_id=account.id, display_name="Admin", role="tenant_admin"
        )
        principal = TenantPrincipal(account.id, member.id, tenant.id, "tenant_admin")
        credential = await CredentialService(
            tx, keyring
        ).create(
            principal,
            kind="api_key",
            provider="openai",
            label="Provider",
            secret=Secret("test-only-key"),
            owner_kind="tenant",
        )
        model = await ModelService(tx).create(
            principal,
            credential_id=credential.id,
            provider="openai",
            model_name="test",
            endpoint="https://provider.invalid/v1",
            context_limit=8192,
            output_limit=2048,
            capability_source="administrator",
            capabilities={"supports_tool_calling": True},
            settings_version=1,
            settings={"protocol": "openai_chat"},
            enabled=False,
        )
    accepted = await validate_draft_model(sessions, principal, model, keyring)
    async with transaction(sessions) as tx:
        await ModelService(tx).set_enabled(principal, model_id=model.id, enabled=True, acceptance=accepted)
        agents = AgentService(tx)
        first = await agents.create(principal, name="A", soul="Present", timezone="UTC", model_id=model.id)
        second = await agents.create(principal, name="B", soul="Present", timezone="UTC", model_id=model.id)
    return principal, first, second


def spec(kind="tool", source_key="example"):
    return CatalogSpec(kind, "registry", source_key, "Example", "A capability", "1")


@pytest.mark.asyncio
async def test_registration_is_scoped_deduplicated_and_never_grants(test_database):
    principal, first, second = await seed(test_database.sessions)
    other, _, _ = await seed(test_database.sessions)
    audit = Observations()
    market = CapabilityMarketService(test_database.sessions, audit)
    registered = await market.register(principal, spec=spec())
    repeated = await market.register(principal, spec=spec())
    assert registered.created and not repeated.created
    assert registered.item.id == repeated.item.id
    assert await market.search(other) == ()
    assert len(await market.search(principal)) == 1
    assert len(audit.items) == 1
    async with transaction(test_database.sessions) as tx:
        for agent in (first, second):
            assert (
                await ToolService(tx, enabled_sources=market.enabled_source_ids).resolve(
                    ToolResolutionScope(principal, agent.id, "main")
                )
            ).tools == ()
    with pytest.raises(NotFound):
        await market.materialize(other, item_id=registered.item.id)
    with pytest.raises(AccessDenied):
        await market.register(replace(principal, role="member"), spec=spec())


@pytest.mark.asyncio
async def test_platform_materialization_parallel_converges(test_database):
    principal, _, _ = await seed(test_database.sessions)
    audit = Observations()
    market = CapabilityMarketService(test_database.sessions, audit)
    platform = PlatformPrincipal(principal.account_id, principal.tenant_id, "platform_admin")
    template = await market.register_platform(platform, spec=spec())
    results = await asyncio.gather(*(market.materialize(principal, item_id=template.item.id) for _ in range(8)))
    assert len({result.item.id for result in results}) == 1
    assert sum(result.created for result in results) == 1
    assert all(result.item.origin_platform_item_id == template.item.id for result in results)
    assert all(result.item.tenant_id == principal.tenant_id for result in results)
    async with transaction(test_database.sessions) as tx:
        assert await tx.session.scalar(select(func.count()).select_from(CapabilityCatalogItemRecord)) == 2


@pytest.mark.asyncio
async def test_source_remains_when_agent_activation_fails(test_database):
    principal, _, _ = await seed(test_database.sessions)
    market = CapabilityMarketService(test_database.sessions, Observations())
    platform = PlatformPrincipal(principal.account_id, principal.tenant_id, "platform_admin")
    template = await market.register_platform(platform, spec=spec())
    result = await market.install_tool(
        principal,
        agent_id=uuid4(),
        item_id=template.item.id,
        definition=DefinitionSpec("example", "Example", '{"type":"object"}', "example.v1", "external"),
    )
    assert result.source.created and not result.activated
    assert result.activation_error == "not_found"
    assert (await market.materialize(principal, item_id=result.source.item.id)).item == result.source.item


@pytest.mark.asyncio
async def test_explicit_mcp_install_activates_only_selected_agent(test_database):
    principal, first, second = await seed(test_database.sessions)
    market = CapabilityMarketService(test_database.sessions, Observations())
    item = await market.register(principal, spec=spec("mcp", "https://MCP.example:443/api"))
    assert item.item.spec.source_key == "https://mcp.example/api"
    result = await market.install_mcp(
        principal,
        agent_id=first.id,
        item_id=item.item.id,
        endpoint="https://mcp.example/api",
        auth_required=False,
        discovered=(MCPTool("lookup", "Lookup", '{"type":"object"}'),),
    )
    assert result.activated
    async with transaction(test_database.sessions) as tx:
        tools = ToolService(tx, enabled_sources=market.enabled_source_ids)
        assert len((await tools.resolve(ToolResolutionScope(principal, first.id, "main"))).tools) == 1
        assert (await tools.resolve(ToolResolutionScope(principal, second.id, "main"))).tools == ()


@pytest.mark.asyncio
async def test_disabled_source_cannot_install_and_search_has_no_backfill(test_database):
    principal, first, _ = await seed(test_database.sessions)
    market = CapabilityMarketService(test_database.sessions, Observations())
    item = await market.register(principal, spec=spec())
    await market.set_enabled(principal, item_id=item.item.id, enabled=False)
    assert await market.search(principal) == ()
    with pytest.raises(NotFound):
        await market.install_tool(
            principal,
            agent_id=first.id,
            item_id=item.item.id,
            definition=DefinitionSpec("example", "Example", '{"type":"object"}', "example.v1", "external"),
        )


@pytest.mark.parametrize(
    "key", ["https://u:secret@example.org/api", "https://example.org?token=x", "http://example.org", "x" * 513]
)
def test_source_identity_rejects_credentials_and_oversize(key):
    with pytest.raises(InvalidInput):
        spec(source_key=key)


def test_multibyte_metadata_bounds():
    assert replace(spec(), name="界" * 66)
    with pytest.raises(InvalidInput):
        replace(spec(), name="界" * 67)


@pytest.mark.asyncio
async def test_search_bounds_and_literal_wildcards(test_database):
    principal, _, _ = await seed(test_database.sessions)
    market = CapabilityMarketService(test_database.sessions, Observations())
    await market.register(principal, spec=spec())
    assert await market.search(principal, query="%") == ()
    assert len(await market.search(principal, limit=100)) == 1
    for limit in (0, 101):
        with pytest.raises(InvalidInput):
            await market.search(principal, limit=limit)


@pytest.mark.asyncio
async def test_trusted_agent_can_register_install_only_own_scope(test_database):
    principal, first, second = await seed(test_database.sessions)
    other, foreign, _ = await seed(test_database.sessions)
    market = CapabilityMarketService(test_database.sessions, Observations())
    scope = AgentInstallScope(principal.tenant_id, first.id)
    item = await market.register_for_agent(scope, spec=spec())
    definition = DefinitionSpec("example", "Example", '{"type":"object"}', "example.v1", "external")
    result = await market.install_tool_for_agent(scope, item_id=item.item.id, definition=definition)
    assert result.activated
    assert (await market.install_tool_for_agent(scope, item_id=item.item.id, definition=definition)).activated
    with pytest.raises(NotFound):
        await market.register_for_agent(AgentInstallScope(other.tenant_id, first.id), spec=spec())
    with pytest.raises(NotFound):
        await market.install_tool_for_agent(
            AgentInstallScope(other.tenant_id, foreign.id), item_id=item.item.id, definition=definition
        )
    with pytest.raises(AccessDenied):
        await market.set_enabled(scope, item_id=item.item.id, enabled=False)
    async with transaction(test_database.sessions) as tx:
        assert (
            len(
                (
                    await ToolService(tx, enabled_sources=market.enabled_source_ids).resolve(
                        ToolResolutionScope(principal, first.id, "main")
                    )
                ).tools
            )
            == 1
        )
        assert (
            await ToolService(tx, enabled_sources=market.enabled_source_ids).resolve(
                ToolResolutionScope(principal, second.id, "main")
            )
        ).tools == ()


@pytest.mark.asyncio
async def test_skill_shared_and_private_updates_use_workspace_owner(test_database, tmp_path):
    principal, first, second = await seed(test_database.sessions)
    audit = Observations()
    catalog = CapabilityMarketService(test_database.sessions, audit)
    workspace = WorkspaceService(
        test_database.sessions,
        LocalStorageBackend(str(tmp_path)),
        audit,
        enabled_skill_sources=catalog.enabled_source_ids,
    )
    market = CapabilityMarketService(test_database.sessions, audit, workspace)
    item = await market.register(principal, spec=spec("skill"))
    prepared = await workspace.prepare_skill_package({"SKILL.md": b"Original"})
    initial = await market.install_skill(
        principal, agent_id=first.id, item_id=item.item.id, skill_name="example", prepared=prepared, shared=True
    )
    assert initial.activated and initial.skill_binding
    binding = initial.skill_binding
    await workspace.bind_skill(principal, agent_id=second.id, skill_name="example", package_id=binding.package_id)
    discovery_a = await workspace.discover_skills(tenant_id=principal.tenant_id, agent_id=first.id)
    discovery_b = await workspace.discover_skills(tenant_id=principal.tenant_id, agent_id=second.id)
    shared = await market.install_skill(
        principal,
        agent_id=first.id,
        item_id=item.item.id,
        skill_name="example",
        prepared=await workspace.prepare_skill_package({"SKILL.md": b"Shared update"}),
        shared=True,
        package_id=binding.package_id,
        expected_revision=binding.revision,
    )
    assert shared.activated and shared.skill_binding
    assert (await workspace.load_skill(discovery_b, "example")).members["SKILL.md"] == b"Shared update"
    private = await market.install_skill_for_agent(
        AgentInstallScope(principal.tenant_id, first.id),
        item_id=item.item.id,
        skill_name="example",
        prepared=await workspace.prepare_skill_package({"SKILL.md": b"Private"}),
        shared=False,
        package_id=shared.skill_binding.package_id,
        expected_revision=shared.skill_binding.revision,
    )
    assert private.activated and private.skill_binding and not private.skill_binding.shared
    assert (await workspace.load_skill(discovery_a, "example")).members["SKILL.md"] == b"Private"
    assert (await workspace.load_skill(discovery_b, "example")).members["SKILL.md"] == b"Shared update"
    refreshed = await market.refresh_shared_skill(
        principal,
        item_id=item.item.id,
        prepared=await workspace.prepare_skill_package({"SKILL.md": b"Shared second update"}),
        expected_revision=shared.skill_binding.revision,
    )
    assert refreshed.package_id == shared.skill_binding.package_id
    assert (await workspace.load_skill(discovery_a, "example")).members["SKILL.md"] == b"Private"
    assert (await workspace.load_skill(discovery_b, "example")).members["SKILL.md"] == b"Shared second update"
    denied = await market.install_skill_for_agent(
        AgentInstallScope(principal.tenant_id, second.id),
        item_id=item.item.id,
        skill_name="example",
        prepared=await workspace.prepare_skill_package({"SKILL.md": b"Forbidden"}),
        shared=True,
        package_id=shared.skill_binding.package_id,
        expected_revision=refreshed.revision,
    )
    assert not denied.activated and denied.activation_error == "access_denied"
    assert (await workspace.load_skill(discovery_b, "example")).members["SKILL.md"] == b"Shared second update"


@pytest.mark.asyncio
async def test_failed_skill_source_lookup_cleans_only_preparation(test_database, tmp_path):
    principal, first, _ = await seed(test_database.sessions)
    audit = Observations()
    catalog = CapabilityMarketService(test_database.sessions, audit)
    workspace = WorkspaceService(
        test_database.sessions,
        LocalStorageBackend(str(tmp_path)),
        audit,
        enabled_skill_sources=catalog.enabled_source_ids,
    )
    market = CapabilityMarketService(test_database.sessions, audit, workspace)
    prepared = await workspace.prepare_skill_package({"SKILL.md": b"Prepared"})
    with pytest.raises(NotFound):
        await market.install_skill(
            principal, agent_id=first.id, item_id=uuid4(), skill_name="example", prepared=prepared, shared=False
        )
    assert not (tmp_path / prepared.storage_key).exists()


@pytest.mark.asyncio
async def test_concurrent_shared_skill_installs_reuse_one_package(test_database, tmp_path):
    principal, first, second = await seed(test_database.sessions)
    audit = Observations()
    catalog = CapabilityMarketService(test_database.sessions, audit)
    workspace = WorkspaceService(
        test_database.sessions,
        LocalStorageBackend(str(tmp_path)),
        audit,
        enabled_skill_sources=catalog.enabled_source_ids,
    )
    market = CapabilityMarketService(test_database.sessions, audit, workspace)
    item = await market.register(principal, spec=spec("skill"))
    prepared_a = await workspace.prepare_skill_package({"SKILL.md": b"Shared"})
    prepared_b = await workspace.prepare_skill_package({"SKILL.md": b"Shared"})
    results = await asyncio.gather(
        *(
            market.install_skill_for_agent(
                AgentInstallScope(principal.tenant_id, agent.id),
                item_id=item.item.id,
                skill_name="example",
                prepared=prepared,
                shared=True,
            )
            for agent, prepared in ((first, prepared_a), (second, prepared_b))
        )
    )
    assert all(result.activated and result.skill_binding for result in results)
    assert len({result.skill_binding.package_id for result in results}) == 1
    assert sum((tmp_path / prepared.storage_key).exists() for prepared in (prepared_a, prepared_b)) == 1


@pytest.mark.asyncio
async def test_refresh_is_admin_scoped_conditional_metadata_only(test_database):
    principal, first, _ = await seed(test_database.sessions)
    market = CapabilityMarketService(test_database.sessions, Observations())
    item = await market.register(principal, spec=spec())
    refreshed = await market.refresh_source(
        principal, item_id=item.item.id, spec=replace(spec(), version="2"), expected_revision=1
    )
    assert refreshed.definition_revision == 2 and refreshed.spec.version == "2"
    with pytest.raises(Conflict):
        await market.refresh_source(principal, item_id=item.item.id, spec=spec(), expected_revision=1)
    with pytest.raises(InvalidInput):
        await market.refresh_source(
            principal, item_id=item.item.id, spec=spec(source_key="different"), expected_revision=2
        )
    scope = AgentInstallScope(principal.tenant_id, first.id)
    assert len(await market.search(scope)) == 1
    with pytest.raises(AccessDenied):
        await market.refresh_source(scope, item_id=item.item.id, spec=spec(), expected_revision=2)


@pytest.mark.asyncio
async def test_unknown_manifest_fails_explicitly(test_database):
    principal, _, _ = await seed(test_database.sessions)
    market = CapabilityMarketService(test_database.sessions, Observations())
    item = await market.register(principal, spec=spec())
    async with transaction(test_database.sessions) as tx:
        row = await tx.session.get(CapabilityCatalogItemRecord, item.item.id)
        row.manifest_schema_version = 2
    with pytest.raises(InvalidInput, match="manifest"):
        await market.search(principal)


@pytest.mark.asyncio
async def test_concurrent_first_tool_installs_share_source_but_not_grants(test_database):
    principal, first, second = await seed(test_database.sessions)
    market = CapabilityMarketService(test_database.sessions, Observations())
    template = await market.register_platform(
        PlatformPrincipal(principal.account_id, principal.tenant_id, "platform_admin"), spec=spec()
    )
    definition = DefinitionSpec("example", "Example", '{"type":"object"}', "example.v1", "external")
    results = await asyncio.gather(
        *(
            market.install_tool_for_agent(
                AgentInstallScope(principal.tenant_id, agent.id), item_id=template.item.id, definition=definition
            )
            for agent in (first, second)
        )
    )
    assert all(result.activated for result in results)
    assert results[0].source.item.id == results[1].source.item.id
    assert sum(result.source.created for result in results) == 1
    async with transaction(test_database.sessions) as tx:
        tools = ToolService(tx, enabled_sources=market.enabled_source_ids)
        first_tools = await tools.resolve(ToolResolutionScope(principal, first.id, "main"))
        second_tools = await tools.resolve(ToolResolutionScope(principal, second.id, "main"))
        assert len(first_tools.tools) == len(second_tools.tools) == 1
        assert first_tools.tools[0].definition.id == second_tools.tools[0].definition.id


@pytest.mark.asyncio
async def test_source_resolution_filters_disabled_foreign_platform_and_bounds(test_database):
    principal, _, _ = await seed(test_database.sessions)
    other, _, _ = await seed(test_database.sessions)
    market = CapabilityMarketService(test_database.sessions, Observations())
    enabled = await market.register(principal, spec=spec())
    disabled = await market.register(principal, spec=spec(source_key="disabled"))
    foreign = await market.register(other, spec=spec())
    platform = await market.register_platform(
        PlatformPrincipal(principal.account_id, principal.tenant_id, "platform_admin"), spec=spec()
    )
    await market.set_enabled(principal, item_id=disabled.item.id, enabled=False)
    requested = frozenset((enabled.item.id, disabled.item.id, foreign.item.id, platform.item.id, uuid4()))
    async with transaction(test_database.sessions) as tx:
        assert await market.enabled_source_ids(
            transaction_context=tx, tenant_id=principal.tenant_id, requested_ids=requested
        ) == frozenset((enabled.item.id,))
        assert (
            await market.enabled_source_ids(
                transaction_context=tx, tenant_id=principal.tenant_id, requested_ids=frozenset()
            )
            == frozenset()
        )
        assert (
            await market.enabled_source_ids(
                transaction_context=tx,
                tenant_id=principal.tenant_id,
                requested_ids=frozenset(uuid4() for _ in range(256)),
            )
            == frozenset()
        )
        with pytest.raises(InvalidInput):
            await market.enabled_source_ids(
                transaction_context=tx,
                tenant_id=principal.tenant_id,
                requested_ids=frozenset(uuid4() for _ in range(257)),
            )


@pytest.mark.asyncio
async def test_source_resolution_reuses_saturated_caller_pool(test_database):
    principal, _, _ = await seed(test_database.sessions)
    market = CapabilityMarketService(test_database.sessions, Observations())
    item = await market.register(principal, spec=spec())
    ready = asyncio.Barrier(4)

    async def resolve():
        async with transaction(test_database.sessions) as tx:
            await tx.session.execute(text("SELECT 1"))
            await ready.wait()
            return await market.enabled_source_ids(
                transaction_context=tx, tenant_id=principal.tenant_id, requested_ids=frozenset((item.item.id,))
            )

    results = await asyncio.wait_for(asyncio.gather(*(resolve() for _ in range(4))), timeout=5)
    assert all(result == frozenset((item.item.id,)) for result in results)


@pytest.mark.asyncio
async def test_account_scoped_mcp_discovery_reuses_identity_without_overwriting_definition(test_database):
    principal, first, second = await seed(test_database.sessions)
    market = CapabilityMarketService(test_database.sessions, Observations())
    item = await market.register(principal, spec=spec("mcp", "https://mcp.example/api"))
    async with transaction(test_database.sessions) as tx:
        credentials = CredentialService(tx, CredentialKeyring(active_key_version="v1", keys={"v1": os.urandom(32)}))
        first_credential = await credentials.create(
            principal,
            kind="api_key",
            provider="mcp",
            label="A account",
            secret=Secret("account-a-test-only"),
            owner_kind="agent",
            owner_id=first.id,
        )
        second_credential = await credentials.create(
            principal,
            kind="api_key",
            provider="mcp",
            label="B account",
            secret=Secret("account-b-test-only"),
            owner_kind="agent",
            owner_id=second.id,
        )
    discovery_a = MCPTool("lookup", "A scope", '{"type":"object","properties":{"a":{"type":"string"}}}')
    discovery_b = MCPTool("lookup", "B scope", '{"type":"object","properties":{"b":{"type":"integer"}}}')
    installed_a = await market.install_mcp(
        principal,
        agent_id=first.id,
        item_id=item.item.id,
        endpoint="https://mcp.example/api",
        auth_required=True,
        credential_id=first_credential.id,
        discovered=(discovery_a,),
    )
    async with transaction(test_database.sessions) as tx:
        before = (
            await ToolService(tx, enabled_sources=market.enabled_source_ids).resolve(
                ToolResolutionScope(principal, first.id, "main")
            )
        ).tools[0]
    installed_b = await market.install_mcp(
        principal,
        agent_id=second.id,
        item_id=item.item.id,
        endpoint="https://mcp.example/api",
        auth_required=True,
        credential_id=second_credential.id,
        discovered=(discovery_b,),
    )
    assert installed_a.activated and installed_b.activated
    async with transaction(test_database.sessions) as tx:
        tools = ToolService(tx, enabled_sources=market.enabled_source_ids)
        resolved_a = (await tools.resolve(ToolResolutionScope(principal, first.id, "main"))).tools[0]
        resolved_b = (await tools.resolve(ToolResolutionScope(principal, second.id, "main"))).tools[0]
        assert resolved_a.definition == before.definition
        assert resolved_a.definition.id == resolved_b.definition.id
        assert resolved_a.definition.spec.description == "A scope"
        assert resolved_b.definition.spec.description == "B scope"
        assert resolved_a.definition.spec.input_schema_json == discovery_a.input_schema_json
        assert resolved_b.definition.spec.input_schema_json == discovery_b.input_schema_json
        assert resolved_a.credential.id == first_credential.id
        assert resolved_b.credential.id == second_credential.id
        persisted = await tools.register_definition(principal, definition=resolved_a.definition.spec)
        assert persisted == before.definition


@pytest.mark.asyncio
async def test_administrator_mcp_install_preserves_explicit_sse_transport(test_database):
    principal, first, _ = await seed(test_database.sessions)
    market = CapabilityMarketService(test_database.sessions, Observations())
    item = await market.register(principal, spec=spec("mcp", "https://mcp.example/events"))
    result = await market.install_mcp(
        principal,
        agent_id=first.id,
        item_id=item.item.id,
        endpoint="https://mcp.example/events",
        auth_required=False,
        transport="sse",
        discovered=(MCPTool("lookup", "Lookup", '{"type":"object"}'),),
    )
    assert result.activated
    async with transaction(test_database.sessions) as tx:
        resolved = (
            await ToolService(tx, enabled_sources=market.enabled_source_ids).resolve(
                ToolResolutionScope(principal, first.id, "main")
            )
        ).tools[0]
        assert resolved.transport == "sse"


@pytest.mark.asyncio
@pytest.mark.parametrize("agent_owned", [False, True])
async def test_catalog_disable_affects_new_tool_resolution_not_fixed_run(test_database, agent_owned):
    principal, first, _ = await seed(test_database.sessions)
    market = CapabilityMarketService(test_database.sessions, Observations())
    item = await market.register(principal, spec=spec())
    definition = DefinitionSpec("example", "Example", '{"type":"object"}', "example.v1", "external")
    assert (
        await market.install_tool(principal, agent_id=first.id, item_id=item.item.id, definition=definition)
    ).activated
    scope = (
        AgentToolResolutionScope(principal.tenant_id, first.id, "main")
        if agent_owned
        else ToolResolutionScope(principal, first.id, "main")
    )
    async with transaction(test_database.sessions) as tx:
        fixed = await ToolService(tx, enabled_sources=market.enabled_source_ids).resolve(scope)
    assert len(fixed.tools) == 1
    await market.set_enabled(principal, item_id=item.item.id, enabled=False)
    async with transaction(test_database.sessions) as tx:
        assert (await ToolService(tx, enabled_sources=market.enabled_source_ids).resolve(scope)).tools == ()
    assert len(fixed.tools) == 1 and fixed.tools[0].definition.spec.name == "example"
    with pytest.raises(NotFound):
        await market.install_tool_for_agent(
            AgentInstallScope(principal.tenant_id, first.id), item_id=item.item.id, definition=definition
        )


@pytest.mark.asyncio
async def test_catalog_disable_changes_new_skill_discovery_not_fixed_load(test_database, tmp_path):
    principal, first, _ = await seed(test_database.sessions)
    audit = Observations()
    catalog = CapabilityMarketService(test_database.sessions, audit)
    workspace = WorkspaceService(
        test_database.sessions,
        LocalStorageBackend(str(tmp_path)),
        audit,
        enabled_skill_sources=catalog.enabled_source_ids,
    )
    market = CapabilityMarketService(test_database.sessions, audit, workspace)
    item = await market.register(principal, spec=spec("skill"))
    installed = await market.install_skill(
        principal,
        agent_id=first.id,
        item_id=item.item.id,
        skill_name="code-review",
        prepared=await workspace.prepare_skill_package({"SKILL.md": b"Instructions"}),
        shared=True,
    )
    assert installed.activated
    fixed = await workspace.discover_skills(tenant_id=principal.tenant_id, agent_id=first.id)
    assert fixed.skills == ("code-review",)
    await market.set_enabled(principal, item_id=item.item.id, enabled=False)
    assert (await workspace.discover_skills(tenant_id=principal.tenant_id, agent_id=first.id)).skills == ()
    assert (await workspace.load_skill(fixed, "code-review")).members["SKILL.md"] == b"Instructions"
    missing_resolver = WorkspaceService(test_database.sessions, LocalStorageBackend(str(tmp_path)), audit)
    with pytest.raises(InvalidInput):
        await missing_resolver.discover_skills(tenant_id=principal.tenant_id, agent_id=first.id)
