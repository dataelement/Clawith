"""Native Group HTTP owns topics and read state; remote Model alone is controlled."""

import json
from uuid import UUID

import httpx
from e2e.test_direct_session import call, response
from e2e.test_runtime_product_owner_fixture import configure_agent, eventually
from execution_dependencies.test_resources import composed_database, configured  # noqa: F401

from app.application import create_app
from app.execution_dependencies import resources as composition
from app.infrastructure.http import create_stateless_http_client
from app.infrastructure.transactions import transaction
from app.modules.run.public import RunService


async def test_real_group_api_roster_topics_fixed_context_and_read_work(
        test_database, composed_database, tmp_path, monkeypatch):  # noqa: F811
    observed = []

    def provider(request):
        if request.method == "GET":
            return httpx.Response(404)
        body = json.loads(request.content)
        if any(tool["function"]["name"] == "capability_probe" for tool in body.get("tools", [])):
            return call("capability_probe", "probe", {"value": "ok"})
        observed.append(body)
        return response({"content": "result"}) if any(message["role"] == "tool" for message in body["messages"]) else call(
            "send_message", "reply", {"text": "topic answer"})

    monkeypatch.setattr(composition, "create_stateless_http_client", lambda **kwargs:
        create_stateless_http_client(transport=httpx.MockTransport(provider), **kwargs))
    app = create_app(configured(tmp_path))
    async with app.router.lifespan_context(app):
        principal, agent, _ = await configure_agent(app.state.execution, test_database.sessions)
        await app.state.auth.provision_trusted_verifier(account_id=principal.account_id,
            login_name="group-human", password="password")
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            login = await client.post("/api/auth/login", json={"login_name": "group-human", "password": "password",
                "tenant_id": str(principal.tenant_id)})
            headers = {"Authorization": "Bearer " + login.json()["token"]}
            made = await client.post("/api/groups", headers=headers, json={"name": "Team"})
            assert made.status_code == 201, made.text
            group_id = made.json()["id"]
            base = f"/api/groups/{group_id}"
            rejected = await client.post(base + "/inputs", headers=headers,
                json={"source_key": "uninvited", "text": "x", "agent_ids": [str(agent.id)]})
            assert rejected.status_code == 403, rejected.text
            invited = await client.post(base + "/agents", headers=headers, json={"agent_id": str(agent.id)})
            assert invited.status_code == 200, invited.text
            assert (await client.get(base + "/members?kind=agent", headers=headers)).json()["members"][0]["id"] == str(agent.id)
            assert (await client.get(base + "/member-candidates?kind=human", headers=headers)).status_code == 200
            topic = await client.post(base + "/conversations", headers=headers, json={"title": "Research"})
            assert topic.status_code == 201, topic.text
            topic_id = topic.json()["id"]
            for key, text, conversation in (("general", "DO_NOT_READ_OTHER_TOPIC", None), ("prior", "EARLIER_RESEARCH", topic_id)):
                body = {"source_key": key, "text": text}
                if conversation:
                    body["conversation_id"] = conversation
                result = await client.post(base + "/inputs", headers=headers, json=body)
                assert result.status_code == 202, result.text
            accepted = await client.post(base + "/inputs", headers=headers, json={"source_key": "work", "text": "ANSWER_RESEARCH",
                "conversation_id": topic_id, "agent_ids": [str(agent.id)]})
            assert accepted.status_code == 202, accepted.text
            assert not accepted.json()["errors"], accepted.text
            run_id = UUID(accepted.json()["runs"][0]["run_id"])
            await eventually(test_database.sessions, principal.tenant_id, run_id, "Completed")
            first = json.dumps(observed[0]["messages"])
            assert "EARLIER_RESEARCH" in first and "DO_NOT_READ_OTHER_TOPIC" not in first
            work = await client.get(base + "/work", headers=headers, params={"conversation_id": topic_id})
            assert work.json()["work"][0]["result"]["status"] == "Completed"
            topics = (await client.get(base + "/conversations", headers=headers)).json()["conversations"]
            state = next(item for item in topics if item["id"] == topic_id)
            assert state["unread_count"] == 1
            read = await client.post(base + f"/conversations/{topic_id}/read", headers=headers,
                json={"through_position": state["head_position"]})
            assert read.status_code == 200, read.text
            history = (await client.get(base + "/history", headers=headers, params={"conversation_id": topic_id})).json()["events"]
            assert len(history) == 3
            deleted = await client.delete(base + f"/conversations/{topic_id}", headers=headers)
            assert deleted.status_code == 200, deleted.text
            async with transaction(test_database.sessions) as tx:
                assert (await RunService(tx).get(tenant_id=principal.tenant_id, run_id=run_id)).status == "Completed"


async def test_group_wait_reply_binds_uploaded_attachment_in_resume_transaction(
        test_database, composed_database, tmp_path, monkeypatch):  # noqa: F811
    from e2e.test_attachments import login

    from app.modules.group.public import GroupAttachmentService

    def provider(request):
        if request.method == "GET":
            return httpx.Response(404)
        body = json.loads(request.content)
        if any(tool["function"]["name"] == "capability_probe" for tool in body.get("tools", [])):
            return call("capability_probe", "probe", {"value": "ok"})
        return response({"content": "done"}) if "HERE_IS_FILE" in json.dumps(body["messages"]) else call(
            "need_input", "question", {"question": "Please provide the file"})

    monkeypatch.setattr(composition, "create_stateless_http_client", lambda **kwargs:
        create_stateless_http_client(transport=httpx.MockTransport(provider), **kwargs))
    app = create_app(configured(tmp_path))
    async with app.router.lifespan_context(app):
        principal, agent, _ = await configure_agent(app.state.execution, test_database.sessions)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            headers = await login(client, app, principal, "group-file-human")
            group = await client.post("/api/groups", headers=headers, json={"name": "Files"})
            group_id = group.json()["id"]
            base = f"/api/groups/{group_id}"
            assert (await client.post(base + "/agents", headers=headers, json={"agent_id": str(agent.id)})).status_code == 200
            accepted = await client.post(base + "/inputs", headers=headers,
                json={"source_key": "ask", "text": "Need file", "agent_ids": [str(agent.id)]})
            run_id = UUID(accepted.json()["runs"][0]["run_id"])
            waiting = await eventually(test_database.sessions, principal.tenant_id, run_id, "Waiting")
            upload = await client.post(base + "/attachments", headers=headers,
                params={"upload_source_key": "file", "filename": "reply.txt", "media_type": "text/plain"}, content=b"reply bytes")
            assert upload.status_code == 201, upload.text
            data = upload.json()
            answered = await client.post(base + "/inputs", headers=headers, json={"source_key": "answer", "text": "HERE_IS_FILE",
                "reply_to_run_id": str(run_id), "waiting_reference": waiting.waiting_reference,
                "references": [{"reference": data["reference"]}]})
            assert answered.status_code == 202, answered.text
            await eventually(test_database.sessions, principal.tenant_id, run_id, "Completed")
            async with transaction(test_database.sessions) as tx:
                blob = await GroupAttachmentService(tx).get_upload(principal, group_id=UUID(group_id), attachment_id=UUID(data["id"]))
                assert blob.view.origin_event_id == UUID(answered.json()["accepted"]["event"]["id"])
