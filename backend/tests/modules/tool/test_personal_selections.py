from uuid import uuid4

import pytest
from modules.tool.test_service import enabled_sources, setup
from sqlalchemy import update

from app.infrastructure.errors import AccessDenied, InvalidInput, NotFound
from app.modules.capability_market.models import CapabilityCatalogItemRecord
from app.modules.credential.public import CredentialService, Secret
from app.modules.identity_tenant.public import IdentityService, TenantPrincipal
from app.modules.tool.models import ToolDefinitionRecord
from app.modules.tool.public import (
    DefinitionSpec,
    MCPTool,
    PersonalAccountSelection,
    ToolResolutionScope,
    ToolService,
    decode_personal_selections,
    encode_personal_selections,
)


def test_personal_selection_codec_preserves_exact_targets_and_connections():
    selected = (PersonalAccountSelection(uuid4(), (uuid4(), uuid4())), PersonalAccountSelection(uuid4(), (uuid4(),)))
    assert decode_personal_selections(encode_personal_selections(selected)) == selected
    assert decode_personal_selections(encode_personal_selections(())) == ()


@pytest.mark.parametrize("value", [None, {}, {"version": 2, "targets": []}, {"version": True, "targets": []},
    {"version": 1, "targets": [], "extra": 1}, {"version": 1, "targets": [{"target_agent_id": "bad", "connection_ids": []}]},
    {"version": 1, "targets": [{"target_agent_id": str(uuid4()), "connection_ids": [None]}]}])
def test_personal_selection_codec_rejects_unknown_versions_or_invalid_shapes(value):
    with pytest.raises(InvalidInput):
        decode_personal_selections(value)


def test_personal_selection_codec_rejects_duplicate_and_unbounded_choices():
    target, connection = uuid4(), uuid4()
    for selections in ((PersonalAccountSelection(target, (connection, connection)),),
            (PersonalAccountSelection(target, (connection,)), PersonalAccountSelection(target, ())),
            (PersonalAccountSelection(target, tuple(uuid4() for _ in range(129))),)):
        with pytest.raises(InvalidInput):
            encode_personal_selections(selections)


async def personal_account(transaction_factory, model_acceptance):
    principal, agent, _, catalog, keyring = await setup(transaction_factory, model_acceptance)
    async with transaction_factory() as tx:
        tools = ToolService(tx, enabled_sources=enabled_sources)
        definition = await tools.register_definition(principal, definition=DefinitionSpec(
            "mail", "Mail", '{"type":"object"}', "mcp.v1", "mcp", catalog, "mail"))
        default = await tools.connect_mcp(principal, agent_id=agent, catalog_item_id=catalog,
            endpoint="https://mcp.test", auth_required=False,
            discovered=(MCPTool("mail", "Agent mail", '{"type":"object"}'),))
        await tools.grant(principal, agent_id=agent, definition_id=definition.id, mcp_connection_id=default.id)
        credential = await CredentialService(tx, keyring).create(principal, kind="api_key", provider="mcp",
            label="Personal mail", secret=Secret("personal-account-secret"), owner_kind="membership")
        connection_id = await tools.bind_personal_connection(principal, agent_id=agent, definition_id=definition.id,
            credential_id=credential.id, label="Personal", endpoint="https://personal.test",
            discovered=(MCPTool("mail", "Personal mail", '{"type":"object"}'),))
    return principal, agent, catalog, definition.id, credential.id, connection_id


async def test_validate_personal_selection_preserves_exact_account_and_rejects_another_member(
        transaction_factory, model_acceptance):
    principal, agent, _, _, credential_id, connection_id = await personal_account(transaction_factory, model_acceptance)
    selection = (PersonalAccountSelection(agent, (connection_id,)),)
    async with transaction_factory() as tx:
        service = ToolService(tx, enabled_sources=enabled_sources)
        assert await service.validate_personal_selections(principal, selections=selection) == selection
        captured = await service.capture_authorized(ToolResolutionScope(principal, agent, "main",
            frozenset({connection_id}), (connection_id,)))
        assert captured.tools[0].credential.id == credential_id
        assert captured.tools[0].credential.owner_id == principal.membership_id
        identities = IdentityService(tx)
        account = await identities.create_account()
        member = await identities.create_membership(tenant_id=principal.tenant_id, account_id=account.id,
            display_name="Other admin", role="tenant_admin")
        other = TenantPrincipal(account.id, member.id, principal.tenant_id, "tenant_admin")
        with pytest.raises(AccessDenied, match="Personal connection is unavailable"):
            await service.validate_personal_selections(other, selections=selection)
        ordinary = await service.capture_authorized(ToolResolutionScope(principal, agent, "main"))
        assert ordinary.tools[0].credential is None
        assert ordinary.tools[0].definition.spec.description == "Agent mail"


@pytest.mark.parametrize("disabled", ["source", "definition"])
async def test_selected_personal_account_cannot_disappear_during_later_capture(
        transaction_factory, model_acceptance, disabled):
    principal, agent, catalog_id, definition_id, _, connection_id = await personal_account(transaction_factory, model_acceptance)
    selection = (PersonalAccountSelection(agent, (connection_id,)),)
    scope = ToolResolutionScope(principal, agent, "main", frozenset({connection_id}), (connection_id,))
    async with transaction_factory() as tx:
        service = ToolService(tx, enabled_sources=enabled_sources)
        assert await service.validate_personal_selections(principal, selections=selection) == selection
        captured = await service.capture_authorized(scope)
        assert len(captured.tools) == 1
    async with transaction_factory() as tx:
        model, record_id = ((CapabilityCatalogItemRecord, catalog_id) if disabled == "source"
            else (ToolDefinitionRecord, definition_id))
        await tx.session.execute(update(model).where(model.tenant_id == principal.tenant_id, model.id == record_id).values(enabled=False))
    async with transaction_factory() as tx:
        service = ToolService(tx, enabled_sources=enabled_sources)
        with pytest.raises(NotFound, match="explicitly selected personal account Tool"):
            await service.capture_authorized(scope)
        with pytest.raises(NotFound, match="explicitly selected personal account Tool"):
            await service.validate_personal_selections(principal, selections=selection)
        assert (await service.capture_authorized(ToolResolutionScope(principal, agent, "main"))).tools == ()
    assert len(captured.tools) == 1
    assert captured.tools[0].definition.spec.description == "Personal mail"
