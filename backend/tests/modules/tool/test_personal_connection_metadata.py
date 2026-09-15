from uuid import uuid4

import pytest
from modules.tool.test_service import enabled_sources, setup

from app.infrastructure.errors import AccessDenied, InvalidInput
from app.modules.credential.public import CredentialService, Secret
from app.modules.tool.models import MembershipAgentToolConnectionRecord
from app.modules.tool.public import AgentToolResolutionScope, DefinitionSpec, MCPTool, ToolService


async def test_disabled_personal_owner_metadata_does_not_authorize_execution(transaction_factory, model_acceptance):
    principal, agent, _, catalog, keyring = await setup(transaction_factory, model_acceptance)
    async with transaction_factory() as tx:
        tools = ToolService(tx, enabled_sources=enabled_sources)
        definition = await tools.register_definition(principal, definition=DefinitionSpec(
            "mail", "Mailbox", '{"type":"object"}', "mcp.v1", "mcp", catalog, "mail"))
        discovered = (MCPTool("mail", "Mailbox", '{"type":"object"}'),)
        shared = await tools.connect_mcp(principal, agent_id=agent, catalog_item_id=catalog,
            endpoint="https://mcp.test", auth_required=False, discovered=discovered)
        await tools.grant(principal, agent_id=agent, definition_id=definition.id, mcp_connection_id=shared.id)
        credential = await CredentialService(tx, keyring).create(principal, kind="api_key", provider="mcp",
            label="Private mailbox", secret=Secret("must-not-be-returned"), owner_kind="membership")
        personal = await tools.bind_personal_connection(principal, agent_id=agent, definition_id=definition.id,
            credential_id=credential.id, label="Owner", endpoint="https://mcp.test", discovered=discovered)
        scope = AgentToolResolutionScope(principal.tenant_id, agent, "main", frozenset({personal}), (personal,))
        assert (await tools.capture_authorized(scope)).tools
        record = await tx.session.get(MembershipAgentToolConnectionRecord, personal)
        record.enabled = False
        await tx.session.flush()
        owners = await tools.personal_connection_owners(tenant_id=principal.tenant_id, connection_ids=(personal,))
        assert owners == {personal: principal.membership_id}
        assert "must-not-be-returned" not in repr(owners)
        assert await tools.personal_connection_owners(tenant_id=uuid4(), connection_ids=(personal,)) == {}
        assert await tools.personal_connection_owners(tenant_id=principal.tenant_id, connection_ids=(uuid4(),)) == {}
        assert await tools.personal_connection_owners(tenant_id=principal.tenant_id, connection_ids=()) == {}
        assert await tools.personal_connection_owners(tenant_id=principal.tenant_id, connection_ids=tuple(uuid4() for _ in range(128))) == {}
        with pytest.raises(InvalidInput):
            await tools.personal_connection_owners(tenant_id=principal.tenant_id, connection_ids=tuple(uuid4() for _ in range(129)))
        with pytest.raises(AccessDenied, match="Personal connection is unavailable"):
            await tools.capture_authorized(scope)
