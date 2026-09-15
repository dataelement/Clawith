"""Actual HTTP input, Model Tool discovery and isolated document extraction."""

import json
from uuid import UUID

import httpx
import pytest
from e2e.test_attachments import login
from e2e.test_direct_session import call, response
from e2e.test_runtime_product_owner_fixture import configure_agent, eventually
from execution_dependencies.test_document_tools import document
from execution_dependencies.test_resources import composed_database, configured  # noqa: F401

from app.application import create_app
from app.execution_dependencies import resources as composition
from app.infrastructure.http import create_stateless_http_client


@pytest.mark.parametrize("kind", ["pdf", "docx", "xlsx", "pptx"])
@pytest.mark.parametrize("source", ["attachment", "workspace"])
async def test_actual_model_reads_document_from_authorized_product_input(
        test_database, composed_database, tmp_path, monkeypatch, kind, source):  # noqa: F811
    observed = []
    reference = None
    path = f"files/input.{kind}"
    marker = f"{kind} document marker"

    def provider(request):
        if request.method == "GET":
            return httpx.Response(404)
        body = json.loads(request.content)
        names = {tool["function"]["name"] for tool in body.get("tools", [])}
        if "capability_probe" in names:
            return call("capability_probe", "probe", {"value": "ok"})
        observed.append(body)
        results = {item.get("tool_call_id"): item for item in body["messages"] if item["role"] == "tool"}
        if source == "workspace" and "save" not in results:
            if "save_attachment" not in names:
                return call("search_tools", "find-save", {"query": "save_attachment"})
            return call("save_attachment", "save", {"reference": reference, "path": path, "expected_revision": None})
        if "read_document" not in names:
            return call("search_tools", "find-document", {"query": "read_document"})
        if "extract" not in results:
            return call("read_document", "extract", {"reference": reference if source == "attachment" else path})
        assert marker in results["extract"]["content"]
        if "answer" not in results:
            return call("send_message", "answer", {"text": "I read the supplied document."})
        return response({"content": "Finished"})

    monkeypatch.setattr(composition, "create_stateless_http_client", lambda **kwargs:
        create_stateless_http_client(transport=httpx.MockTransport(provider), **kwargs))
    app = create_app(configured(tmp_path))
    async with app.router.lifespan_context(app):
        principal, agent, _ = await configure_agent(app.state.execution, test_database.sessions)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            headers = await login(client, app, principal)
            made = await client.post("/api/sessions", headers=headers, json={"agent_id": str(agent.id)})
            assert made.status_code == 201
            session_id = made.json()["id"]
            uploaded = await client.post(f"/api/sessions/{session_id}/attachments", headers=headers,
                params={"upload_source_key": "document", "filename": f"input.{kind}", "media_type": "application/octet-stream"},
                content=document(kind))
            assert uploaded.status_code == 201, uploaded.text
            reference = uploaded.json()["reference"]
            submitted = await client.post(f"/api/sessions/{session_id}/inputs", headers=headers,
                json={"source_key": "read", "text": "Read the document and report its contents.",
                    "references": [{"reference": reference}]})
            assert submitted.status_code == 202 and submitted.json()["error"] is None, submitted.text
            await eventually(test_database.sessions, principal.tenant_id, UUID(submitted.json()["run"]["run_id"]), "Completed")
            assert marker not in json.dumps(observed[0])
            assert any(marker in json.dumps(body) for body in observed[1:])
