from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from modules.session.test_session import accept, setup
from modules.session.test_session import start as started
from sqlalchemy import event, select

from app.infrastructure.errors import AccessDenied, InvalidInput
from app.modules.agent.public import AgentService
from app.modules.group.public import GroupService
from app.modules.heartbeat.models import AgentHeartbeatRecord
from app.modules.heartbeat.public import HeartbeatConfig, HeartbeatService
from app.modules.identity_tenant.public import IdentityService, TenantPrincipal
from app.modules.run.public import InputContent
from app.modules.session.public import SessionService
from app.modules.tool.public import AgentToolResolutionScope
from app.modules.trigger.models import AgentTriggerRecord, TriggerOccurrenceRecord
from app.modules.trigger.public import TriggerConfig, TriggerService
from app.modules.workspace.public import WorkspaceSubject

NOW = datetime(2026, 9, 9, tzinfo=UTC)


@pytest.mark.parametrize("owner", ["trigger", "heartbeat"])
async def test_explicit_session_destination_is_frozen_per_occurrence_and_old_versions_still_read(transaction_factory, owner):
    p, destination = await setup(transaction_factory)
    async with transaction_factory() as tx:
        other = await SessionService(tx).create(p, agent_id=destination.agent_id)
        if owner == "trigger":
            service = TriggerService(tx)
            config = TriggerConfig("scheduled", "interval", "work", interval_minutes=1,
                destination_kind="session", destination_id=destination.id)
            created = await service.create(p, agent_id=destination.agent_id, config=config, now=NOW)
            row = await tx.session.scalar(select(AgentTriggerRecord).where(AgentTriggerRecord.id == created.id))
            assert row.configuration_version == 3
            first = await service.accept(tenant_id=p.tenant_id, trigger_id=created.id, source_key="first", now=NOW + timedelta(minutes=1), event_kind="manual")
            await service.update(p, trigger_id=created.id, config=replace(config, destination_id=other.id), enabled=True)
            reread = await service.get_occurrence(tenant_id=p.tenant_id, occurrence_id=first.id)
            await service.update(p, trigger_id=created.id, config=replace(config, destination_kind=None, destination_id=None), enabled=True)
            assert (await service.get(p, trigger_id=created.id)).config.destination_id is None
            assert row.configuration_version == 1
        else:
            service = HeartbeatService(tx)
            config = HeartbeatConfig("work", 1, destination_kind="session", destination_id=destination.id)
            created = await service.configure(p, agent_id=destination.agent_id, config=config, now=NOW)
            row = await tx.session.scalar(select(AgentHeartbeatRecord).where(AgentHeartbeatRecord.id == created.id))
            assert row.configuration_version == 2
            due = NOW + timedelta(minutes=1)
            first = await service.accept(tenant_id=p.tenant_id, heartbeat_id=created.id, source_key=due.isoformat(), due_at=due, now=due, not_before=NOW)
            await service.configure(p, agent_id=destination.agent_id, config=replace(config, destination_id=other.id), now=due)
            reread = await service.get_occurrence(tenant_id=p.tenant_id, occurrence_id=first.id)
            await service.configure(p, agent_id=destination.agent_id, config=replace(config, destination_kind=None, destination_id=None), now=due)
            assert (await service.get(p, agent_id=destination.agent_id)).config.destination_id is None
            assert row.configuration_version == 1
        assert first.destination_id == reread.destination_id == destination.id
        assert first.destination_kind == "session" and first.destination_conversation_id is None


@pytest.mark.parametrize("owner", ["trigger", "heartbeat"])
async def test_native_destination_requires_actual_origin_and_cannot_select_another_session(transaction_factory, owner):
    p, session = await setup(transaction_factory)
    receipt = await accept(transaction_factory, p, session)
    run = await started(transaction_factory, p, session, receipt)
    scope = AgentToolResolutionScope(p.tenant_id, session.agent_id, "main")
    async with transaction_factory() as tx:
        other = await SessionService(tx).create(p, agent_id=session.agent_id)
        if owner == "trigger":
            call = TriggerService(tx).create_for_agent
            config = TriggerConfig("scheduled", "interval", "work", interval_minutes=1,
                destination_kind="session", destination_id=session.id)
        else:
            call = HeartbeatService(tx).configure_for_agent
            config = HeartbeatConfig("work", 1, destination_kind="session", destination_id=session.id)
        with pytest.raises(AccessDenied):
            await call(scope, config=config)
        with pytest.raises(AccessDenied):
            await call(scope, config=replace(config, destination_id=other.id), origin_run=run)
        result = await call(scope, config=config, origin_run=run)
        assert result.config.destination_id == session.id


@pytest.mark.parametrize("config_type,args", [(TriggerConfig, ("name", "interval", "work")), (HeartbeatConfig, ("work", 1))])
@pytest.mark.parametrize("fields", [{"destination_kind": "session"}, {"destination_id": uuid4()},
    {"destination_kind": "session", "destination_id": uuid4(), "destination_conversation_id": uuid4()},
    {"destination_kind": "invalid", "destination_id": uuid4()}])
def test_destination_structure_rejects_partial_or_unknown_targets(config_type, args, fields):
    with pytest.raises(InvalidInput):
        config_type(*args, **fields)


async def test_private_message_history_does_not_load_another_members_payload_and_cursor_advances(test_database, transaction_factory):
    p, session = await setup(transaction_factory)
    async with transaction_factory() as tx:
        identities = IdentityService(tx)
        account = await identities.create_account()
        membership = await identities.create_membership(tenant_id=p.tenant_id, account_id=account.id, display_name="Other", role="tenant_admin")
        other = TenantPrincipal(account.id, membership.id, p.tenant_id, "tenant_admin")
        owner = TriggerService(tx)
        target = await owner.create(p, agent_id=session.agent_id, config=TriggerConfig("watch", "on_message", "Private work"))
        accepted = await owner.accept(tenant_id=p.tenant_id, trigger_id=target.id, source_key="private", now=NOW,
            event_kind="on_message", source_membership_id=p.membership_id,
            origin=WorkspaceSubject("membership", p.membership_id), input=InputContent("private source body"))
        await owner.fail_admission(tenant_id=p.tenant_id, occurrence_id=accepted.id, reason="model_unavailable")
    statements = []
    def captured_sql(*args):
        statements.append(args[2])
    event.listen(test_database.engine.sync_engine, "before_cursor_execute", captured_sql)
    try:
        async with transaction_factory() as tx:
            hidden = await TriggerService(tx).history(other, trigger_id=target.id, limit=1)
        assert hidden.items == () and hidden.next_after_id == accepted.id and not hidden.has_more
        assert not any(f"{TriggerOccurrenceRecord.__tablename__}.payload," in sql for sql in statements)
    finally:
        event.remove(test_database.engine.sync_engine, "before_cursor_execute", captured_sql)
    async with transaction_factory() as tx:
        own = await TriggerService(tx).history(p, trigger_id=target.id)
        assert own.items[0].input.text.endswith("private source body")
        assert own.items[0].origin_id == p.membership_id


async def test_group_origin_history_requires_membership_and_human_target_freezes_topic(transaction_factory):
    p, session = await setup(transaction_factory)
    async with transaction_factory() as tx:
        groups = GroupService(tx)
        group = await groups.create(p, name="Private group")
        await groups.set_agent(p, group_id=group.id, agent_id=session.agent_id, enabled=True)
        default = await groups.resolve_conversation(p, group_id=group.id)
        second = await groups.create_conversation(p, group_id=group.id, title="Other topic")
        owner = TriggerService(tx)
        configured = await owner.create(p, agent_id=session.agent_id, config=TriggerConfig("group", "on_message", "Group work",
            destination_kind="group", destination_id=group.id))
        assert configured.config.destination_conversation_id == default
        occurrence = await owner.accept(tenant_id=p.tenant_id, trigger_id=configured.id, source_key="group", now=NOW,
            event_kind="on_message", source_membership_id=p.membership_id, origin=WorkspaceSubject("group", group.id),
            origin_conversation_id=default, input=InputContent("Group-only content"))
        await owner.update(p, trigger_id=configured.id, config=replace(configured.config, destination_conversation_id=second.id), enabled=True)
        assert (await owner.get_occurrence(tenant_id=p.tenant_id, occurrence_id=occurrence.id)).destination_conversation_id == default
        assert (await owner.history(p, trigger_id=configured.id)).items
        identities = IdentityService(tx)
        account = await identities.create_account()
        membership = await identities.create_membership(tenant_id=p.tenant_id, account_id=account.id, display_name="Nonmember", role="tenant_admin")
        outsider = TenantPrincipal(account.id, membership.id, p.tenant_id, "tenant_admin")
        assert not (await owner.history(outsider, trigger_id=configured.id)).items


@pytest.mark.parametrize("owner", ["trigger", "heartbeat"])
async def test_human_cannot_configure_another_users_session_destination(transaction_factory, owner):
    p, session = await setup(transaction_factory)
    async with transaction_factory() as tx:
        identities = IdentityService(tx)
        account = await identities.create_account()
        membership = await identities.create_membership(tenant_id=p.tenant_id, account_id=account.id,
            display_name="Other", role="tenant_admin")
        other = TenantPrincipal(account.id, membership.id, p.tenant_id, "tenant_admin")
        target = await SessionService(tx).create(other, agent_id=session.agent_id)
        with pytest.raises(AccessDenied):
            if owner == "trigger":
                await TriggerService(tx).create(p, agent_id=session.agent_id, config=TriggerConfig("private", "interval", "work",
                    interval_minutes=1, destination_kind="session", destination_id=target.id))
            else:
                await HeartbeatService(tx).configure(p, agent_id=session.agent_id,
                    config=HeartbeatConfig("work", 1, destination_kind="session", destination_id=target.id))


@pytest.mark.parametrize("owner", ["trigger", "heartbeat"])
async def test_native_group_destination_is_limited_to_original_topic(transaction_factory, owner):
    from modules.group.test_service import setup as setup_group
    from modules.group.test_service import started as start_group
    p, _, group, agent = await setup_group(transaction_factory)
    _, run = await start_group(transaction_factory, p, group, agent)
    scope = AgentToolResolutionScope(p.tenant_id, agent, "main")
    async with transaction_factory() as tx:
        original = await GroupService(tx).resolve_conversation(p, group_id=group.id)
        another = await GroupService(tx).create_conversation(p, group_id=group.id, title="Another topic")
        if owner == "trigger":
            operation = TriggerService(tx).create_for_agent
            config = TriggerConfig("native", "interval", "work", interval_minutes=1,
                destination_kind="group", destination_id=group.id)
        else:
            operation = HeartbeatService(tx).configure_for_agent
            config = HeartbeatConfig("work", 1, destination_kind="group", destination_id=group.id)
        with pytest.raises(AccessDenied):
            await operation(scope, config=replace(config, destination_conversation_id=another.id), origin_run=run)
        created = await operation(scope, config=config, origin_run=run)
        assert created.config.destination_conversation_id == original


async def test_agent_origin_visibility_does_not_become_receiver_agent_public(transaction_factory):
    p, source_session = await setup(transaction_factory)
    async with transaction_factory() as tx:
        source = await AgentService(tx).get(p, agent_id=source_session.agent_id)
        receiver = await AgentService(tx).create(p, name="Receiver", soul="Receiver", timezone="UTC", model_id=source.model_id)
        trigger = await TriggerService(tx).create(p, agent_id=receiver.id,
            config=TriggerConfig("from source", "on_message", "Process only supplied input", source_agent_id=source.id))
        await TriggerService(tx).accept(tenant_id=p.tenant_id, trigger_id=trigger.id, source_key="agent-source", now=NOW,
            event_kind="on_message", source_agent_id=source.id, origin=WorkspaceSubject("agent", source.id),
            input=InputContent("Original Agent visibility"))
        receiver_only = replace(p, role="member", allowed_agent_ids=frozenset({receiver.id}))
        both = replace(p, role="member", allowed_agent_ids=frozenset({source.id, receiver.id}))
        assert not (await TriggerService(tx).history(receiver_only, trigger_id=trigger.id)).items
        visible = await TriggerService(tx).history(both, trigger_id=trigger.id)
        assert visible.items[0].origin_id == source.id
