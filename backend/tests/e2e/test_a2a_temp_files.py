"""Actual Main/Child Tools process a delegated file and save the frozen return to A."""

import json
from uuid import UUID, uuid4

import httpx
import pytest
from e2e.test_attachments import login
from e2e.test_direct_session import call, response
from e2e.test_runtime_product_owner_fixture import configure_agent, eventually
from execution_dependencies.test_resources import composed_database, configured  # noqa: F401

from app.application import create_app
from app.execution_dependencies import resources as composition
from app.execution_dependencies.provisioning import provision_builtin_tools
from app.infrastructure.errors import NotFound
from app.infrastructure.http import create_stateless_http_client
from app.infrastructure.transactions import transaction
from app.modules.a2a.public import A2AService, A2ATempFileService
from app.modules.agent.public import AgentService
from app.modules.permission.public import PermissionService
from app.modules.run.public import RunService
from app.modules.workspace.public import WorkspaceSubject


@pytest.mark.parametrize("child_work", [False, True])
async def test_a2a_temporary_return_is_saved_to_original_users_workspace_without_b_shared_files(
        test_database, composed_database, tmp_path, monkeypatch, child_work):  # noqa: F811
    target_id, input_ref, request_id = None, None, None
    generated_name = "chosen-by-b-" + uuid4().hex + ".txt"
    saves = []
    async def provider(request):
        nonlocal request_id
        if request.method == "GET":
            return httpx.Response(404)
        body = json.loads(request.content)
        names = {item["function"]["name"] for item in body.get("tools", [])}
        if "capability_probe" in names:
            return call("capability_probe", "probe", {"value": "ok"})
        messages = body["messages"]
        user = json.dumps([item for item in messages if item["role"] == "user"])
        results = {item.get("tool_call_id"): json.loads(item["content"]) for item in messages if item["role"] == "tool"}
        target = "initial_input:a2a:" in user
        child = "initial_input:task:" in user
        if target or child:
            if "a2a_file" not in names:
                return call("search_tools", "find-temp", {"query": "a2a_file"})
            if target and "deny-shared" not in results:
                return call("write_file", "deny-shared", {"workspace": "agent", "path": "files/forbidden.txt", "content": "private", "expected_revision": None})
            if target:
                assert "denied" in json.dumps(results["deny-shared"]).lower() or "shared" in json.dumps(results["deny-shared"]).lower()
                if "import" not in results:
                    return call("a2a_file", "import", {"action": "import", "name": "input.txt", "reference": input_ref})
                assert results["import"].get("file"), results["import"]
                if child_work:
                    if "delegate" not in results:
                        return call("task", "delegate", {"action": "delegate", "work": "CHILD_TEMP read input.txt, choose a filename and return the processed file"})
                    if "CHILD_DONE" not in user:
                        return call("wait_for_tasks", "wait-child", {})
                    return response({"content": "TARGET_DONE"})
            if "read" not in results:
                return call("a2a_file", "read", {"action": "read", "name": "input.txt"})
            assert "private-input-marker" in results["read"]["text"]
            if "write" not in results:
                return call("a2a_file", "write", {"action": "write", "name": generated_name, "text": "processed-private-result"})
            revision = results["write"]["file"]["revision"]
            if "return" not in results:
                return call("a2a_file", "return", {"action": "return", "name": generated_name, "expected_revision": revision})
            if "deny-return-rewrite" not in results:
                return call("a2a_file", "deny-return-rewrite", {"action": "write", "name": generated_name, "text": "replacement", "expected_revision": revision})
            assert "cannot change" in results["deny-return-rewrite"]["message"]
            return response({"content": "CHILD_DONE" if child else "TARGET_DONE"})
        if "send_message_to_agent" not in names:
            return call("search_tools", "find-agent", {"query": "send_message_to_agent"})
        if "send" not in results:
            return call("send_message_to_agent", "send", {"target_agent_id": str(target_id), "intent": "task_delegate",
                "text": "Process my file and return a file using your chosen name", "references": [{"reference": input_ref}]})
        request_id = UUID(results["send"]["request_id"])
        if "wait" not in results:
            return call("send_message_to_agent", "wait", {"action": "wait", "request_id": str(request_id)})
        assert "TARGET_DONE" in user
        assert generated_name in user and "Returned files" in user
        if "inspect-files" not in results:
            return call("send_message_to_agent", "inspect-files", {"action": "inspect", "request_id": str(request_id)})
        discovered = results["inspect-files"]["files"]
        assert len(discovered) == 1 and set(discovered[0]) == {"name", "media_type", "byte_size", "sha256", "revision"}
        discovered_name = discovered[0]["name"]
        assert discovered_name == generated_name
        if "a2a_file" not in names:
            return call("search_tools", "find-result", {"query": "a2a_file"})
        if "save" not in results:
            return call("a2a_file", "save", {"action": "save", "request_id": str(request_id), "name": discovered_name,
                "path": "files/result.txt", "expected_revision": None})
        assert results["save"].get("saved"), results["save"]
        if "save-again" not in results:
            return call("a2a_file", "save-again", {"action": "save", "request_id": str(request_id), "name": discovered_name,
                "path": "files/result.txt", "expected_revision": None})
        assert results["save-again"]["revision"] == results["save"]["revision"]
        saves.append(results["save"])
        return response({"content": "Saved returned file"})
    monkeypatch.setattr(composition, "create_stateless_http_client", lambda **kwargs:
        create_stateless_http_client(transport=httpx.MockTransport(provider), **kwargs))
    app = create_app(configured(tmp_path))
    async with app.router.lifespan_context(app):
        principal, source, _ = await configure_agent(app.state.execution, test_database.sessions)
        async with transaction(test_database.sessions) as tx:
            target = await AgentService(tx).create(principal, name="Processor", soul="Process files privately", timezone="UTC", model_id=source.model_id)
            target_id = target.id
            await provision_builtin_tools(tx, principal, agent_id=target.id)
            await PermissionService(tx).set_visibility(principal, agent_id=target.id, visibility="tenant")
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            headers = await login(client, app, principal)
            session = (await client.post("/api/sessions", headers=headers, json={"agent_id": str(source.id)})).json()
            uploaded = await client.post(f"/api/sessions/{session['id']}/attachments", headers=headers,
                params={"upload_source_key": "input", "filename": "input.txt", "media_type": "text/plain"}, content=b"private-input-marker")
            assert uploaded.status_code == 201, uploaded.text
            input_ref = uploaded.json()["reference"]
            accepted = await client.post(f"/api/sessions/{session['id']}/inputs", headers=headers,
                json={"source_key": "work", "text": "Use the other Agent to process this file", "references": [{"reference": input_ref}]})
            assert accepted.status_code == 202 and accepted.json()["error"] is None, accepted.text
            run_id = UUID(accepted.json()["run"]["run_id"])
            await eventually(test_database.sessions, principal.tenant_id, run_id, "Completed")
        async with transaction(test_database.sessions) as tx:
            snap = await RunService(tx).read_snapshot(tenant_id=principal.tenant_id, run_id=run_id)
            request = await A2AService(tx).get(tenant_id=principal.tenant_id, request_id=request_id)
            target_snap = await RunService(tx).read_snapshot(tenant_id=principal.tenant_id, run_id=request.target_run_id)
            plans = await A2ATempFileService(tx).files(tenant_id=principal.tenant_id, request_id=request_id)
        assert saves and snap.workspace.output == WorkspaceSubject("membership", principal.membership_id)
        assert (await app.state.execution.workspace.read(snap.workspace, snap.workspace.output, "files/result.txt")).content == b"processed-private-result"
        with pytest.raises(NotFound):
            await app.state.execution.workspace.read(target_snap.workspace, target_snap.workspace.output, "files/forbidden.txt")
        await app.state.products.a2a_files.cleanup_once()
        for plan in plans:
            assert await app.state.execution.temp_files.inspect(plan.storage_key) is None


async def test_nested_a2a_model_import_preserves_binary_across_b_and_c(test_database, composed_database, tmp_path, monkeypatch):  # noqa: F811
    middle_id, leaf_id, input_ref = None, None, None
    final_bytes = b"\x00\xff\x80binary-through-two-independent-agents"
    async def provider(request):
        if request.method == "GET":
            return httpx.Response(404)
        body = json.loads(request.content)
        names = {item["function"]["name"] for item in body.get("tools", [])}
        if "capability_probe" in names:
            return call("capability_probe", "probe", {"value": "ok"})
        system = json.dumps([item for item in body["messages"] if item["role"] == "system"])
        results = {item.get("tool_call_id"): json.loads(item["content"]) for item in body["messages"] if item["role"] == "tool"}
        leaf, middle = "LeafBinary" in system, "MiddleBinary" in system
        if leaf:
            if "a2a_file" not in names:
                return call("search_tools", "find-temp", {"query": "a2a_file"})
            if "import" not in results:
                return call("a2a_file", "import", {"action": "import", "name": "source.bin", "reference": input_ref})
            assert "file" in results["import"], results["import"]
            if "return" not in results:
                return call("a2a_file", "return", {"action": "return", "name": "source.bin", "expected_revision": results["import"]["file"]["revision"]})
            return response({"content": "LEAF_BINARY_RETURNED"})
        if "send_message_to_agent" not in names:
            return call("search_tools", "find-agent", {"query": "send_message_to_agent"})
        if "send" not in results:
            return call("send_message_to_agent", "send", {"target_agent_id": str(leaf_id if middle else middle_id),
                "intent": "consult", "text": "Return the binary file", "references": [{"reference": input_ref}]})
        request_id = results["send"]["request_id"]
        if "wait" not in results:
            return call("send_message_to_agent", "wait", {"action": "wait", "request_id": request_id})
        if "a2a_file" not in names:
            return call("search_tools", "find-temp", {"query": "a2a_file"})
        if middle:
            if "copy" not in results:
                return call("a2a_file", "copy", {"action": "import", "request_id": request_id,
                    "source_name": "source.bin", "name": "result.bin", "expected_revision": None})
            assert "file" in results["copy"], results["copy"]
            if "return" not in results:
                return call("a2a_file", "return", {"action": "return", "name": "result.bin", "expected_revision": results["copy"]["file"]["revision"]})
            return response({"content": "MIDDLE_BINARY_RETURNED"})
        if "save" not in results:
            return call("a2a_file", "save", {"action": "save", "request_id": request_id, "name": "result.bin",
                "path": "files/nested.bin", "expected_revision": None})
        assert results["save"].get("saved"), results["save"]
        return response({"content": "Nested binary saved"})
    monkeypatch.setattr(composition, "create_stateless_http_client", lambda **kwargs:
        create_stateless_http_client(transport=httpx.MockTransport(provider), **kwargs))
    app = create_app(configured(tmp_path))
    async with app.router.lifespan_context(app):
        principal, source, _ = await configure_agent(app.state.execution, test_database.sessions)
        async with transaction(test_database.sessions) as tx:
            agents = []
            for name in ("MiddleBinary", "LeafBinary"):
                agent = await AgentService(tx).create(principal, name=name, soul=name, timezone="UTC", model_id=source.model_id)
                await provision_builtin_tools(tx, principal, agent_id=agent.id)
                await PermissionService(tx).set_visibility(principal, agent_id=agent.id, visibility="tenant")
                agents.append(agent.id)
            middle_id, leaf_id = agents
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            headers = await login(client, app, principal)
            session = (await client.post("/api/sessions", headers=headers, json={"agent_id": str(source.id)})).json()
            uploaded = await client.post(f"/api/sessions/{session['id']}/attachments", headers=headers,
                params={"upload_source_key": "binary", "filename": "input.bin", "media_type": "application/octet-stream"}, content=final_bytes)
            assert uploaded.status_code == 201, uploaded.text
            input_ref = uploaded.json()["reference"]
            accepted = await client.post(f"/api/sessions/{session['id']}/inputs", headers=headers,
                json={"source_key": "binary", "text": "Pass this binary through B and C", "references": [{"reference": input_ref}]})
            assert accepted.status_code == 202 and accepted.json()["error"] is None, accepted.text
            run_id = UUID(accepted.json()["run"]["run_id"])
            await eventually(test_database.sessions, principal.tenant_id, run_id, "Completed")
        async with transaction(test_database.sessions) as tx:
            snapshot = await RunService(tx).read_snapshot(tenant_id=principal.tenant_id, run_id=run_id)
        assert (await app.state.execution.workspace.read(snapshot.workspace, snapshot.workspace.output, "files/nested.bin")).content == final_bytes
