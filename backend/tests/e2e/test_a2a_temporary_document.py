"""Model-issued document extraction respects the current A2A temporary-file scope."""

import asyncio
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
from app.execution_dependencies.provisioning import provision_builtin_tools
from app.infrastructure.http import create_stateless_http_client
from app.infrastructure.transactions import transaction
from app.modules.a2a.public import A2AService, A2ATempFileService
from app.modules.agent.public import AgentService
from app.modules.permission.public import PermissionService
from app.modules.run.public import RunService
from app.modules.workspace.public import WorkspaceScope, WorkspaceSubject


@pytest.mark.parametrize("kind", ["pdf", "docx"])
async def test_model_extracts_authorized_temporary_document_without_shared_workspace_copy(
        test_database, composed_database, tmp_path, monkeypatch, kind):  # noqa: F811
    target_id, input_ref, request_id = None, None, None
    name = f"private-document.{kind}"
    marker = f"{kind} document marker"
    extracted, request_seen, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
    denied = []

    async def provider(request):
        nonlocal request_id
        if request.method == "GET":
            return httpx.Response(404)
        body = json.loads(request.content)
        names = {item["function"]["name"] for item in body.get("tools", [])}
        if "capability_probe" in names:
            return call("capability_probe", "probe", {"value":"ok"})
        user = json.dumps([item for item in body["messages"] if item["role"] == "user"])
        results = {item.get("tool_call_id"):json.loads(item["content"]) for item in body["messages"] if item["role"] == "tool"}
        if "ordinary_probe" in user:
            if "read_document" not in names:
                return call("search_tools", "find-document", {"query":"read_document"})
            if "read-private" not in results:
                return call("read_document", "read-private", {"reference":"temporary:" + name})
            assert results["read-private"]["code"] == "access_denied", results["read-private"]
            assert marker not in json.dumps(body)
            denied.append(results["read-private"])
            return response({"content":"Temporary file access was denied"})
        if "initial_input:a2a:" in user:
            if "a2a_file" not in names:
                return call("search_tools", "find-temp", {"query":"a2a_file"})
            if "import" not in results:
                return call("a2a_file", "import", {"action":"import","name":name,"reference":input_ref})
            assert results["import"].get("file"), results["import"]
            if "read_document" not in names:
                return call("search_tools", "find-document", {"query":"read_document"})
            if "extract" not in results:
                assert marker not in json.dumps(body)
                return call("read_document", "extract", {"reference":"temporary:" + name})
            assert marker in results["extract"]["text"], results["extract"]
            assert results["extract"]["format"] == kind and not results["extract"]["truncated"]
            extracted.set()
            await release.wait()
            return response({"content":"Document extracted privately"})
        if "send_message_to_agent" not in names:
            return call("search_tools", "find-agent", {"query":"send_message_to_agent"})
        if "send" not in results:
            return call("send_message_to_agent", "send", {"target_agent_id":str(target_id),"intent":"consult",
                "text":"Import the delegated document as a temporary file and read it.","references":[{"reference":input_ref}]})
        request_id = UUID(results["send"]["request_id"])
        request_seen.set()
        if "wait" not in results:
            return call("send_message_to_agent", "wait", {"action":"wait","request_id":str(request_id)})
        return response({"content":"Delegated document extraction finished"})

    monkeypatch.setattr(composition, "create_stateless_http_client", lambda **kwargs:
        create_stateless_http_client(transport=httpx.MockTransport(provider), **kwargs))
    app = create_app(configured(tmp_path))
    async with app.router.lifespan_context(app):
        principal, source, _ = await configure_agent(app.state.execution, test_database.sessions)
        async with transaction(test_database.sessions) as tx:
            target = await AgentService(tx).create(principal, name="Private document processor", soul="Process delegated documents",
                timezone="UTC", model_id=source.model_id)
            target_id = target.id
            await provision_builtin_tools(tx, principal, agent_id=target_id)
            await PermissionService(tx).set_visibility(principal, agent_id=target_id, visibility="tenant")
        # Prepare an empty directory through Workspace so listing can prove no document was copied.
        shared = WorkspaceSubject("agent", target_id)
        bootstrap = WorkspaceScope(principal.tenant_id, target_id, shared)
        await app.state.execution.workspace.ensure(bootstrap, shared)
        revision = await app.state.execution.workspace.write(bootstrap, shared, "files/.fixture", b"", expected_revision=None)
        await app.state.execution.workspace.delete(bootstrap, shared, "files/.fixture", expected_revision=revision)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            headers = await login(client, app, principal)
            session = (await client.post("/api/sessions", headers=headers, json={"agent_id":str(source.id)})).json()
            uploaded = await client.post(f"/api/sessions/{session['id']}/attachments", headers=headers,
                params={"upload_source_key":"document","filename":f"input.{kind}","media_type":"application/octet-stream"},
                content=document(kind))
            assert uploaded.status_code == 201, uploaded.text
            input_ref = uploaded.json()["reference"]
            started = await client.post(f"/api/sessions/{session['id']}/inputs", headers=headers,
                json={"source_key":"delegate","text":"Delegate document extraction","references":[{"reference":input_ref}]})
            assert started.status_code == 202 and started.json()["error"] is None, started.text
            source_run_id = UUID(started.json()["run"]["run_id"])
            try:
                await asyncio.wait_for(asyncio.gather(extracted.wait(), request_seen.wait()), timeout=15)
                async with transaction(test_database.sessions) as tx:
                    request = await A2AService(tx).get(tenant_id=principal.tenant_id, request_id=request_id)
                    target_snapshot = await RunService(tx).read_snapshot(tenant_id=principal.tenant_id, run_id=request.target_run_id)
                    plans = await A2ATempFileService(tx).files(tenant_id=principal.tenant_id, request_id=request_id)
                assert len(plans) == 1 and await app.state.execution.temp_files.inspect(plans[0].storage_key) is not None
                listing = await app.state.execution.workspace.list(target_snapshot.workspace,
                    WorkspaceSubject("agent", target_id), "files", limit=100)
                assert not listing.entries
                ordinary = (await client.post("/api/sessions", headers=headers, json={"agent_id":str(target_id)})).json()
                probe = await client.post(f"/api/sessions/{ordinary['id']}/inputs", headers=headers,
                    json={"source_key":"ordinary","text":"ordinary_probe"})
                assert probe.status_code == 202 and probe.json()["error"] is None, probe.text
                await eventually(test_database.sessions, principal.tenant_id, UUID(probe.json()["run"]["run_id"]), "Completed")
                assert denied
            finally:
                release.set()
            await eventually(test_database.sessions, principal.tenant_id, source_run_id, "Completed")
            listing = await app.state.execution.workspace.list(target_snapshot.workspace, shared, "files", limit=100)
            assert not listing.entries
