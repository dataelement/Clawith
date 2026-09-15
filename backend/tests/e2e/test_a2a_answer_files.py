"""The actual A2A answer Tool can delegate another source-authorized attachment."""

import json
from uuid import UUID

import httpx
from e2e.test_attachments import login
from e2e.test_direct_session import call, response
from e2e.test_runtime_product_owner_fixture import configure_agent, eventually
from execution_dependencies.test_resources import composed_database, configured  # noqa: F401

from app.application import create_app
from app.execution_dependencies import resources as composition
from app.execution_dependencies.provisioning import provision_builtin_tools
from app.infrastructure.http import create_stateless_http_client
from app.infrastructure.transactions import transaction
from app.modules.a2a.public import A2AService
from app.modules.agent.public import AgentService
from app.modules.permission.public import PermissionService
from app.modules.run.public import RelatedInputPayload, RunService, ToolResultPayload


async def test_second_attachment_requires_explicit_answer_then_reaches_target_model(
        test_database, composed_database, tmp_path, monkeypatch):  # noqa: F811
    target_id, request_id = None, None
    references = []

    async def provider(request):
        nonlocal request_id
        if request.method == "GET":
            return httpx.Response(404)
        body = json.loads(request.content)
        names = {item["function"]["name"] for item in body.get("tools", [])}
        if "capability_probe" in names:
            return call("capability_probe", "probe", {"value": "ok"})
        messages = body["messages"]
        results = {message.get("tool_call_id"): message["content"] for message in messages if message["role"] == "tool"}
        is_target = any(message["role"] == "user" and "initial_input:a2a:" in json.dumps(message["content"]) for message in messages)
        if is_target:
            if "read_attachment" not in names:
                return call("search_tools", "reader", {"query": "read_attachment"})
            if "first" not in results:
                return call("read_attachment", "first", {"reference": references[0]})
            if "denied" not in results:
                return call("read_attachment", "denied", {"reference": references[1]})
            assert "not explicitly delegated" in results["denied"]
            if "question" not in results:
                return call("need_input", "question", {"question": "Please supply the second file"})
            if "second" not in results:
                return call("read_attachment", "second", {"reference": references[1]})
            assert "SECOND_CONTENT_MARKER" in json.dumps(messages)
            return response({"content": "FILE_DONE"})
        if "send_message_to_agent" not in names:
            return call("search_tools", "a2a", {"query": "send_message_to_agent"})
        if "send" not in results:
            return call("send_message_to_agent", "send", {"action": "send", "target_agent_id": str(target_id),
                "intent": "consult", "text": "Start with the first file", "references": [{"reference": references[0]}]})
        request_id = UUID(json.loads(results["send"])["request_id"])
        if "wait-question" not in results:
            return call("send_message_to_agent", "wait-question", {"action": "wait", "request_id": str(request_id)})
        if "answer" not in results:
            async with transaction(test_database.sessions) as tx:
                current = await A2AService(tx).get(tenant_id=principal.tenant_id, request_id=request_id)
            assert current.result["kind"] == "needs_input"
            return call("send_message_to_agent", "answer", {"action": "answer", "request_id": str(request_id),
                "waiting_reference": current.result["waiting_reference"], "text": "Read this additional file",
                "references": [{"reference": references[1]}]})
        if "wait-result" not in results:
            return call("send_message_to_agent", "wait-result", {"action": "wait", "request_id": str(request_id)})
        assert "FILE_DONE" in json.dumps(messages)
        return response({"content": "Both files processed"})

    monkeypatch.setattr(composition, "create_stateless_http_client", lambda **kwargs:
        create_stateless_http_client(transport=httpx.MockTransport(provider), **kwargs))
    app = create_app(configured(tmp_path))
    async with app.router.lifespan_context(app):
        principal, agent, _ = await configure_agent(app.state.execution, test_database.sessions)
        async with transaction(test_database.sessions) as tx:
            target = await AgentService(tx).create(principal, name="Reader", soul="Read explicitly delegated files",
                timezone="UTC", model_id=agent.model_id)
            target_id = target.id
            await provision_builtin_tools(tx, principal, agent_id=target_id)
            await PermissionService(tx).set_visibility(principal, agent_id=target_id, visibility="tenant")
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            headers = await login(client, app, principal)
            created = await client.post("/api/sessions", headers=headers, json={"agent_id": str(agent.id)})
            session_id = created.json()["id"]
            for index, content in enumerate((b"FIRST_CONTENT_MARKER", b"SECOND_CONTENT_MARKER")):
                uploaded = await client.post(f"/api/sessions/{session_id}/attachments", headers=headers,
                    params={"upload_source_key": str(index), "filename": f"{index}.txt", "media_type": "text/plain"}, content=content)
                assert uploaded.status_code == 201, uploaded.text
                references.append(uploaded.json()["reference"])
            accepted = await client.post(f"/api/sessions/{session_id}/inputs", headers=headers, json={"source_key": "files",
                "text": "Delegate the first file, answer with the second if asked", "references": [{"reference": item} for item in references]})
            assert accepted.status_code == 202, accepted.text
            run_id = UUID(accepted.json()["run"]["run_id"])
            await eventually(test_database.sessions, principal.tenant_id, run_id, "Completed")
        async with transaction(test_database.sessions) as tx:
            request = await A2AService(tx).get(tenant_id=principal.tenant_id, request_id=request_id)
            assert [item.reference for item in request.input.references] == references[:1]
            history = await RunService(tx).read_history(tenant_id=principal.tenant_id, run_id=request.target_run_id)
            denied = [item.payload.result for item in history.entries if isinstance(item.payload, ToolResultPayload)
                and item.payload.result.call_id == "denied"]
            assert len(denied) == 1 and denied[0].status == "error"
            answers = [item for item in history.entries if isinstance(item.payload, RelatedInputPayload)
                and item.source is not None and item.source.kind == "a2a_answer"]
            assert len(answers) == 1 and answers[0].source.owner_id == request_id
            assert answers[0].payload.input.references[0].reference == references[1]
