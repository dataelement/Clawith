import json

import httpx
from e2e.test_runtime_product_owner_fixture import configure_agent, eventually
from execution_dependencies.test_resources import composed_database, configured  # noqa: F401

from app.application import create_app
from app.execution_dependencies import resources as composition
from app.infrastructure.http import create_stateless_http_client


def response(message, reason="stop"):
    return httpx.Response(200, json={"choices": [{"message": message, "finish_reason": reason}]})


def call(name, identity, arguments):
    return response({"tool_calls": [{"id": identity, "function": {"name": name,
        "arguments": json.dumps(arguments)}}]}, "tool_calls")


async def test_session_messages_wait_reply_and_final_use_real_product_entry(
        test_database, composed_database, tmp_path, monkeypatch):  # noqa: F811
    observed = []
    def provider(request):
        if request.method == "GET":
            return httpx.Response(404)
        body = json.loads(request.content)
        if any(tool["function"]["name"] == "capability_probe" for tool in body.get("tools", [])):
            return call("capability_probe", "probe", {"value": "ok"})
        observed.append(body)
        calls = [item for message in body["messages"] for item in message.get("tool_calls", [])]
        names = [item["function"]["name"] for item in calls]
        if "Independent request B" in json.dumps(body["messages"]):
            return call("send_message", "b-answer", {"text": "B finished."}) if not names else response({"content": "B result"})
        if not names:
            return call("send_message", "ack", {"text": "I have started."})
        if "need_input" not in names:
            return call("need_input", "question", {"question": "Which format?"})
        if not any(item["id"] == "answer" for item in calls):
            return call("send_message", "answer", {"text": "The Markdown answer is ready."})
        return response({"content": "Internal execution result; not a second reply."})

    monkeypatch.setattr(composition, "create_stateless_http_client", lambda **kwargs:
        create_stateless_http_client(transport=httpx.MockTransport(provider), **kwargs))
    app = create_app(configured(tmp_path))
    async with app.router.lifespan_context(app):
        principal, agent, _ = await configure_agent(app.state.execution, test_database.sessions)
        await app.state.auth.provision_trusted_verifier(account_id=principal.account_id,
            login_name="person", password="password")
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            login = await client.post("/api/auth/login", json={"login_name": "person", "password": "password",
                "tenant_id": str(principal.tenant_id)})
            headers = {"Authorization": "Bearer " + login.json()["token"]}
            made = await client.post("/api/sessions", json={"agent_id": str(agent.id)}, headers=headers)
            assert made.status_code == 201, made.text
            session_id = made.json()["id"]
            first = {"source_key": "request-1", "text": "Prepare a report."}
            accepted = await client.post(f"/api/sessions/{session_id}/inputs", json=first, headers=headers)
            assert accepted.status_code == 202, accepted.text
            assert accepted.json()["error"] is None, accepted.text
            from uuid import UUID
            run_id = UUID(accepted.json()["run"]["run_id"])
            waiting = await eventually(test_database.sessions, principal.tenant_id, run_id, "Waiting")
            duplicate = await client.post(f"/api/sessions/{session_id}/inputs", json=first, headers=headers)
            assert duplicate.json()["run"]["run_id"] == str(run_id)
            before = (await client.get(f"/api/sessions/{session_id}/history", headers=headers)).json()
            assert [entry["content"]["text"] for entry in before["entries"]] == [
                "Prepare a report.", "I have started.", "Which format?"]
            question = before["entries"][-1]
            assert question["waiting_reference"] == waiting.waiting_reference
            independent = await client.post(f"/api/sessions/{session_id}/inputs", headers=headers,
                json={"source_key": "request-b", "text": "Independent request B"})
            assert independent.status_code == 202, independent.text
            run_b = UUID(independent.json()["run"]["run_id"])
            assert run_b != run_id
            await eventually(test_database.sessions, principal.tenant_id, run_b, "Completed")
            replied = await client.post(f"/api/sessions/{session_id}/inputs", headers=headers, json={
                "source_key": "reply-1", "text": "Markdown", "reply_to_run_id": str(run_id),
                "waiting_reference": waiting.waiting_reference})
            assert replied.status_code == 202, replied.text
            await eventually(test_database.sessions, principal.tenant_id, run_id, "Completed")
            after = (await client.get(f"/api/sessions/{session_id}/history", headers=headers)).json()
            assert [entry["content"]["text"] for entry in after["entries"]] == [
                "Prepare a report.", "I have started.", "Which format?", "Independent request B", "B finished.",
                "Markdown", "The Markdown answer is ready."]
            work = (await client.get(f"/api/sessions/{session_id}/work", headers=headers)).json()["work"]
            assert len(work) == 2 and all(item["result"]["status"] == "Completed" for item in work)
            assert "Internal execution result" not in json.dumps(after)
            assert (await client.get(f"/api/sessions/{session_id}/history")).status_code == 401
            assert len(observed) == 6
