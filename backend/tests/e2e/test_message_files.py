"""Model messages capture immutable files before product acceptance."""

import json
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import httpx
import pytest
from e2e.test_attachments import login
from e2e.test_direct_session import call, response
from e2e.test_runtime_product_owner_fixture import configure_agent, eventually
from execution_dependencies.test_resources import composed_database, configured  # noqa: F401

from app.application import create_app
from app.execution_dependencies import resources as composition
from app.execution_dependencies.message_tools import WorkspaceMessageFile
from app.infrastructure.errors import NotFound
from app.infrastructure.http import create_stateless_http_client
from app.infrastructure.transactions import transaction
from app.modules.group.public import GroupService
from app.modules.run.public import InputContent, RunService
from app.modules.session.public import SessionAttachmentService, SessionService
from app.modules.workspace.public import WorkspaceScope, WorkspaceSubject


@pytest.mark.parametrize("subject", ["output", "agent"])
@pytest.mark.parametrize("stale", [False, True])
async def test_model_message_captures_exact_workspace_revision_for_later_delivery(
        test_database, composed_database, tmp_path, monkeypatch, subject, stale):  # noqa: F811
    revision = None
    messages = []
    def provider(request):
        if request.method == "GET":
            return httpx.Response(404)
        body = json.loads(request.content)
        names = {item["function"]["name"] for item in body.get("tools", [])}
        if "capability_probe" in names:
            return call("capability_probe", "probe", {"value": "ok"})
        results = [item for item in body["messages"] if item.get("tool_call_id") == "send-file"]
        if not results:
            return call("send_message", "send-file", {"text": "Original report", "files": [{"path": "files/report.bin",
                "expected_revision": "stale" if stale else revision, "subject": subject}]})
        messages.extend(results)
        return response({"content": "Done"})
    monkeypatch.setattr(composition, "create_stateless_http_client", lambda **kwargs:
        create_stateless_http_client(transport=httpx.MockTransport(provider), **kwargs))
    app = create_app(configured(tmp_path))
    async with app.router.lifespan_context(app):
        principal, agent, _ = await configure_agent(app.state.execution, test_database.sessions)
        output = WorkspaceSubject("membership", principal.membership_id)
        scope = WorkspaceScope(principal.tenant_id, agent.id, output, uuid4(), allow_shared_memory_writes=False)
        selected = output if subject == "output" else WorkspaceSubject("agent", agent.id)
        writer = WorkspaceScope(principal.tenant_id, agent.id, selected, uuid4())
        await app.state.execution.workspace.ensure(scope, selected)
        original = b"\x00\xfforiginal-report"
        revision = await app.state.execution.workspace.write(writer, selected, "files/report.bin", original, expected_revision=None)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            headers = await login(client, app, principal)
            made = await client.post("/api/sessions", headers=headers, json={"agent_id": str(agent.id)})
            session_id = UUID(made.json()["id"])
            submitted = await client.post(f"/api/sessions/{session_id}/inputs", headers=headers,
                json={"source_key": "report", "text": "Send me the report"})
            assert submitted.status_code == 202 and submitted.json()["error"] is None, submitted.text
            await eventually(test_database.sessions, principal.tenant_id, UUID(submitted.json()["run"]["run_id"]), "Completed")
            async with transaction(test_database.sessions) as tx:
                history = await SessionService(tx).read_history(principal, session_id=session_id)
            replies = [item for item in history.entries if item.kind == "reply"]
            assert messages
            if stale:
                assert not replies and "Workspace file changed" in messages[0]["content"]
                return
            assert len(replies) == 1 and len(replies[0].content.references) == 1
            reference = replies[0].content.references[0].reference
            await app.state.execution.workspace.write(writer, selected, "files/report.bin", b"replacement", expected_revision=revision)
            delivered = await app.state.attachment_inputs.read_for_delivery(tenant_id=principal.tenant_id, agent_id=agent.id,
                message_id=replies[0].id, reference=reference, kind="session")
            assert delivered.content == original and delivered.name == "report.bin"
            async with transaction(test_database.sessions) as tx:
                snapshot = await RunService(tx).read_snapshot(tenant_id=principal.tenant_id,
                    run_id=UUID(submitted.json()["run"]["run_id"]))
            replay = await app.state.products.send_message(snapshot, replies[0].step_id, replies[0].call_id,
                InputContent("Original report"), (WorkspaceMessageFile("files/report.bin", revision, subject),))
            assert replay["message_id"] == str(replies[0].id)
            with pytest.raises(NotFound):
                await app.state.attachment_inputs.read_for_delivery(tenant_id=principal.tenant_id, agent_id=agent.id,
                    message_id=history.entries[0].id, reference=reference, kind="session")


@pytest.mark.parametrize("failure,expected_orphans", [("second_stale", 1), ("over_total", 4), ("over_count", 0)])
async def test_failed_message_capture_has_no_reply_and_cleans_real_partial_blobs(
        test_database, composed_database, tmp_path, monkeypatch, failure, expected_orphans):  # noqa: F811
    revision = None
    results = []
    def provider(request):
        if request.method == "GET":
            return httpx.Response(404)
        body = json.loads(request.content)
        if any(item["function"]["name"] == "capability_probe" for item in body.get("tools", [])):
            return call("capability_probe", "probe", {"value": "ok"})
        settled = [item for item in body["messages"] if item.get("tool_call_id") == "send-files"]
        if not settled:
            count = 2 if failure == "second_stale" else 5 if failure == "over_total" else 9
            files = [{"path": "files/file.bin", "expected_revision":
                "stale" if failure == "second_stale" and i == 1 else revision} for i in range(count)]
            return call("send_message", "send-files", {"text": "Files", "files": files})
        results.extend(settled)
        return response({"content": "Capture failed"})
    monkeypatch.setattr(composition, "create_stateless_http_client", lambda **kwargs:
        create_stateless_http_client(transport=httpx.MockTransport(provider), **kwargs))
    app = create_app(configured(tmp_path))
    async with app.router.lifespan_context(app):
        principal, agent, _ = await configure_agent(app.state.execution, test_database.sessions)
        output = WorkspaceSubject("membership", principal.membership_id)
        scope = WorkspaceScope(principal.tenant_id, agent.id, output, uuid4())
        await app.state.execution.workspace.ensure(scope, output)
        data = b"x" * (4 * 1024 * 1024) if failure == "over_total" else b"file"
        revision = await app.state.execution.workspace.write(scope, output, "files/file.bin", data, expected_revision=None)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            headers = await login(client, app, principal)
            made = await client.post("/api/sessions", headers=headers, json={"agent_id": str(agent.id)})
            session_id = UUID(made.json()["id"])
            submitted = await client.post(f"/api/sessions/{session_id}/inputs", headers=headers,
                json={"source_key": "files", "text": "Send files"})
            assert submitted.status_code == 202 and submitted.json()["error"] is None, submitted.text
            await eventually(test_database.sessions, principal.tenant_id, UUID(submitted.json()["run"]["run_id"]), "Completed")
        async with transaction(test_database.sessions) as tx:
            history = await SessionService(tx).read_history(principal, session_id=session_id)
            orphans = await SessionAttachmentService(tx).expired_unbound(now=datetime.now(UTC) + timedelta(hours=25))
        assert results and not any(item.kind == "reply" for item in history.entries)
        assert len(orphans) == expected_orphans
        for blob in orphans:
            assert await app.state.execution.input_files.inspect(blob.storage_key) is not None
        assert await app.state.attachment_inputs.cleanup_once(now=datetime.now(UTC) + timedelta(hours=25)) == expected_orphans
        for blob in orphans:
            assert await app.state.execution.input_files.inspect(blob.storage_key) is None


async def test_group_model_message_delivers_original_workspace_file(test_database, composed_database, tmp_path, monkeypatch):  # noqa: F811
    revision = None
    def provider(request):
        if request.method == "GET":
            return httpx.Response(404)
        body = json.loads(request.content)
        if any(item["function"]["name"] == "capability_probe" for item in body.get("tools", [])):
            return call("capability_probe", "probe", {"value": "ok"})
        if not any(item.get("tool_call_id") == "group-file" for item in body["messages"]):
            return call("send_message", "group-file", {"text": "Report", "files": [{"path": "files/group.bin", "expected_revision": revision}]})
        return response({"content": "Done"})
    monkeypatch.setattr(composition, "create_stateless_http_client", lambda **kwargs:
        create_stateless_http_client(transport=httpx.MockTransport(provider), **kwargs))
    app = create_app(configured(tmp_path))
    async with app.router.lifespan_context(app):
        principal, agent, _ = await configure_agent(app.state.execution, test_database.sessions)
        async with transaction(test_database.sessions) as tx:
            group = await GroupService(tx).create(principal, name="Reports")
            await GroupService(tx).set_agent(principal, group_id=group.id, agent_id=agent.id, enabled=True)
        scope = WorkspaceScope(principal.tenant_id, agent.id, WorkspaceSubject("group", group.id), uuid4())
        await app.state.execution.workspace.ensure(scope, scope.output)
        data = b"\xffgroup-original"
        revision = await app.state.execution.workspace.write(scope, scope.output, "files/group.bin", data, expected_revision=None)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            headers = await login(client, app, principal)
            accepted = await client.post(f"/api/groups/{group.id}/inputs", headers=headers,
                json={"source_key": "file", "text": "Send the group report", "agent_ids": [str(agent.id)]})
            assert accepted.status_code == 202 and not accepted.json()["errors"], accepted.text
            await eventually(test_database.sessions, principal.tenant_id, UUID(accepted.json()["runs"][0]["run_id"]), "Completed")
        async with transaction(test_database.sessions) as tx:
            replies = [item for item in await GroupService(tx).list_events(principal, group_id=group.id) if item.kind == "reply"]
        assert len(replies) == 1 and len(replies[0].input.references) == 1
        await app.state.execution.workspace.write(scope, scope.output, "files/group.bin", b"changed", expected_revision=revision)
        actual = await app.state.attachment_inputs.read_for_delivery(tenant_id=principal.tenant_id, agent_id=agent.id,
            message_id=replies[0].id, reference=replies[0].input.references[0].reference, kind="group")
        assert actual.content == data
