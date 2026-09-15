import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from modules.capability_market.test_service import seed
from modules.run.test_lifecycle import snapshot
from sqlalchemy import select

from app.infrastructure.errors import AccessDenied, Conflict, InvalidInput, NotFound
from app.infrastructure.transactions import transaction
from app.modules.run.public import InputContent, RunService
from app.modules.tool.public import AgentToolResolutionScope
from app.modules.trigger.models import AgentTriggerRecord
from app.modules.trigger.public import TriggerConfig, TriggerService
from app.modules.workspace.public import WorkspaceSubject

BASE = datetime(2026, 9, 9, 0, tzinfo=UTC)


async def create(test_database, *, config=None):
    principal, agent, other = await seed(test_database.sessions)
    async with transaction(test_database.sessions) as tx:
        view = await TriggerService(tx).create(principal, agent_id=agent.id,
            config=config or TriggerConfig("interval", "interval", "Check work", interval_minutes=5), now=BASE)
    return principal, agent, other, view


async def test_due_accept_is_idempotent_and_next_cycle_does_not_replay_old_work(test_database):
    principal, _, _, view = await create(test_database)
    now = BASE + timedelta(minutes=5)
    async with transaction(test_database.sessions) as tx:
        service = TriggerService(tx)
        due = await service.due(now=now, not_before=BASE)
        assert len(due.items) == 1
        occurrence = await service.accept(tenant_id=principal.tenant_id, trigger_id=view.id,
            source_key=due.items[0].source_key, due_at=now, now=now, not_before=BASE)
        duplicate = await service.accept(tenant_id=principal.tenant_id, trigger_id=view.id,
            source_key=due.items[0].source_key, due_at=now, now=now, not_before=BASE)
        assert duplicate == occurrence
        assert occurrence.source.kind == "trigger" and occurrence.source.owner_id == occurrence.id
        assert occurrence.input.text == "Check work" and occurrence.delegated_connection_ids == ()
        assert not (await service.due(now=now, not_before=BASE)).items
        assert not (await service.due(now=now + timedelta(minutes=2), not_before=now + timedelta(minutes=1))).items
        later = await service.due(now=now + timedelta(minutes=5), not_before=now + timedelta(minutes=1))
        assert later.items[0].due_at == now + timedelta(minutes=5)


@pytest.mark.parametrize("kind", ["once", "cron", "interval"])
async def test_schedule_types_and_timezone(test_database, kind):
    options = {"once": {"at": BASE + timedelta(minutes=5)},
        "cron": {"cron_expression": "5 8 * * *", "timezone": "Asia/Shanghai"},
        "interval": {"interval_minutes": 5}}
    principal, _, _, view = await create(test_database, config=TriggerConfig(kind, kind, "work", **options[kind]))
    async with transaction(test_database.sessions) as tx:
        service = TriggerService(tx)
        now = BASE + timedelta(minutes=5)
        item = (await service.due(now=now, not_before=BASE)).items[0]
        assert item.due_at == now
        await service.accept(tenant_id=principal.tenant_id, trigger_id=view.id, source_key=item.source_key,
            now=now, due_at=item.due_at, not_before=BASE)
        assert not (await service.due(now=now, not_before=BASE)).items


async def test_poll_change_baseline_duplicate_and_next_value(test_database):
    principal, _, _, view = await create(test_database, config=TriggerConfig("poll", "poll", "Check change",
        interval_minutes=5, poll_url="https://status.invalid/data"))
    async with transaction(test_database.sessions) as tx:
        service = TriggerService(tx)
        first = BASE + timedelta(minutes=5)
        assert await service.observe_poll(tenant_id=principal.tenant_id, trigger_id=view.id,
            due_at=first, now=first, not_before=BASE, value="old", expected_config=view.config) is None
        assert await service.observe_poll(tenant_id=principal.tenant_id, trigger_id=view.id,
            due_at=first, now=first, not_before=BASE, value="different duplicate", expected_config=view.config) is None
        assert not (await service.due(now=first, not_before=BASE)).items
        second = first + timedelta(minutes=5)
        occurrence = await service.observe_poll(tenant_id=principal.tenant_id, trigger_id=view.id,
            due_at=second, now=second, not_before=BASE, value="new", expected_config=view.config)
        assert occurrence is not None and occurrence.input.text.endswith("new")
        assert await service.observe_poll(tenant_id=principal.tenant_id, trigger_id=view.id,
            due_at=second, now=second, not_before=BASE, value="another", expected_config=view.config) == occurrence


@pytest.mark.parametrize("kind", ["webhook", "on_message"])
async def test_event_occurrence_limits_and_source_scope(test_database, kind):
    principal, _, other, view = await create(test_database,
        config=TriggerConfig("event", kind, "Handle event", max_fires=1))
    async with transaction(test_database.sessions) as tx:
        service = TriggerService(tx)
        assert not (await service.due(now=BASE, not_before=BASE)).items
        occurrence = await service.accept(tenant_id=principal.tenant_id, trigger_id=view.id, source_key="event-1",
            now=BASE, input=InputContent("payload"), event_kind=kind, source_agent_id=other.id, origin=WorkspaceSubject("agent", view.agent_id))
        assert occurrence.input.text.endswith("payload")
        with pytest.raises(Conflict):
            await service.accept(tenant_id=principal.tenant_id, trigger_id=view.id, source_key="event-2",
                now=BASE, event_kind=kind, source_agent_id=other.id, origin=WorkspaceSubject("agent", view.agent_id))
        assert await service.accept(tenant_id=principal.tenant_id, trigger_id=view.id, source_key="event-1",
            now=BASE, event_kind=kind, source_agent_id=other.id, origin=WorkspaceSubject("agent", view.agent_id)) == occurrence


async def test_concurrent_occurrence_admission_creates_one_fact(test_database):
    principal, _, _, view = await create(test_database, config=TriggerConfig("webhook", "webhook", "Work"))
    async def accept():
        async with transaction(test_database.sessions) as tx:
            return await TriggerService(tx).accept(tenant_id=principal.tenant_id, trigger_id=view.id,
                source_key="same", now=BASE, event_kind="webhook")
    values = await asyncio.gather(*(accept() for _ in range(6)))
    assert len({value.id for value in values}) == 1
    async with transaction(test_database.sessions) as tx:
        row = await tx.session.scalar(select(AgentTriggerRecord).where(AgentTriggerRecord.id == view.id))
        assert row.configuration["fire_count"] == 1


async def test_tenant_member_delegation_and_invalid_payload_boundaries(test_database):
    principal, agent, _, view = await create(test_database)
    async with transaction(test_database.sessions) as tx:
        service = TriggerService(tx)
        with pytest.raises(NotFound):
            await service.get(replace(principal, tenant_id=uuid4()), trigger_id=view.id)
        with pytest.raises(AccessDenied):
            await service.get(replace(principal, role="member"), trigger_id=view.id)
        with pytest.raises(AccessDenied):
            await service.create(principal, agent_id=agent.id, config=view.config, delegated_connection_ids=(uuid4(),))
        with pytest.raises(InvalidInput):
            await service.due(now=BASE, not_before=BASE, limit=101)
        row = await tx.session.scalar(select(AgentTriggerRecord).where(AgentTriggerRecord.id == view.id))
        row.configuration_version = 2
        await tx.session.flush()
        with pytest.raises(InvalidInput):
            await service.get(principal, trigger_id=view.id)


async def test_started_and_terminal_callbacks_share_real_run_transaction(test_database):
    principal, agent, _, view = await create(test_database, config=TriggerConfig("webhook", "webhook", "Work"))
    async with transaction(test_database.sessions) as tx:
        occurrence = await TriggerService(tx).accept(tenant_id=principal.tenant_id, trigger_id=view.id,
            source_key="call", now=BASE, event_kind="webhook")
    run_id = uuid4()
    async with transaction(test_database.sessions) as tx:
        service = TriggerService(tx)
        run = (await RunService(tx).start(tenant_id=principal.tenant_id, agent_id=agent.id, run_id=run_id,
            source=occurrence.source, input=occurrence.input, snapshot=snapshot(principal.tenant_id, agent.id, run_id),
            start_consumer=service)).run
        assert (await service.get_occurrence(tenant_id=principal.tenant_id, occurrence_id=occurrence.id)).run_id == run.id
        await RunService(tx).terminate(tenant_id=principal.tenant_id, run_id=run.id, status="Cancelled", reason="test", consumer=service)
    async with transaction(test_database.sessions) as tx:
        stored = await TriggerService(tx).get_occurrence(tenant_id=principal.tenant_id, occurrence_id=occurrence.id)
        assert stored.admission == "started" and stored.result.status == "Cancelled"


async def test_native_scope_and_removed_configuration_keep_history(test_database):
    principal, agent, other, view = await create(test_database)
    scope = AgentToolResolutionScope(principal.tenant_id, agent.id, "main")
    async with transaction(test_database.sessions) as tx:
        service = TriggerService(tx)
        created = await service.create_for_agent(scope, config=TriggerConfig("self", "webhook", "Work"))
        assert (await service.get_for_agent(scope, trigger_id=created.id)).agent_id == agent.id
        with pytest.raises(AccessDenied):
            await service.update_for_agent(replace(scope, agent_id=other.id), trigger_id=view.id,
                config=view.config, enabled=True)
        with pytest.raises(AccessDenied):
            await service.create_for_agent(replace(scope, role="sub"), config=view.config)
        with pytest.raises(AccessDenied):
            await service.create_for_agent(replace(scope, selected_personal_connections=(uuid4(),)), config=view.config)
        occurrence = await service.accept(tenant_id=principal.tenant_id, trigger_id=created.id,
            source_key="once", now=BASE, event_kind="webhook")
        removed = await service.remove_for_agent(scope, trigger_id=created.id)
        assert removed.removed_at is not None and not removed.enabled
        assert created.id not in {item.id for item in await service.list_for_agent(scope)}
        assert (await service.history(principal, trigger_id=created.id)).items[0].id == occurrence.id
        with pytest.raises(Conflict):
            await service.update_for_agent(scope, trigger_id=created.id, config=created.config, enabled=True)


async def test_start_consumer_rollback_keeps_pending_admission(test_database):
    principal, agent, _, view = await create(test_database, config=TriggerConfig("event", "webhook", "Work"))
    async with transaction(test_database.sessions) as tx:
        occurrence = await TriggerService(tx).accept(tenant_id=principal.tenant_id, trigger_id=view.id,
            source_key="rollback", now=BASE, event_kind="webhook")
    class Failing:
        async def record_started(self, transaction, *, run):
            await TriggerService(transaction).record_started(transaction, run=run)
            raise RuntimeError("rollback owner")
    run_id = uuid4()
    with pytest.raises(RuntimeError):
        async with transaction(test_database.sessions) as tx:
            await RunService(tx).start(tenant_id=principal.tenant_id, agent_id=agent.id, run_id=run_id,
                source=occurrence.source, input=occurrence.input, snapshot=snapshot(principal.tenant_id, agent.id, run_id),
                start_consumer=Failing())
    async with transaction(test_database.sessions) as tx:
        stored = await TriggerService(tx).get_occurrence(tenant_id=principal.tenant_id, occurrence_id=occurrence.id)
        assert stored.run_id is None and stored.admission == "pending"
        with pytest.raises(NotFound):
            await RunService(tx).get(tenant_id=principal.tenant_id, run_id=run_id)


async def test_due_page_exposes_scanned_cursor_even_when_no_candidate(test_database):
    principal, agent, _, _ = await create(test_database, config=TriggerConfig("none", "webhook", "Work"))
    async with transaction(test_database.sessions) as tx:
        service = TriggerService(tx)
        await service.create(principal, agent_id=agent.id, config=TriggerConfig("none2", "webhook", "Work"), now=BASE)
        first = await service.due(now=BASE, not_before=BASE, limit=1)
        assert not first.items and first.next_after_id is not None
        second = await service.due(now=BASE, not_before=BASE, limit=1, after_id=first.next_after_id)
        assert not second.items and second.next_after_id != first.next_after_id


async def test_poll_config_change_rejects_old_http_result(test_database):
    principal, _, _, view = await create(test_database, config=TriggerConfig("poll", "poll", "Check change",
        interval_minutes=5, poll_url="https://old.invalid/data"))
    async with transaction(test_database.sessions) as tx:
        service = TriggerService(tx)
        await service.update(principal, trigger_id=view.id,
            config=replace(view.config, poll_url="https://new.invalid/data"), enabled=True)
        with pytest.raises(Conflict, match="changed"):
            await service.observe_poll(tenant_id=principal.tenant_id, trigger_id=view.id,
                due_at=BASE + timedelta(minutes=5), now=BASE + timedelta(minutes=5), not_before=BASE,
                value="old-source-value", expected_config=view.config)


def test_cron_calendar_reachability_preserves_leap_day():
    with pytest.raises(InvalidInput, match="reachable"):
        TriggerConfig("impossible", "cron", "Work", cron_expression="0 0 31 2 *")
    assert TriggerConfig("leap day", "cron", "Work", cron_expression="0 0 29 2 *").kind == "cron"


async def test_unreachable_stored_configuration_does_not_block_other_due_work(test_database):
    principal, agent, _, valid = await create(test_database)
    async with transaction(test_database.sessions) as tx:
        service = TriggerService(tx)
        invalid = await service.create(principal, agent_id=agent.id,
            config=TriggerConfig("cron", "cron", "Work", cron_expression="* * * * *"), now=BASE)
        row = await tx.session.scalar(select(AgentTriggerRecord).where(AgentTriggerRecord.id == invalid.id))
        row.configuration = {**row.configuration, "spec": {**row.configuration["spec"], "cron_expression": "0 0 31 2 *"}}
        await tx.session.flush()
        page = await service.due(now=BASE + timedelta(minutes=5), not_before=BASE)
        assert [item.trigger.id for item in page.items] == [valid.id]
        assert len(page.errors) == 1 and page.errors[0].trigger_id == invalid.id


async def test_once_creation_and_update_require_future_instant(test_database):
    principal, agent, _, view = await create(test_database)
    async with transaction(test_database.sessions) as tx:
        service = TriggerService(tx)
        with pytest.raises(InvalidInput, match="future"):
            await service.create(principal, agent_id=agent.id,
                config=TriggerConfig("past", "once", "Work", at=BASE), now=BASE)
        with pytest.raises(InvalidInput, match="future"):
            await service.update(principal, trigger_id=view.id,
                config=TriggerConfig("past", "once", "Work", at=BASE), enabled=True)


@pytest.mark.parametrize("options", [{"kind": "interval", "interval_minutes": 0}, {"kind": "cron", "cron_expression": "bad"},
    {"kind": "once", "at": BASE.replace(tzinfo=None)}, {"kind": "webhook", "timezone": "../invalid"},
    {"kind": "poll", "interval_minutes": 5, "poll_url": "file:///secret"}, {"kind": "webhook", "cooldown_seconds": -1}])
def test_invalid_configuration_fails_early(options):
    with pytest.raises(InvalidInput):
        TriggerConfig("invalid", instruction="work", **options)
