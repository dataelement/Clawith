"""Scheduled replies use existing Channel destinations without fabricated human input."""

import asyncio
import json

import httpx
import pytest
from e2e.test_channel_inputs import slack_configuration, slack_event, wait_messages
from e2e.test_direct_session import call, response
from e2e.test_runtime_product_owner_fixture import eventually
from execution_dependencies.test_resources import composed_database, configured  # noqa: F401
from sqlalchemy import select

from app.application import create_app
from app.execution_dependencies import resources as composition
from app.infrastructure.http import create_stateless_http_client
from app.infrastructure.transactions import transaction
from app.modules.channel.models import ChannelDeliveryRecord
from app.modules.channel.public import ChannelService
from app.modules.credential.public import Secret
from app.modules.group.public import GroupService
from app.modules.session.public import SessionService
from app.modules.trigger.public import TriggerConfig, TriggerService


@pytest.mark.parametrize("kind", ["session", "group"])
async def test_scheduled_reply_reaches_channel_and_unavailable_context_does_not_block_cursor(
        test_database, composed_database, tmp_path, monkeypatch, kind):  # noqa: F811
    outgoing = []
    def peer(request):
        if request.url.host == "slack.com":
            outgoing.append(json.loads(request.content))
            return httpx.Response(200, json={"ok": True, "channel": outgoing[-1]["channel"], "ts": str(1000 + len(outgoing))})
        if request.method == "GET":
            return httpx.Response(404)
        body = json.loads(request.content)
        if any(item["function"]["name"] == "capability_probe" for item in body.get("tools", [])):
            return call("capability_probe", "probe", {"value": "ok"})
        if ("This is unattended scheduled execution" in json.dumps(body["messages"])
                and not any(item.get("tool_call_id") == "message" for item in body["messages"])):
            return call("send_message", "message", {"text": "Scheduled result"})
        return response({"content": "Finished"})
    monkeypatch.setattr(composition, "create_stateless_http_client", lambda **kwargs:
        create_stateless_http_client(transport=httpx.MockTransport(peer), **kwargs))
    app = create_app(configured(tmp_path))
    async with app.router.lifespan_context(app), httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        principal, agent, channel_id = await slack_configuration(app, test_database.sessions, client)
        conversation = "D1" if kind == "session" else "G1"
        group = None
        if kind == "group":
            async with transaction(test_database.sessions) as tx:
                group = await GroupService(tx).create(principal, name="Scheduled reports")
                await GroupService(tx).set_agent(principal, group_id=group.id, agent_id=agent.id, enabled=True)
                await ChannelService(tx).bind_group(principal, channel_id=channel_id, external_group_id=conversation, group_id=group.id)
        body, headers = slack_event("first", "Initialize conversation", conversation=conversation)
        initialized = await client.post(f"/api/channels/{principal.tenant_id}/{channel_id}/events", content=body, headers=headers)
        assert initialized.status_code == 200, initialized.text
        async with transaction(test_database.sessions) as tx:
            if kind == "session":
                destination = (await SessionService(tx).list(principal)).sessions[0].id
                topic = None
            else:
                destination = group.id
                topic = await GroupService(tx).resolve_conversation(principal, group_id=group.id)
                separate = await GroupService(tx).create_conversation(principal, group_id=group.id, title="Not mapped")
            trigger = await TriggerService(tx).create(principal, agent_id=agent.id,
                config=TriggerConfig("report", "interval", "Publish scheduled report", interval_minutes=1440, destination_kind=kind,
                    destination_id=destination, destination_conversation_id=topic))
            credential = await app.state.execution.credentials(tx).create(principal, kind="channel", provider="teams",
                label="Missing authenticated reply context", secret=Secret('{"version":1,"client_secret":"test"}'),
                owner_kind="agent", owner_id=agent.id)
            teams = await ChannelService(tx).configure(principal, agent_id=agent.id, provider="teams",
                external_identity="teams-app", credential_id=credential.id, settings_json='{"tenant_id":"botframework.com"}')
            if kind == "session":
                await ChannelService(tx).bind_conversation(tenant_id=principal.tenant_id, channel_id=teams.id,
                    conversation_id="teams-conversation", membership_id=principal.membership_id, session_id=destination)
            else:
                await ChannelService(tx).bind_group(principal, channel_id=teams.id, external_group_id="teams-group", group_id=destination)
                off_topic = await TriggerService(tx).create(principal, agent_id=agent.id,
                    config=TriggerConfig("other", "interval", "Publish scheduled report", interval_minutes=1440, destination_kind="group",
                        destination_id=destination, destination_conversation_id=separate.id))
        if kind == "group":
            ignored = await app.state.scheduled.fire_manual(principal, trigger_id=off_topic.id, event_id="other-topic")
            await eventually(test_database.sessions, principal.tenant_id, ignored.run_id, "Completed")
        for index in range(2):
            occurrence = await app.state.scheduled.fire_manual(principal, trigger_id=trigger.id, event_id=str(index))
            await eventually(test_database.sessions, principal.tenant_id, occurrence.run_id, "Completed")
            await wait_messages(outgoing, index + 1)
        assert len(outgoing) == 2 and all(item["channel"] == conversation for item in outgoing)
        async with asyncio.timeout(10):
            while True:
                async with transaction(test_database.sessions) as tx:
                    sources = await ChannelService(tx).delivery_sources(kind=kind)
                    if kind == "session":
                        history = (await SessionService(tx).read_history(principal, session_id=destination)).entries
                    else:
                        history = (await GroupService(tx).read_delivery_page(tenant_id=principal.tenant_id,
                            group_id=destination, after_position=0)).entries
                    failed = (await tx.session.scalars(select(ChannelDeliveryRecord).where(
                        ChannelDeliveryRecord.channel_configuration_id == teams.id,
                        ChannelDeliveryRecord.delivery_status == "failed"))).all()
                if all(source.cursor == history[-1].position for source in sources) and len(failed) == 2:
                    break
                await asyncio.sleep(.02)
        assert len([item for item in history if item.kind == "input"]) == 1
        assert all(item.last_error == "teams_authenticated_reply_context_required" for item in failed)
        replies = [item for item in history if item.kind == "reply"]
        assert all((item.origin_input_id if kind == "session" else item.origin_event_id) is None for item in replies)
