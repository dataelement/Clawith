import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx
import pytest
from e2e.test_direct_session import call, response
from e2e.test_runtime_product_owner_fixture import configure_agent, eventually
from execution_dependencies.test_resources import composed_database, configured  # noqa: F401
from sqlalchemy import delete, func, select

from app.application import create_app
from app.execution_dependencies import resources as composition
from app.execution_dependencies.message_tools import WorkspaceMessageFile
from app.infrastructure.http import create_stateless_http_client
from app.infrastructure.transactions import transaction
from app.modules.group.models import GroupEventRecord
from app.modules.group.public import GroupService
from app.modules.heartbeat.public import HeartbeatConfig, HeartbeatService
from app.modules.identity_tenant.public import IdentityService, TenantPrincipal
from app.modules.run.public import InputContent, RunService, ToolResultPayload
from app.modules.session.models import SessionRecord
from app.modules.session.public import SessionService
from app.modules.trigger.public import TriggerConfig, TriggerService
from app.modules.workspace.public import WorkspaceScope, WorkspaceSubject


@pytest.mark.parametrize("owner", ["trigger", "heartbeat"])
@pytest.mark.parametrize("target_kind", ["session", "group"])
@pytest.mark.parametrize("removed", [False, True])
async def test_explicit_scheduled_message_destination_preserves_source_and_result(
        test_database, composed_database, tmp_path, monkeypatch, owner, target_kind, removed):  # noqa: F811
    def provider(request):
        if request.method == "GET":
            return httpx.Response(404)
        body = json.loads(request.content)
        if any(tool["function"]["name"] == "capability_probe" for tool in body.get("tools", [])):
            return call("capability_probe", "probe", {"value": "ok"})
        if not any(message["role"] == "tool" for message in body["messages"]):
            return call("send_message", "publish", {"text": "Scheduled notification"})
        return response({"content": "Execution result remains available"})
    monkeypatch.setattr(composition, "create_stateless_http_client", lambda **kwargs:
        create_stateless_http_client(transport=httpx.MockTransport(provider), **kwargs))
    app = create_app(configured(tmp_path))
    async with app.router.lifespan_context(app):
        principal, agent, _ = await configure_agent(app.state.execution, test_database.sessions)
        now = datetime.now(UTC)
        async with transaction(test_database.sessions) as tx:
            conversation = None
            if target_kind == "session":
                target = await SessionService(tx).create(principal, agent_id=agent.id)
            else:
                target = await GroupService(tx).create(principal, name="Notifications")
                await GroupService(tx).set_agent(principal, group_id=target.id, agent_id=agent.id, enabled=True)
                conversation = await GroupService(tx).resolve_conversation(principal, group_id=target.id)
            destination = {"destination_kind": target_kind, "destination_id": target.id,
                "destination_conversation_id": conversation}
            if owner == "trigger":
                configured_trigger = await TriggerService(tx).create(principal, agent_id=agent.id,
                    config=TriggerConfig("notification", "interval", "Notify explicitly then finish", interval_minutes=1, **destination), now=now)
            else:
                await HeartbeatService(tx).configure(principal, agent_id=agent.id,
                    config=HeartbeatConfig("Notify explicitly then finish", 1, **destination), now=now)
            if removed:
                if target_kind == "session":
                    await tx.session.execute(delete(SessionRecord).where(SessionRecord.id == target.id))
                else:
                    await GroupService(tx).update(principal, group_id=target.id, name="Notifications", announcement="", enabled=False)
        if owner == "trigger":
            occurrence = await app.state.scheduled.fire_manual(principal, trigger_id=configured_trigger.id, event_id="one")
        else:
            app.state.scheduled._clock = lambda: now + timedelta(minutes=1)
            await app.state.scheduled.tick()
            async with transaction(test_database.sessions) as tx:
                occurrence = (await HeartbeatService(tx).history(principal, agent_id=agent.id)).items[0]
        run = await eventually(test_database.sessions, principal.tenant_id, occurrence.run_id, "Completed")
        assert run.source.kind == owner
        async with transaction(test_database.sessions) as tx:
            history = await RunService(tx).read_history(tenant_id=principal.tenant_id, run_id=run.id)
            result_owner = TriggerService(tx) if owner == "trigger" else HeartbeatService(tx)
            retained = await result_owner.get_occurrence(tenant_id=principal.tenant_id, occurrence_id=occurrence.id)
            assert retained.result.status == "Completed" and "remains available" in retained.result.output_preview
            delivery = next(entry.payload.result for entry in history.entries if isinstance(entry.payload, ToolResultPayload))
            assert delivery.status == ("error" if removed else "success"), delivery.content_json
            if target_kind == "session":
                if removed:
                    assert not (await SessionService(tx).list(principal)).sessions
                else:
                    entries = (await SessionService(tx).read_history(principal, session_id=target.id)).entries
                    assert len(entries) == 1 and entries[0].content.text == "Scheduled notification"
                    assert entries[0].source_run_id == run.id and entries[0].kind != "input"
                    assert not (await SessionService(tx).list_work(principal, session_id=target.id)).work
            elif removed:
                assert await tx.session.scalar(select(func.count()).select_from(GroupEventRecord).where(GroupEventRecord.group_id == target.id)) == 0
            else:
                entries = await GroupService(tx).list_events(principal, group_id=target.id, conversation_id=conversation)
                assert len(entries) == 1 and entries[0].input.text == "Scheduled notification"
                assert entries[0].source_run_id == run.id and entries[0].kind != "input"


async def test_private_message_trigger_cannot_publish_into_config_creators_different_session(
        test_database, composed_database, tmp_path, monkeypatch):  # noqa: F811
    def provider(request):
        if request.method == "GET":
            return httpx.Response(404)
        body = json.loads(request.content)
        if any(tool["function"]["name"] == "capability_probe" for tool in body.get("tools", [])):
            return call("capability_probe", "probe", {"value": "ok"})
        if not any(message["role"] == "tool" for message in body["messages"]):
            return call("send_message", "publish", {"text": "Do not disclose this private result to another User"})
        return response({"content": "Retained private execution result"})
    monkeypatch.setattr(composition, "create_stateless_http_client", lambda **kwargs:
        create_stateless_http_client(transport=httpx.MockTransport(provider), **kwargs))
    app = create_app(configured(tmp_path))
    async with app.router.lifespan_context(app):
        creator, agent, _ = await configure_agent(app.state.execution, test_database.sessions)
        async with transaction(test_database.sessions) as tx:
            creator_session = await SessionService(tx).create(creator, agent_id=agent.id)
            target = await TriggerService(tx).create(creator, agent_id=agent.id,
                config=TriggerConfig("incoming", "on_message", "Process incoming message",
                    destination_kind="session", destination_id=creator_session.id))
            identity = IdentityService(tx)
            account = await identity.create_account()
            member = await identity.create_membership(tenant_id=creator.tenant_id, account_id=account.id,
                display_name="Private sender", role="tenant_admin")
            sender = TenantPrincipal(account.id, member.id, creator.tenant_id, "tenant_admin")
            sender_session = await SessionService(tx).create(sender, agent_id=agent.id)
        await app.state.products.submit_session(sender, session_id=sender_session.id, source_key="private",
            input=InputContent("Private sender's source input"))
        async with transaction(test_database.sessions) as tx:
            accepted = (await TriggerService(tx).history(sender, trigger_id=target.id)).items[0]
        await eventually(test_database.sessions, sender.tenant_id, accepted.run_id, "Completed")
        async with transaction(test_database.sessions) as tx:
            assert not (await SessionService(tx).read_history(creator, session_id=creator_session.id)).entries
            assert not (await TriggerService(tx).history(creator, trigger_id=target.id)).items
            retained = (await TriggerService(tx).history(sender, trigger_id=target.id)).items[0]
            assert retained.result.status == "Completed" and "Retained private" in retained.result.output_preview
            history = await RunService(tx).read_history(tenant_id=sender.tenant_id, run_id=accepted.run_id)
            error = next(entry.payload.result for entry in history.entries if isinstance(entry.payload, ToolResultPayload))
            assert error.status == "error"


@pytest.mark.parametrize("target_kind", ["session", "group"])
async def test_scheduled_file_message_is_immutable_and_replay_does_not_recapture_deleted_source(
        test_database, composed_database, tmp_path, monkeypatch, target_kind):  # noqa: F811
    revision = None
    def provider(request):
        if request.method == "GET":
            return httpx.Response(404)
        body = json.loads(request.content)
        if any(tool["function"]["name"] == "capability_probe" for tool in body.get("tools", [])):
            return call("capability_probe", "probe", {"value": "ok"})
        if not any(message["role"] == "tool" for message in body["messages"]):
            return call("send_message", "publish-file", {"text": "File notification", "files": [{
                "path": "files/report.bin", "expected_revision": revision}]})
        return response({"content": "File publication finished"})
    monkeypatch.setattr(composition, "create_stateless_http_client", lambda **kwargs:
        create_stateless_http_client(transport=httpx.MockTransport(provider), **kwargs))
    app = create_app(configured(tmp_path))
    async with app.router.lifespan_context(app):
        p, agent, _ = await configure_agent(app.state.execution, test_database.sessions)
        subject = WorkspaceSubject("agent", agent.id)
        writer = WorkspaceScope(p.tenant_id, agent.id, subject, uuid4())
        await app.state.execution.workspace.ensure(writer, subject)
        original = b"\x00\xffscheduled original binary file"
        revision = await app.state.execution.workspace.write(writer, subject, "files/report.bin", original, expected_revision=None)
        async with transaction(test_database.sessions) as tx:
            topic = None
            if target_kind == "session":
                target = await SessionService(tx).create(p, agent_id=agent.id)
            else:
                target = await GroupService(tx).create(p, name="File notifications")
                await GroupService(tx).set_agent(p, group_id=target.id, agent_id=agent.id, enabled=True)
                topic = await GroupService(tx).resolve_conversation(p, group_id=target.id)
            configured_trigger = await TriggerService(tx).create(p, agent_id=agent.id, config=TriggerConfig(
                "file", "interval", "Send the report file", interval_minutes=1, destination_kind=target_kind,
                destination_id=target.id, destination_conversation_id=topic))
        occurrence = await app.state.scheduled.fire_manual(p, trigger_id=configured_trigger.id, event_id="file")
        await eventually(test_database.sessions, p.tenant_id, occurrence.run_id, "Completed")
        async with transaction(test_database.sessions) as tx:
            if target_kind == "session":
                entries = (await SessionService(tx).read_history(p, session_id=target.id)).entries
                content = entries[0].content
            else:
                entries = await GroupService(tx).list_events(p, group_id=target.id, conversation_id=topic)
                content = entries[0].input
            assert len(entries) == 1 and len(content.references) == 1
            message = entries[0]
            snapshot = await RunService(tx).read_snapshot(tenant_id=p.tenant_id, run_id=occurrence.run_id)
        await app.state.execution.workspace.delete(writer, subject, "files/report.bin", expected_revision=revision)
        loaded = await app.state.attachment_inputs.read_for_delivery(tenant_id=p.tenant_id, agent_id=agent.id,
            message_id=message.id, reference=content.references[0].reference, kind=target_kind)
        assert loaded.content == original
        replay = await app.state.products.send_message(snapshot, message.step_id, message.call_id,
            InputContent("File notification"), (WorkspaceMessageFile("files/report.bin", revision, "output"),))
        assert replay["message_id"] == str(message.id)
