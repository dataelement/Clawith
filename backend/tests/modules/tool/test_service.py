import os
from dataclasses import replace
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.infrastructure.errors import AccessDenied, Conflict, NotFound
from app.modules.agent.public import AgentService
from app.modules.capability_market.models import CapabilityCatalogItemRecord
from app.modules.credential.public import CredentialKeyring, CredentialService, Secret
from app.modules.identity_tenant.public import IdentityService, TenantPrincipal
from app.modules.model.public import ModelService
from app.modules.permission.public import PermissionService
from app.modules.tool.public import (
    AgentInstallScope,
    AgentToolResolutionScope,
    DefinitionSpec,
    MCPInstallSpec,
    MCPTool,
    ToolResolutionScope,
    ToolService,
)


async def enabled_sources(*, transaction_context, tenant_id, requested_ids):
    rows = await transaction_context.session.scalars(
        select(CapabilityCatalogItemRecord.id).where(
            CapabilityCatalogItemRecord.tenant_id == tenant_id,
            CapabilityCatalogItemRecord.id.in_(requested_ids),
            CapabilityCatalogItemRecord.enabled.is_(True),
        )
    )
    return frozenset(rows)


async def setup(transaction_factory, model_acceptance):
    async with transaction_factory() as tx:
        identities = IdentityService(tx)
        account = await identities.create_account()
        tenant = await identities.create_tenant(name="Tool Tenant")
        member = await identities.create_membership(
            tenant_id=tenant.id, account_id=account.id, display_name="Admin", role="tenant_admin"
        )
        principal = TenantPrincipal(account.id, member.id, tenant.id, "tenant_admin")
        keyring = CredentialKeyring(active_key_version="v1", keys={"v1": os.urandom(32)})
        credentials = CredentialService(tx, keyring)
        credential = await credentials.create(
            principal, kind="api_key", provider="openai", label="Model", secret=Secret("test"), owner_kind="tenant"
        )
        model = await ModelService(tx).create(
            principal,
            credential_id=credential.id,
            provider="openai",
            model_name="model",
            endpoint="https://model.test/v1",
            context_limit=8192,
            output_limit=2048,
            capability_source="administrator",
            capabilities={"supports_tool_calling": True},
            settings_version=1,
            settings={"protocol": "openai_chat"},
            enabled=False,
        )
    accepted = await model_acceptance(principal, model, keyring)
    async with transaction_factory() as tx:
        await ModelService(tx).set_enabled(principal, model_id=model.id, enabled=True, acceptance=accepted)
        agents = AgentService(tx)
        a = await agents.create(principal, name="A", soul="Help", timezone="UTC", model_id=model.id)
        b = await agents.create(principal, name="B", soul="Help", timezone="UTC", model_id=model.id)
        now = datetime.now(UTC)
        catalog = CapabilityCatalogItemRecord(
            id=uuid4(),
            tenant_id=tenant.id,
            created_at=now,
            updated_at=now,
            origin_platform_item_id=None,
            kind="mcp",
            source="http",
            source_key="https://mcp.test",
            name="MCP",
            description="Test",
            version="1",
            manifest_schema_version=1,
            manifest={},
            definition_revision=1,
            enabled=True,
            installed_by_membership_id=member.id,
            installed_by_agent_id=None,
        )
        tx.session.add(catalog)
        await tx.session.flush()
        return principal, a.id, b.id, catalog.id, keyring


async def test_declared_result_format_persists_and_is_captured(transaction_factory, model_acceptance):
    principal, agent, _, _, _ = await setup(transaction_factory, model_acceptance)
    spec = DefinitionSpec("read_attachment", "Preview", '{"type":"object"}', "read_attachment.v1", "builtin",
        result_format="content_blocks")
    async with transaction_factory() as tx:
        service = ToolService(tx, enabled_sources=enabled_sources)
        definition = await service.register_definition(principal, definition=spec)
        await service.grant(principal, agent_id=agent, definition_id=definition.id)
    async with transaction_factory() as tx:
        captured = await ToolService(tx, enabled_sources=enabled_sources).resolve(ToolResolutionScope(principal, agent, "main"))
        assert captured.tools[0].definition.spec == spec
        with pytest.raises(Conflict):
            await ToolService(tx).register_definition(principal, definition=replace(spec, result_format=None))


async def test_member_capture_and_personal_mcp_preserve_membership_identity(transaction_factory, model_acceptance):
    admin, agent, other_agent, catalog, keyring = await setup(transaction_factory, model_acceptance)
    async with transaction_factory() as tx:
        identities, permissions = IdentityService(tx), PermissionService(tx)
        people = []
        await permissions.set_visibility(admin, agent_id=agent, visibility="restricted")
        for name in ("Alice", "Bob"):
            account = await identities.create_account()
            membership = await identities.create_membership(tenant_id=admin.tenant_id, account_id=account.id,
                display_name=name, role="member")
            await permissions.grant_membership(admin, agent_id=agent, membership_id=membership.id)
            people.append(await permissions.freeze_principal(TenantPrincipal(account.id, membership.id, admin.tenant_id, "member")))
        alice, bob = people
        tools = ToolService(tx, enabled_sources=enabled_sources)
        definition = await tools.register_definition(admin, definition=DefinitionSpec("member_mail", "Mail",
            '{"type":"object"}', "mcp.v1", "mcp", catalog, "mail"))
        connection = await tools.connect_mcp(admin, agent_id=agent, catalog_item_id=catalog,
            endpoint="https://mcp.test", auth_required=False, discovered=(MCPTool("mail", "Agent mail", '{"type":"object"}'),))
        await tools.grant(admin, agent_id=agent, definition_id=definition.id, mcp_connection_id=connection.id)
        captured = await tools.capture_authorized(ToolResolutionScope(alice, agent, "main"))
        assert captured.tools[0].definition.id == definition.id
        assert captured.tools[0].credential is None
        with pytest.raises(AccessDenied):
            await tools.capture_authorized(ToolResolutionScope(alice, other_agent, "main"))
        with pytest.raises(NotFound):
            await tools.capture_authorized(ToolResolutionScope(replace(alice, tenant_id=uuid4()), agent, "main"))
        credentials = CredentialService(tx, keyring)
        alice_key = await credentials.create(alice, kind="api_key", provider="mcp", label="Alice mail",
            secret=Secret("alice-secret"), owner_kind="membership")
        personal = await tools.bind_personal_connection(alice, agent_id=agent, definition_id=definition.id,
            credential_id=alice_key.id, label="Alice", endpoint="https://mcp.test",
            discovered=(MCPTool("mail", "Alice mail", '{"type":"object"}'),))
        selected = await tools.capture_authorized(ToolResolutionScope(alice, agent, "main", frozenset({personal}), (personal,)))
        assert selected.tools[0].credential.owner_kind == "membership"
        assert selected.tools[0].credential.owner_id == alice.membership_id
        assert selected.tools[0].credential.id == alice_key.id
        with pytest.raises(AccessDenied):
            await tools.capture_authorized(ToolResolutionScope(bob, agent, "main", frozenset({personal}), (personal,)))
        with pytest.raises(AccessDenied):
            await tools.bind_personal_connection(bob, agent_id=agent, definition_id=definition.id,
                credential_id=alice_key.id, label="Not Bob's", endpoint="https://mcp.test", discovered=())
        assert (await tools.capture_authorized(ToolResolutionScope(alice, agent, "main"))).tools[0].credential is None


async def test_optional_auth_grants_idempotency_and_fixed_discovery(transaction_factory, model_acceptance):
    principal, agent, _, catalog, _ = await setup(transaction_factory, model_acceptance)
    async with transaction_factory() as tx:
        service = ToolService(tx, enabled_sources=enabled_sources)
        spec = DefinitionSpec("catalog_echo", "Echo", '{"type":"object"}', "mcp.v1", "mcp", catalog, "echo")
        definition = await service.register_definition(principal, definition=spec)
        assert (await service.register_definition(principal, definition=spec)).id == definition.id
        connection = await service.connect_mcp(
            principal,
            agent_id=agent,
            catalog_item_id=catalog,
            endpoint="https://mcp.test",
            auth_required=False,
            discovered=(MCPTool("echo", "Shared account", '{"type":"object"}'),),
        )
        grant = await service.grant(
            principal, agent_id=agent, definition_id=definition.id, mcp_connection_id=connection.id
        )
        assert grant == await service.grant(
            principal, agent_id=agent, definition_id=definition.id, mcp_connection_id=connection.id
        )
        view = await service.resolve(
            ToolResolutionScope(principal, agent, "main"), direct_names=frozenset({"catalog_echo"})
        )
        assert len(view.visible()) == 1
        assert view.tools[0].credential is None
        assert view.tools[0].definition.spec.description == "Shared account"
        extra = await service.register_definition(
            principal, definition=DefinitionSpec("extra", "Extra", '{"type":"object"}', "extra.v1", "product")
        )
        await service.grant(principal, agent_id=agent, definition_id=extra.id)
        assert len(view.tools) == 1
        assert len((await service.resolve(ToolResolutionScope(principal, agent, "main"))).tools) == 2


async def test_personal_account_explicit_selection_keeps_agent_default_and_schema(transaction_factory, model_acceptance):
    principal, agent, _, catalog, keyring = await setup(transaction_factory, model_acceptance)
    async with transaction_factory() as tx:
        credentials = CredentialService(tx, keyring)
        service = ToolService(tx, enabled_sources=enabled_sources)
        definition = await service.register_definition(
            principal, definition=DefinitionSpec("mail", "Mail", '{"type":"object"}', "mcp.v1", "mcp", catalog, "mail")
        )
        default = await service.connect_mcp(
            principal,
            agent_id=agent,
            catalog_item_id=catalog,
            endpoint="https://mcp.test",
            auth_required=False,
            discovered=(MCPTool("mail", "Agent", '{"type":"object"}'),),
        )
        await service.grant(principal, agent_id=agent, definition_id=definition.id, mcp_connection_id=default.id)
        credential = await credentials.create(
            principal,
            kind="api_key",
            provider="mcp",
            label="My mail",
            secret=Secret("private"),
            owner_kind="membership",
        )
        personal = await service.bind_personal_connection(
            principal,
            agent_id=agent,
            definition_id=definition.id,
            credential_id=credential.id,
            label="My account",
            endpoint="https://mcp.test",
            discovered=(MCPTool("mail", "Personal", '{"type":"object","required":["to"]}'),),
        )
        with pytest.raises(AccessDenied):
            await service.resolve(
                ToolResolutionScope(principal, agent, "main", selected_personal_connections=(personal,))
            )
        selected = await service.resolve(
            ToolResolutionScope(principal, agent, "main", frozenset({personal}), (personal,))
        )
        assert selected.tools[0].credential.id == credential.id
        assert selected.tools[0].definition.spec.description == "Personal"
        ordinary = await service.resolve(ToolResolutionScope(principal, agent, "main"))
        assert ordinary.tools[0].credential is None
        assert ordinary.tools[0].definition.spec.description == "Agent"
        agent_default = await service.resolve(AgentToolResolutionScope(principal.tenant_id, agent, "main"))
        assert agent_default.tools[0].credential is None
        delegated = await service.resolve(
            AgentToolResolutionScope(principal.tenant_id, agent, "main", frozenset({personal}), (personal,))
        )
        assert delegated.tools[0].credential.id == credential.id
        with pytest.raises(AccessDenied):
            await service.resolve(
                AgentToolResolutionScope(principal.tenant_id, agent, "main", selected_personal_connections=(personal,))
            )


async def test_wrong_agent_credentials_and_connection_rejected_at_mutation(transaction_factory, model_acceptance):
    principal, agent, other, catalog, keyring = await setup(transaction_factory, model_acceptance)
    async with transaction_factory() as tx:
        credentials = CredentialService(tx, keyring)
        service = ToolService(tx, enabled_sources=enabled_sources)
        credential = await credentials.create(
            principal,
            kind="api_key",
            provider="mcp",
            label="Other",
            secret=Secret("private"),
            owner_kind="agent",
            owner_id=other,
        )
        with pytest.raises(AccessDenied):
            await service.connect_mcp(
                principal,
                agent_id=agent,
                catalog_item_id=catalog,
                endpoint="https://mcp.test",
                auth_required=True,
                credential_id=credential.id,
            )
        definition = await service.register_definition(
            principal, definition=DefinitionSpec("mail", "Mail", '{"type":"object"}', "mcp.v1", "mcp", catalog, "mail")
        )
        connection = await service.connect_mcp(
            principal, agent_id=other, catalog_item_id=catalog, endpoint="https://mcp.test", auth_required=False
        )
        with pytest.raises(AccessDenied):
            await service.grant(principal, agent_id=agent, definition_id=definition.id, mcp_connection_id=connection.id)
        with pytest.raises(Conflict):
            await service.register_definition(
                principal,
                definition=DefinitionSpec(
                    "mail", "Changed", '{"type":"object"}', "mcp.v1", "mcp", catalog, "different_upstream"
                ),
            )


async def test_required_auth_missing_is_unavailable_not_synthetic_token(transaction_factory, model_acceptance):
    principal, agent, _, catalog, _ = await setup(transaction_factory, model_acceptance)
    async with transaction_factory() as tx:
        service = ToolService(tx, enabled_sources=enabled_sources)
        definition = await service.register_definition(
            principal, definition=DefinitionSpec("mail", "Mail", '{"type":"object"}', "mcp.v1", "mcp", catalog, "mail")
        )
        connection = await service.connect_mcp(
            principal,
            agent_id=agent,
            catalog_item_id=catalog,
            endpoint="https://mcp.test",
            auth_required=True,
            discovered=(MCPTool("mail", "Mail", '{"type":"object"}'),),
        )
        await service.grant(principal, agent_id=agent, definition_id=definition.id, mcp_connection_id=connection.id)
        assert not (await service.resolve(ToolResolutionScope(principal, agent, "main"))).tools


async def test_catalog_resolution_required_and_disabling_only_changes_next_snapshot(transaction_factory, model_acceptance):
    from app.infrastructure.errors import InvalidInput

    principal, agent, _, catalog, _ = await setup(transaction_factory, model_acceptance)
    async with transaction_factory() as tx:
        service = ToolService(tx, enabled_sources=enabled_sources)
        definition = await service.register_definition(
            principal,
            definition=DefinitionSpec("tool", "Tool", '{"type":"object"}', "external.v1", "external", catalog),
        )
        await service.grant(principal, agent_id=agent, definition_id=definition.id)
        with pytest.raises(InvalidInput, match="resolver"):
            await ToolService(tx).resolve(ToolResolutionScope(principal, agent, "main"))
        old = await service.resolve(AgentToolResolutionScope(principal.tenant_id, agent, "main"))
        source = await tx.session.get(CapabilityCatalogItemRecord, catalog)
        source.enabled = False
        await tx.session.flush()
        assert old.tools
        assert not (await service.resolve(AgentToolResolutionScope(principal.tenant_id, agent, "main"))).tools
        assert not (await service.resolve(ToolResolutionScope(principal, agent, "main"))).tools


async def test_agent_self_install_has_no_fake_membership_and_cannot_bind_another_agents_secret(transaction_factory, model_acceptance):
    principal, agent, other, catalog, keyring = await setup(transaction_factory, model_acceptance)
    async with transaction_factory() as tx:
        credentials = CredentialService(tx, keyring)
        service = ToolService(tx, enabled_sources=enabled_sources)
        spec = DefinitionSpec("mail", "Mail", '{"type":"object"}', "mcp.v1", "mcp", catalog, "mail")
        other_credential = await credentials.create(
            principal,
            kind="api_key",
            provider="mcp",
            label="Other",
            secret=Secret("not-for-agent"),
            owner_kind="agent",
            owner_id=other,
        )
        with pytest.raises(NotFound):
            await service.install_for_agent(
                AgentInstallScope(principal.tenant_id, agent),
                definition=spec,
                connection=MCPInstallSpec("https://mcp.test", True, other_credential.id),
            )
        installed = await service.install_for_agent(
            AgentInstallScope(principal.tenant_id, agent),
            definition=spec,
            connection=MCPInstallSpec(
                "https://mcp.test", False, discovered=(MCPTool("mail", "Mail", '{"type":"object"}'),)
            ),
        )
        assert (await service.resolve(ToolResolutionScope(principal, agent, "main"))).tools[
            0
        ].definition.id == installed.id
        assert not (await service.resolve(ToolResolutionScope(principal, other, "main"))).tools
        with pytest.raises(AccessDenied):
            await service.install_for_agent(
                AgentInstallScope(principal.tenant_id, agent),
                definition=DefinitionSpec("builtin", "Builtin", '{"type":"object"}', "builtin.v1", "builtin"),
            )


async def test_refresh_and_revocation_do_not_mutate_captured_tools(transaction_factory, model_acceptance):
    principal, agent, _, catalog, _ = await setup(transaction_factory, model_acceptance)
    async with transaction_factory() as tx:
        service = ToolService(tx, enabled_sources=enabled_sources)
        definition = await service.register_definition(
            principal, definition=DefinitionSpec("mail", "Mail", '{"type":"object"}', "mcp.v1", "mcp", catalog, "mail")
        )
        connection = await service.connect_mcp(
            principal,
            agent_id=agent,
            catalog_item_id=catalog,
            endpoint="https://mcp.test",
            auth_required=False,
            discovered=(MCPTool("mail", "First", '{"type":"object"}'),),
        )
        await service.grant(principal, agent_id=agent, definition_id=definition.id, mcp_connection_id=connection.id)
        old = await service.resolve(ToolResolutionScope(principal, agent, "main"))
        await service.refresh_discovery(
            principal,
            connection_id=connection.id,
            expected_credential_id=None,
            discovered=(MCPTool("mail", "Next", '{"type":"object"}'),),
        )
        assert old.tools[0].definition.spec.description == "First"
        assert (await service.resolve(ToolResolutionScope(principal, agent, "main"))).tools[
            0
        ].definition.spec.description == "Next"
        await service.revoke_grant(principal, agent_id=agent, definition_id=definition.id)
        assert old.tools
        assert not (await service.resolve(ToolResolutionScope(principal, agent, "main"))).tools


async def test_owner_metadata_never_reveals_secret_and_rejects_other_owner_or_tenant(transaction_factory, model_acceptance):
    principal, agent, other, _, keyring = await setup(transaction_factory, model_acceptance)
    async with transaction_factory() as tx:
        credentials = CredentialService(tx, keyring)
        credential = await credentials.create(
            principal,
            kind="api_key",
            provider="mcp",
            label="Agent",
            secret=Secret("not-visible"),
            owner_kind="agent",
            owner_id=agent,
        )
        metadata = await credentials.require_owner_metadata(
            tenant_id=principal.tenant_id, credential_id=credential.id, owner_kind="agent", owner_id=agent
        )
        assert "not-visible" not in repr(metadata)
        for tenant_id, owner_id in ((uuid4(), agent), (principal.tenant_id, other)):
            with pytest.raises(NotFound):
                await credentials.require_owner_metadata(
                    tenant_id=tenant_id, credential_id=credential.id, owner_kind="agent", owner_id=owner_id
                )
        await credentials.revoke(principal, credential_id=credential.id)
        with pytest.raises(NotFound):
            await credentials.require_owner_metadata(
                tenant_id=principal.tenant_id, credential_id=credential.id, owner_kind="agent", owner_id=agent
            )
