"""Committed explicit answers notify only subscriptions on the receiving Agent."""

import asyncio
import json
from uuid import UUID

import httpx
import pytest
from e2e.test_attachments import login
from e2e.test_channel_inputs import slack_configuration, slack_event, wait_messages
from e2e.test_direct_session import call, response
from e2e.test_runtime_product_owner_fixture import eventually
from execution_dependencies.test_resources import composed_database, configured  # noqa: F401

from app.application import create_app
from app.execution_dependencies import resources as composition
from app.infrastructure.http import create_stateless_http_client
from app.infrastructure.transactions import transaction
from app.modules.channel.public import ChannelService
from app.modules.group.public import GroupService
from app.modules.run.public import RunService
from app.modules.session.public import SessionService
from app.modules.trigger.public import TriggerConfig, TriggerService


@pytest.mark.parametrize("entry", ["channel_session", "channel_group", "group_http"])
async def test_waiting_answers_dispatch_subscription_once_with_private_scope(
        test_database, composed_database, tmp_path, monkeypatch, entry):  # noqa: F811
    outgoing = []
    def peer(request):
        if request.url.host == "slack.com":
            outgoing.append(json.loads(request.content))
            return httpx.Response(200, json={"ok": True, "channel": outgoing[-1]["channel"], "ts": "1001"})
        if request.method == "GET":
            return httpx.Response(404)
        body = json.loads(request.content)
        if any(item["function"]["name"] == "capability_probe" for item in body.get("tools", [])):
            return call("capability_probe", "probe", {"value": "ok"})
        if "Observe accepted answer" in json.dumps(body):
            return response({"content": "Observed"})
        if not any(item.get("tool_call_id") == "question" for item in body["messages"]):
            return call("need_input", "question", {"question": "Which format?"})
        return response({"content": "Finished"})
    monkeypatch.setattr(composition, "create_stateless_http_client", lambda **kwargs:
        create_stateless_http_client(transport=httpx.MockTransport(peer), **kwargs))
    app = create_app(configured(tmp_path))
    async with app.router.lifespan_context(app), httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        principal, agent, channel_id = await slack_configuration(app, test_database.sessions, client)
        group = None
        headers = await login(client, app, principal)
        if entry != "channel_session":
            async with transaction(test_database.sessions) as tx:
                group = await GroupService(tx).create(principal, name="Answers")
                await GroupService(tx).set_agent(principal, group_id=group.id, agent_id=agent.id, enabled=True)
                await ChannelService(tx).bind_group(principal, channel_id=channel_id, external_group_id="G1", group_id=group.id)
        channel_path = f"/api/channels/{principal.tenant_id}/{channel_id}/events"
        if entry == "group_http":
            sent = await client.post(f"/api/groups/{group.id}/inputs", headers=headers,
                json={"source_key": "question", "text": "Prepare report", "agent_ids": [str(agent.id)]})
            assert sent.status_code == 202, sent.text
            run_id = UUID(sent.json()["runs"][0]["run_id"])
            await eventually(test_database.sessions, principal.tenant_id, run_id, "Waiting")
        else:
            body, signed = slack_event("question", "Prepare report", conversation="D1" if group is None else "G1")
            sent = await client.post(channel_path, content=body, headers=signed)
            assert sent.status_code == 200, sent.text
            await wait_messages(outgoing, 1)
        async with transaction(test_database.sessions) as tx:
            if group is None:
                session = (await SessionService(tx).list(principal)).sessions[0]
                question = (await SessionService(tx).read_history(principal, session_id=session.id)).entries[-1]
            else:
                question = (await GroupService(tx).list_events(principal, group_id=group.id))[-1]
            run_id = question.source_run_id
            trigger = await TriggerService(tx).create(principal, agent_id=agent.id,
                config=TriggerConfig("answers", "on_message", "Observe accepted answer", source_membership_id=principal.membership_id))
        if entry == "group_http":
            answer = {"source_key": "answer", "text": "Markdown", "reply_to_run_id": str(run_id),
                "waiting_reference": question.waiting_reference}
            for _ in range(2):
                result = await client.post(f"/api/groups/{group.id}/inputs", headers=headers, json=answer)
                assert result.status_code == 202, result.text
        else:
            body, signed = slack_event("answer", "Markdown", reply_to="1001", conversation="D1" if group is None else "G1")
            for _ in range(2):
                result = await client.post(channel_path, content=body, headers=signed)
                assert result.status_code == 200, result.text
        await eventually(test_database.sessions, principal.tenant_id, run_id, "Completed")
        async with asyncio.timeout(10):
            while True:
                async with transaction(test_database.sessions) as tx:
                    items = (await TriggerService(tx).history(principal, trigger_id=trigger.id)).items
                if len(items) == 1 and items[0].result is not None:
                    break
                await asyncio.sleep(.02)
        async with transaction(test_database.sessions) as tx:
            snapshot = await RunService(tx).read_snapshot(tenant_id=principal.tenant_id, run_id=items[0].run_id)
            if group is None:
                work = (await SessionService(tx).list_work(principal, session_id=session.id)).work
            else:
                work = await GroupService(tx).list_work(principal, group_id=group.id)
            assert len(work) == 1 and work[0].run_id == run_id
            assert snapshot.agent_id == agent.id
            assert snapshot.workspace.output.kind == ("membership" if group is None else "group")
            assert snapshot.workspace.output.id == (principal.membership_id if group is None else group.id)
            assert not snapshot.workspace.allow_shared_memory_writes
