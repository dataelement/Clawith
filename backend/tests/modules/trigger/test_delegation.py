from datetime import UTC, datetime, timedelta

import pytest
from modules.tool.test_service import enabled_sources, setup
from sqlalchemy import select

from app.infrastructure.errors import AccessDenied
from app.modules.credential.public import CredentialService, Secret
from app.modules.group.public import GroupService
from app.modules.heartbeat.models import HeartbeatOccurrenceRecord
from app.modules.heartbeat.public import HeartbeatConfig, HeartbeatService
from app.modules.identity_tenant.public import IdentityService, TenantPrincipal
from app.modules.run.public import InputContent
from app.modules.tool.models import MembershipAgentToolConnectionRecord
from app.modules.tool.public import AgentToolResolutionScope, DefinitionSpec, MCPTool, ToolService
from app.modules.trigger.models import TriggerOccurrenceRecord
from app.modules.trigger.public import TriggerConfig, TriggerService
from app.modules.workspace.public import WorkspaceSubject


@pytest.mark.parametrize("owner", ["trigger", "heartbeat"])
async def test_explicit_personal_account_survives_config_and_native_recapture(transaction_factory, model_acceptance, owner):
    principal, agent, _, catalog, keyring = await setup(transaction_factory, model_acceptance)
    async with transaction_factory() as tx:
        tools = ToolService(tx, enabled_sources=enabled_sources)
        definition = await tools.register_definition(principal, definition=DefinitionSpec(
            "mail", "Read mailbox", '{"type":"object"}', "mcp.v1", "mcp", catalog, "mail"))
        discovered = (MCPTool("mail", "Mailbox", '{"type":"object"}'),)
        connection = await tools.connect_mcp(principal, agent_id=agent, catalog_item_id=catalog,
            endpoint="https://mcp.test", auth_required=False, discovered=discovered)
        await tools.grant(principal, agent_id=agent, definition_id=definition.id, mcp_connection_id=connection.id)
        credential = await CredentialService(tx, keyring).create(principal, kind="api_key", provider="mcp",
            label="Personal", secret=Secret("test-only-secret"), owner_kind="membership")
        personal = await tools.bind_personal_connection(principal, agent_id=agent, definition_id=definition.id,
            credential_id=credential.id, label="My mailbox", endpoint="https://mcp.test", discovered=discovered)
        scope = AgentToolResolutionScope(principal.tenant_id, agent, "main", frozenset({personal}), (personal,))
        if owner == "trigger":
            service = TriggerService(tx, enabled_sources=enabled_sources)
            human = await service.create(principal, agent_id=agent, config=TriggerConfig("Inbox", "webhook", "Check mailbox"),
                delegated_connection_ids=(personal,))
            native = await service.create_for_agent(scope, config=human.config)
            occurrence = await service.accept(tenant_id=principal.tenant_id, trigger_id=native.id,
                source_key="notification", now=datetime.now(UTC), event_kind="webhook")
            assert occurrence.delegated_connection_ids == (personal,)
        else:
            service = HeartbeatService(tx, enabled_sources=enabled_sources)
            human = await service.configure(principal, agent_id=agent, config=HeartbeatConfig("Check mailbox", 5),
                delegated_connection_ids=(personal,))
            native = await service.configure_for_agent(scope, config=human.config)
            now = datetime.now(UTC) + timedelta(minutes=6)
            due = (await service.due(now=now, not_before=now - timedelta(minutes=7))).items[0]
            occurrence = await service.accept(tenant_id=principal.tenant_id, heartbeat_id=native.id,
                source_key=due.source_key, due_at=due.due_at, now=now, not_before=now - timedelta(minutes=7))
        assert human.delegated_connection_ids == native.delegated_connection_ids == (personal,)
        assert occurrence.origin_kind == "membership" and occurrence.origin_id == principal.membership_id
        record_type = TriggerOccurrenceRecord if owner == "trigger" else HeartbeatOccurrenceRecord
        stored = await tx.session.scalar(select(record_type).where(record_type.id == occurrence.id))
        # An old accepted occurrence remains private and readable after its connection is disabled.
        stored.payload_version = 1
        retained = {"text", "delegated_connections"} if owner == "trigger" else {"instruction", "delegated_connections"}
        stored.payload = {key: value for key, value in stored.payload.items() if key in retained}
        connection_record = await tx.session.get(MembershipAgentToolConnectionRecord, personal)
        connection_record.enabled = False
        await tx.session.flush()
        identity = IdentityService(tx)
        account = await identity.create_account()
        member = await identity.create_membership(tenant_id=principal.tenant_id, account_id=account.id,
            display_name="Other", role="tenant_admin")
        other = TenantPrincipal(account.id, member.id, principal.tenant_id, "tenant_admin")
        options = {"trigger_id": native.id} if owner == "trigger" else {"agent_id": agent}
        own_page = await service.history(principal, **options)
        assert own_page.items[0].origin_id == principal.membership_id
        assert (await service.history(other, **options)).items == ()
        if owner == "trigger":
            connection_record.enabled = True
            await tx.session.flush()
            watch = await service.create(principal, agent_id=agent,
                config=TriggerConfig("Private inbox", "on_message", "Inspect private mail"), delegated_connection_ids=(personal,))
            with pytest.raises(AccessDenied):
                await service.accept(tenant_id=principal.tenant_id, trigger_id=watch.id, source_key="other-human",
                    now=datetime.now(UTC), event_kind="on_message", source_membership_id=other.membership_id,
                    origin=WorkspaceSubject("membership", other.membership_id), input=InputContent("Other private input"))
            with pytest.raises(AccessDenied):
                await service.accept(tenant_id=principal.tenant_id, trigger_id=watch.id, source_key="agent-event",
                    now=datetime.now(UTC), event_kind="on_message", source_agent_id=agent,
                    origin=WorkspaceSubject("membership", principal.membership_id), input=InputContent("Agent input without account delegation proof"))
            assert (await service.history(principal, trigger_id=watch.id)).items == ()
            accepted = await service.accept(tenant_id=principal.tenant_id, trigger_id=watch.id, source_key="own-human",
                now=datetime.now(UTC), event_kind="on_message", source_membership_id=principal.membership_id,
                origin=WorkspaceSubject("membership", principal.membership_id), input=InputContent("Owner input"))
            assert accepted.origin_id == principal.membership_id
            groups = GroupService(tx)
            group = await groups.create(principal, name="Authorized account owner group")
            await groups.set_agent(principal, group_id=group.id, agent_id=agent, enabled=True)
            topic = await groups.resolve_conversation(principal, group_id=group.id)
            grouped = await service.accept(tenant_id=principal.tenant_id, trigger_id=watch.id, source_key="own-group",
                now=datetime.now(UTC), event_kind="on_message", source_membership_id=principal.membership_id,
                origin=WorkspaceSubject("group", group.id), origin_conversation_id=topic, input=InputContent("Owner group task"))
            assert grouped.origin_kind == "group" and grouped.origin_id == group.id
            with pytest.raises(AccessDenied):
                await service.accept(tenant_id=principal.tenant_id, trigger_id=watch.id, source_key="other-group",
                    now=datetime.now(UTC), event_kind="on_message", source_membership_id=other.membership_id,
                    origin=WorkspaceSubject("group", group.id), origin_conversation_id=topic, input=InputContent("Other member cannot use account"))
