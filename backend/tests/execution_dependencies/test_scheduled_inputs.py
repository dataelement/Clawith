"""Normal clock dispatch through real configuration, Run and Provider adapter owners."""

import asyncio
import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from e2e.test_runtime_product_owner_fixture import configure_agent
from sqlalchemy import update

from app import application
from app.execution_dependencies import resources as composition
from app.execution_dependencies.runtime import RuntimeToolBatches
from app.execution_dependencies.scheduled_inputs import ScheduledInputs
from app.infrastructure.http import create_stateless_http_client
from app.infrastructure.transactions import transaction
from app.modules.heartbeat.public import HeartbeatConfig, HeartbeatService
from app.modules.identity_tenant.models import TenantRecord
from app.modules.identity_tenant.public import IdentityService
from app.modules.run.public import RunRuntime, RunService
from app.modules.trigger.public import TriggerConfig, TriggerService
from execution_dependencies.test_resources import composed_database, configured  # noqa: F401


async def test_interval_once_and_heartbeat_dispatch_without_catchup_or_duplicate(test_database, composed_database, tmp_path, monkeypatch):  # noqa: F811
    observed = []
    def provider(request):
        if request.method == "GET":
            return httpx.Response(404)
        body = json.loads(request.content)
        if any(tool["function"]["name"] == "capability_probe" for tool in body.get("tools", [])):
            return httpx.Response(200, json={"choices": [{"finish_reason": "tool_calls", "message": {
                "tool_calls": [{"id": "probe", "function": {"name": "capability_probe", "arguments": '{"value":"ok"}'}}]}}]})
        observed.append(body)
        return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {"content": "scheduled result"}}]})
    monkeypatch.setattr(composition, "create_stateless_http_client",
        lambda **kwargs: create_stateless_http_client(transport=httpx.MockTransport(provider), **kwargs))
    app = application.create_app(configured(tmp_path))
    async with app.router.lifespan_context(app):
        p, agent, _ = await configure_agent(app.state.execution, test_database.sessions)
        now = [datetime(2026, 9, 9, tzinfo=UTC)]
        async with transaction(test_database.sessions) as tx:
            trigger = TriggerService(tx)
            interval = await trigger.create(p, agent_id=agent.id,
                config=TriggerConfig("interval", "interval", "interval work", interval_minutes=1), now=now[0])
            once = await trigger.create(p, agent_id=agent.id,
                config=TriggerConfig("once", "once", "once work", at=now[0] + timedelta(minutes=1)), now=now[0])
            await HeartbeatService(tx).configure(p, agent_id=agent.id, config=HeartbeatConfig("heartbeat work", 1), now=now[0])
        await app.state.runtime.close()
        scheduled = ScheduledInputs(app.state.database, app.state.execution, clock=lambda: now[0], poll_interval_seconds=.01)
        batches = RuntimeToolBatches(app.state.execution)
        engine = RunRuntime(control_sessions=test_database.sessions, execution_sessions=test_database.sessions,
            model=app.state.execution.model, tools=batches, consumer=scheduled, start_consumer=scheduled)
        batches.runtime, scheduled.runtime = engine, engine
        await engine.startup()
        await scheduled.start()
        try:
            now[0] += timedelta(minutes=1)
            async with asyncio.timeout(10):
                while True:
                    async with transaction(test_database.sessions) as tx:
                        first = await TriggerService(tx).history(p, trigger_id=interval.id)
                        second = await TriggerService(tx).history(p, trigger_id=once.id)
                        heartbeat = await HeartbeatService(tx).history(p, agent_id=agent.id)
                    rows = first.items + second.items + heartbeat.items
                    if len(rows) == 3 and all(row.result is not None for row in rows):
                        break
                    await asyncio.sleep(.02)
            assert len(observed) == 3 and all(row.result.status == "Completed" for row in rows)
            async with transaction(test_database.sessions) as tx:
                for row in rows:
                    captured = await RunService(tx).read_snapshot(tenant_id=p.tenant_id, run_id=row.run_id)
                    assert "need_input" not in captured.initial_direct_names
                    assert any("unattended scheduled execution" in source.content for source in captured.sources)
            await scheduled.tick()
            assert len(observed) == 3
            assert len(await _triggers(test_database, p, agent.id)) == 2
        finally:
            await scheduled.close()
            await engine.close()
        assert scheduled._task.done() and engine.dispatcher.admitted == 0
        # Restart past missed periods: neither pending occurrences nor missed intervals execute.
        now[0] += timedelta(minutes=10, seconds=1)
        restarted = ScheduledInputs(app.state.database, app.state.execution, clock=lambda: now[0], poll_interval_seconds=.01)
        restarted.runtime = app.state.runtime
        await restarted.start()
        try:
            await restarted.tick()
            assert len(observed) == 3
            async with transaction(test_database.sessions) as tx:
                assert len((await TriggerService(tx).history(p, trigger_id=interval.id)).items) == 1
                assert len((await TriggerService(tx).history(p, trigger_id=once.id)).items) == 1
                assert len((await HeartbeatService(tx).history(p, agent_id=agent.id)).items) == 1
        finally:
            await restarted.close()


async def _triggers(database, principal, agent):
    async with transaction(database.sessions) as tx:
        return await TriggerService(tx).list(principal, agent_id=agent)


async def test_disabled_tenant_is_filtered_before_occurrence_or_provider_start(test_database, composed_database, tmp_path, monkeypatch):  # noqa: F811
    from uuid import uuid4

    import pytest
    from modules.session.test_session import setup

    from app.infrastructure.errors import InvalidInput

    async def factory_provider(*args, **kwargs):
        raise AssertionError("disabled Tenant must not reach Provider")
    # This case exercises real due/configuration ownership without needing a configured Model peer.
    from runtime.test_engine import Model
    p, session = await setup(lambda: transaction(test_database.sessions))
    now = [datetime(2026, 9, 9, tzinfo=UTC)]
    async with transaction(test_database.sessions) as tx:
        trigger = await TriggerService(tx).create(p, agent_id=session.agent_id,
            config=TriggerConfig("interval", "interval", "work", interval_minutes=1), now=now[0])
        await tx.session.execute(update(TenantRecord).where(TenantRecord.id == p.tenant_id).values(enabled=False))
        with pytest.raises(InvalidInput):
            await IdentityService(tx).filter_enabled_tenant_ids(tenant_ids=tuple(uuid4() for _ in range(101)))
    scheduled = ScheduledInputs(composed_database[0], None, clock=lambda: now[0], poll_interval_seconds=.01)
    scheduled.runtime = Model(factory_provider)
    await scheduled.start()
    try:
        now[0] += timedelta(minutes=1)
        await scheduled.tick()
        async with transaction(test_database.sessions) as tx:
            assert (await TriggerService(tx).history(p, trigger_id=trigger.id)).items == ()
    finally:
        await scheduled.close()


async def _credential_fixture(database):
    from types import SimpleNamespace

    from modules.capability_market.test_service import seed

    from app.modules.credential.public import CredentialKeyring, CredentialService, Secret
    p, agent, other = await seed(database.sessions)
    keyring = CredentialKeyring(active_key_version="k1", keys={"k1": b"a" * 32})
    resources = SimpleNamespace(credentials=lambda tx: CredentialService(tx, keyring))
    async with transaction(database.sessions) as tx:
        secret = await resources.credentials(tx).create(p, kind="api_token", provider="schedule", label="schedule",
            secret=Secret("Basic exact-value"), owner_kind="tenant")
    return p, agent, other, secret, resources


async def test_poll_uses_exact_credential_method_headers_and_path_before_change_acceptance(test_database, composed_database):  # noqa: F811
    p, agent, _, credential, resources = await _credential_fixture(test_database)
    now = [datetime(2026, 9, 9, tzinfo=UTC)]
    value, requests = ["old"], []
    def respond(request):
        requests.append(request)
        return httpx.Response(200, json={"state": {"value": value[0]}})
    async with create_stateless_http_client(transport=httpx.MockTransport(respond)) as client:
        resources.http = client
        inputs = ScheduledInputs(composed_database[0], resources, clock=lambda: now[0])
        inputs._not_before = now[0]
        async with transaction(test_database.sessions) as tx:
            trigger = await TriggerService(tx).create(p, agent_id=agent.id, now=now[0], config=TriggerConfig(
                "poll", "poll", "inspect change", interval_minutes=1, poll_url="https://poll.invalid/state",
                poll_method="POST", poll_headers=(("X-Mode", "status"),), poll_json_path="$.state.value",
                poll_credential_id=credential.id))
        for expected in (None, "new"):
            now[0] += timedelta(minutes=1)
            async with transaction(test_database.sessions) as tx:
                due = (await TriggerService(tx).due(now=now[0], not_before=inputs._not_before)).items[0]
            result = await inputs._poll(due)
            if expected is None:
                assert result is None
                value[0] = "new"
            else:
                assert result.input.text.endswith(expected)
                async with transaction(test_database.sessions) as tx:
                    assert len((await TriggerService(tx).history(p, trigger_id=trigger.id)).items) == 1
        assert len(requests) == 2
        assert all(request.method == "POST" and request.headers["authorization"] == "Basic exact-value"
            and request.headers["x-mode"] == "status" for request in requests)


async def test_webhook_authenticates_exact_body_and_event_before_deduplicated_acceptance(test_database, composed_database):  # noqa: F811
    import hashlib
    import hmac

    import pytest

    from app.infrastructure.errors import AccessDenied
    p, agent, _, credential, resources = await _credential_fixture(test_database)
    now = datetime(2026, 9, 9, tzinfo=UTC)
    class Intake(ScheduledInputs):
        async def _execute(self, occurrence, **kwargs):
            pass  # Run execution is covered by the normal clock integration case.
    inputs = Intake(composed_database[0], resources, clock=lambda: now)
    async with transaction(test_database.sessions) as tx:
        target = await TriggerService(tx).create(p, agent_id=agent.id, now=now,
            config=TriggerConfig("hook", "webhook", "handle hook", webhook_credential_id=credential.id))
    body = b'{"event":"accepted"}'
    signature = hmac.new(b"Basic exact-value", b"event-1\n" + body, hashlib.sha256).hexdigest()
    first = await inputs.webhook(tenant_id=p.tenant_id, trigger_id=target.id, event_id="event-1", signature=signature, body=body)
    duplicate = await inputs.webhook(tenant_id=p.tenant_id, trigger_id=target.id, event_id="event-1", signature=signature, body=body)
    assert first.id == duplicate.id and body.decode() in first.input.text
    for event, payload, signed in (("event-2", body, signature), ("event-1", b"changed", signature), ("event-1", body, "x" * 64)):
        with pytest.raises(AccessDenied):
            await inputs.webhook(tenant_id=p.tenant_id, trigger_id=target.id, event_id=event, signature=signed, body=payload)
    async with transaction(test_database.sessions) as tx:
        assert len((await TriggerService(tx).history(p, trigger_id=target.id)).items) == 1


async def test_message_trigger_only_sees_target_and_sender_with_original_private_scope(test_database, composed_database):  # noqa: F811
    from uuid import uuid4

    from app.modules.run.public import InputContent, InputReference
    from app.modules.workspace.public import WorkspaceScope, WorkspaceSubject
    p, agent, other, _, resources = await _credential_fixture(test_database)
    now = datetime(2026, 9, 9, tzinfo=UTC)
    received = []
    class Intake(ScheduledInputs):
        async def _execute(self, occurrence, *, workspace=None):
            received.append((occurrence, workspace))
    inputs = Intake(composed_database[0], resources, clock=lambda: now)
    async with transaction(test_database.sessions) as tx:
        first = await TriggerService(tx).create(p, agent_id=agent.id, now=now,
            config=TriggerConfig("personal", "on_message", "handle personal", source_membership_id=p.membership_id))
        second = await TriggerService(tx).create(p, agent_id=other.id, now=now,
            config=TriggerConfig("other", "on_message", "not this Agent", source_membership_id=p.membership_id))
    scope = WorkspaceScope(p.tenant_id, agent.id, WorkspaceSubject("membership", p.membership_id), uuid4())
    message = InputContent("private message", references=(InputReference("opaque:file", "attachment"),))
    assert await inputs.on_message(message_id=uuid4(), input=message, workspace=scope, source_membership_id=p.membership_id) == 1
    assert received[0][1] == scope and received[0][0].input.references == message.references
    assert await inputs.on_message(message_id=uuid4(), input=InputContent("agent message"), workspace=scope, source_agent_id=other.id) == 0
    async with transaction(test_database.sessions) as tx:
        assert len((await TriggerService(tx).history(p, trigger_id=first.id)).items) == 1
        assert (await TriggerService(tx).history(p, trigger_id=second.id)).items == ()


async def test_native_schedule_tools_verify_actual_main_call_use_agent_timezone_and_fragment_large_config(test_database, composed_database):  # noqa: F811
    from dataclasses import replace
    from types import SimpleNamespace
    from uuid import uuid4

    from modules.run.test_lifecycle import snapshot

    from app.execution_dependencies.schedule_tools import SCHEDULE_TOOL_DEFINITIONS, schedule_tool_bindings
    from app.modules.agent.public import AgentService
    from app.modules.model.public import ModelStepResult, ModelToolCall, ModelUsage
    from app.modules.run.public import InputContent, ModelStepPayload, RunService, SourceIdentity
    from app.modules.tool.public import (
        AgentToolResolutionScope,
        AuthorizedToolSet,
        CallScope,
        ResolvedTool,
        ToolCall,
        ToolDefinition,
    )
    p, agent, _, _, resources = await _credential_fixture(test_database)
    resources.market = SimpleNamespace(enabled_source_ids=None)
    inputs = ScheduledInputs(composed_database[0], resources)
    run_id = uuid4()
    scope = AgentToolResolutionScope(p.tenant_id, agent.id, "main")
    tools = tuple(ResolvedTool(ToolDefinition(uuid4(), p.tenant_id, spec), None) for spec in SCHEDULE_TOOL_DEFINITIONS)
    start_snapshot = replace(snapshot(p.tenant_id, agent.id, run_id), tools=AuthorizedToolSet(p.tenant_id, agent.id, tools),
        initial_direct_names=frozenset(spec.name for spec in SCHEDULE_TOOL_DEFINITIONS))
    async with transaction(test_database.sessions) as tx:
        await AgentService(tx).update(p, agent_id=agent.id, timezone="Asia/Shanghai")
        await RunService(tx).start(tenant_id=p.tenant_id, agent_id=agent.id, run_id=run_id, snapshot=start_snapshot,
            input=InputContent("configure schedule"), source=SourceIdentity("session", uuid4(), "schedule"))
    bindings = schedule_tool_bindings(inputs=inputs, scope=scope, run_id=run_id, step_id="step")
    assert schedule_tool_bindings(inputs=inputs, scope=replace(scope, role="sub"), run_id=run_id, step_id="step") == ()
    args = {"action": "create", "config": {"name": "big", "kind": "interval", "instruction": "汉" * 20000, "interval_minutes": 1}}
    tool = tools[0]
    call = ToolCall("create", "trigger", json.dumps(args, ensure_ascii=False))
    call_scope = CallScope(p.tenant_id, agent.id, run_id)
    executor = bindings[0].executor
    assert (await executor.execute(tool, call, call_scope)).status == "error"
    async with transaction(test_database.sessions) as tx:
        await RunService(tx).record_model_step(tenant_id=p.tenant_id, run_id=run_id, payload=ModelStepPayload("step", 1,
            ModelStepResult("", (ModelToolCall(call.id, call.name, call.arguments_json),), "tool_calls", ModelUsage(), "step", False)))
    created = await executor.execute(tool, call, call_scope)
    assert created.status == "success" and len(created.content_json.encode()) < 250000
    partial = json.loads(created.content_json)
    assert partial["next_offset"] == 16000
    async with transaction(test_database.sessions) as tx:
        values = await TriggerService(tx).list_for_agent(scope)
        assert len(values) == 1 and values[0].config.timezone == "Asia/Shanghai"
    # A different Tenant definition cannot reuse the verified call correlation.
    foreign = replace(tool, definition=replace(tool.definition, tenant_id=uuid4()))
    assert (await executor.execute(foreign, call, call_scope)).status == "error"


@pytest.mark.parametrize("headers", [(("Authorization", "secret"),), (("X-Test", "汉"),), (("bad name", "value"),), (("X-Test", "value\x00"),)])
def test_poll_header_configuration_rejects_secret_or_invalid_wire_values(headers):
    from app.infrastructure.errors import InvalidInput
    with pytest.raises(InvalidInput):
        TriggerConfig("poll", "poll", "work", interval_minutes=1, poll_url="https://poll.invalid", poll_headers=headers)


async def test_webhook_http_rejects_missing_auth_and_oversized_body_before_intake(test_database, composed_database):  # noqa: F811
    from uuid import uuid4

    from fastapi import FastAPI

    from app.api.product_inputs.schedules import router
    class ForbiddenIntake:
        async def webhook(self, **kwargs):
            raise AssertionError("Rejected HTTP input must not reach occurrence intake")
    app = FastAPI()
    app.include_router(router)
    app.state.scheduled = ForbiddenIntake()
    url = f"/api/webhooks/{uuid4()}/{uuid4()}"
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        assert (await client.post(url, content="body")).status_code == 401
        assert (await client.post(url, headers={"X-Event-ID": "event", "X-Signature": "0" * 64}, content=b"x" * 65537)).status_code == 413


async def test_manual_retry_cannot_return_another_members_private_accepted_result(test_database, composed_database):  # noqa: F811
    from app.infrastructure.errors import AccessDenied
    from app.modules.identity_tenant.public import TenantPrincipal
    from app.modules.run.public import InputContent
    from app.modules.workspace.public import WorkspaceSubject
    principal, agent, _, _, resources = await _credential_fixture(test_database)
    now = datetime(2026, 9, 9, tzinfo=UTC)
    async with transaction(test_database.sessions) as tx:
        identities = IdentityService(tx)
        account = await identities.create_account()
        membership = await identities.create_membership(tenant_id=principal.tenant_id, account_id=account.id,
            display_name="Other", role="tenant_admin")
        other = TenantPrincipal(account.id, membership.id, principal.tenant_id, "tenant_admin")
        owner = TriggerService(tx)
        configured = await owner.create(principal, agent_id=agent.id, config=TriggerConfig("accepted", "interval", "work", interval_minutes=1), now=now)
        await owner.accept(tenant_id=principal.tenant_id, trigger_id=configured.id, source_key="manual:private", now=now,
            event_kind="manual", origin=WorkspaceSubject("membership", principal.membership_id), input=InputContent("private accepted content"))
    inputs = ScheduledInputs(composed_database[0], resources, clock=lambda: now)
    with pytest.raises(AccessDenied):
        await inputs.fire_manual(other, trigger_id=configured.id, event_id="private")


async def test_execution_origin_preserves_private_scope_without_a_delivery_destination(test_database, composed_database):  # noqa: F811
    from dataclasses import replace
    from uuid import uuid4

    from modules.run.test_lifecycle import snapshot

    from app.infrastructure.errors import AccessDenied
    from app.modules.run.public import InputContent
    from app.modules.workspace.public import WorkspaceSubject
    principal, agent, _, _, resources = await _credential_fixture(test_database)
    inputs = ScheduledInputs(composed_database[0], resources)
    run_id = uuid4()
    async with transaction(test_database.sessions) as tx:
        owner = TriggerService(tx)
        target = await owner.create(principal, agent_id=agent.id, config=TriggerConfig("private", "on_message", "Process only supplied input"))
        occurrence = await owner.accept(tenant_id=principal.tenant_id, trigger_id=target.id, source_key="private-origin",
            now=datetime.now(UTC), event_kind="on_message", source_membership_id=principal.membership_id,
            origin=WorkspaceSubject("membership", principal.membership_id), input=InputContent("Private input"))
        started = await RunService(tx).start(tenant_id=principal.tenant_id, agent_id=agent.id, run_id=run_id,
            snapshot=replace(snapshot(principal.tenant_id, agent.id, run_id), allow_human_input=False),
            source=occurrence.source, input=occurrence.input, start_consumer=inputs)
        assert await inputs.execution_destination(tx, started.run) is None
        assert await inputs.execution_origin(tx, started.run) == (WorkspaceSubject("membership", principal.membership_id), None)
        with pytest.raises(AccessDenied):
            await inputs.execution_origin(tx, replace(started.run, run_id=uuid4()))
