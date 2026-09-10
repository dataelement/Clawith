"""Raw HTTP input files through actual product ownership, storage and Model requests."""

import asyncio
import io
import json
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from uuid import UUID, uuid4

import httpx
import pytest
from e2e.test_direct_session import call, response
from e2e.test_mcp_image_result import image_urls
from e2e.test_runtime_product_owner_fixture import configure_agent, eventually
from execution_dependencies.test_resources import composed_database, configured  # noqa: F401
from PIL import Image

from app.application import create_app
from app.execution_dependencies import resources as composition
from app.infrastructure.http import create_stateless_http_client
from app.infrastructure.transactions import transaction
from app.modules.group.public import GroupService
from app.modules.identity_tenant.public import IdentityService
from app.modules.session.public import SessionAttachmentService, SessionService


def picture():
    with Image.new("RGB", (64, 32), "blue") as image:
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        return buffer.getvalue()


async def login(client, app, principal, name="person"):
    await app.state.auth.provision_trusted_verifier(account_id=principal.account_id, login_name=name, password="password")
    result = await client.post("/api/auth/login", json={"login_name": name, "password": "password", "tenant_id": str(principal.tenant_id)})
    assert result.status_code == 200, result.text
    return {"Authorization": "Bearer " + result.json()["token"]}


@pytest.mark.parametrize("media_type", ["text/plain", "image/png"])
async def test_uploaded_attachment_reaches_actual_model_only_after_explicit_tool_read(
        test_database, composed_database, tmp_path, monkeypatch, media_type):  # noqa: F811
    content = b"owned-file-content-marker" if media_type == "text/plain" else picture()
    observed, counted = [], []
    reference = None
    def provider(request):
        if request.method == "GET":
            return httpx.Response(404)
        body = json.loads(request.content)
        if request.url.path.endswith("/responses/input_tokens"):
            counted.append(body)
            return httpx.Response(200, json={"input_tokens": 1000})
        names = {tool["function"]["name"] for tool in body.get("tools", [])}
        if "capability_probe" in names:
            return call("capability_probe", "probe", {"value": "ok"})
        observed.append(body)
        results = {message.get("tool_call_id") for message in body["messages"] if message["role"] == "tool"}
        if "read_attachment" not in names:
            return call("search_tools", "find-file-tool", {"query": "read_attachment"})
        if "read-file" not in results:
            return call("read_attachment", "read-file", {"reference": reference})
        if "reply" not in results:
            if media_type == "text/plain":
                assert "owned-file-content-marker" in json.dumps(body)
            else:
                images = list(image_urls(body))
                assert images and all(value.startswith("data:image/jpeg;base64,") for value in images)
                assert "Reduced first-frame image preview" in json.dumps(body)
            return call("send_message", "reply", {"text": "I inspected the provided attachment view."})
        return response({"content": "Execution complete"})
    monkeypatch.setattr(composition, "create_stateless_http_client", lambda **kwargs:
        create_stateless_http_client(transport=httpx.MockTransport(provider), **kwargs))
    app = create_app(configured(tmp_path))
    async with app.router.lifespan_context(app):
        principal, agent, _ = await configure_agent(app.state.execution, test_database.sessions,
            capabilities={"supports_tool_calling": True, "supports_images": True, "image_token_counting": "openai_responses"})
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            headers = await login(client, app, principal)
            made = await client.post("/api/sessions", headers=headers, json={"agent_id": str(agent.id)})
            session_id = made.json()["id"]
            path = f"/api/sessions/{session_id}/attachments"
            params = {"upload_source_key": "upload", "filename": "input.txt" if media_type == "text/plain" else "input.png", "media_type": media_type}
            uploaded = await client.post(path, headers=headers, params=params, content=content)
            assert uploaded.status_code == 201, uploaded.text
            data = uploaded.json()
            reference = data["reference"]
            assert data["sha256"] == sha256(content).hexdigest() and data["origin_input_id"] is None
            repeated = await client.post(path, headers=headers, params=params, content=content)
            assert repeated.status_code == 201 and repeated.json()["id"] == data["id"]
            downloaded = await client.get(path + "/" + data["id"], headers=headers)
            assert downloaded.status_code == 200 and downloaded.content == content
            assert downloaded.headers["x-content-type-options"] == "nosniff"
            submitted = await client.post(f"/api/sessions/{session_id}/inputs", headers=headers, json={"source_key": "read",
                "text": "Read the attachment", "references": [{"reference": reference, "name": params["filename"], "media_type": media_type}]})
            assert submitted.status_code == 202 and submitted.json()["error"] is None, submitted.text
            await eventually(test_database.sessions, principal.tenant_id, UUID(submitted.json()["run"]["run_id"]), "Completed")
            assert observed and "owned-file-content-marker" not in json.dumps(observed[0]) and not list(image_urls(observed[0]))
            assert bool(counted) == (media_type == "image/png")


async def test_raw_upload_bounds_private_access_binary_download_and_real_cleanup(
        test_database, composed_database, tmp_path, monkeypatch):  # noqa: F811
    def provider(request):
        return httpx.Response(404) if request.method == "GET" else call("capability_probe", "probe", {"value": "ok"})
    monkeypatch.setattr(composition, "create_stateless_http_client", lambda **kwargs:
        create_stateless_http_client(transport=httpx.MockTransport(provider), **kwargs))
    app = create_app(configured(tmp_path))
    async with app.router.lifespan_context(app):
        principal, agent, _ = await configure_agent(app.state.execution, test_database.sessions)
        async with transaction(test_database.sessions) as tx:
            identity = IdentityService(tx)
            account = await identity.create_account()
            membership = await identity.create_membership(tenant_id=principal.tenant_id, account_id=account.id,
                display_name="Other administrator", role="tenant_admin")
            from app.modules.identity_tenant.public import TenantPrincipal
            other = TenantPrincipal(account.id, membership.id, principal.tenant_id, "tenant_admin")
            group = await GroupService(tx).create(principal, name="Files")
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            headers, other_headers = await login(client, app, principal), await login(client, app, other, "other")
            made = await client.post("/api/sessions", headers=headers, json={"agent_id": str(agent.id)})
            session_id = UUID(made.json()["id"])
            path = f"/api/sessions/{session_id}/attachments"
            params = {"upload_source_key": "binary", "filename": "file.bin", "media_type": "application/octet-stream"}
            assert (await client.post(path, params=params, content=b"x")).status_code == 401
            assert (await client.post(path, headers=other_headers, params=params, content=b"x")).status_code == 403
            big_headers = {**headers, "Content-Length": str(4194305)}
            assert (await client.post(path, headers=big_headers, params=params, content=b"x")).status_code == 413
            async def excessive():
                yield b"x" * 3000000
                yield b"y" * 2000000
            assert (await client.post(path, headers=headers, params=params, content=excessive())).status_code == 413
            binary = b"\x00\xffbinary-data"
            uploaded = await client.post(path, headers=headers, params=params, content=binary)
            assert uploaded.status_code == 201, uploaded.text
            data = uploaded.json()
            assert (await client.get(path + "/" + data["id"], headers=other_headers)).status_code == 403
            assert (await client.get(path + "/" + data["id"], headers=headers)).content == binary
            group_path = f"/api/groups/{group.id}/attachments"
            assert (await client.post(group_path, headers=other_headers, params=params, content=binary)).status_code == 403
            grouped = await client.post(group_path, headers=headers, params=params, content=binary)
            assert grouped.status_code == 201, grouped.text
            assert (await client.get(group_path + "/" + grouped.json()["id"], headers=headers)).content == binary
            async with transaction(test_database.sessions) as tx:
                assert (await SessionService(tx).get(principal, session_id=session_id)).through_position == 0
                blob = await SessionAttachmentService(tx).get_upload(principal, session_id=session_id, attachment_id=UUID(data["id"]))
            assert await app.state.execution.input_files.inspect(blob.storage_key) is not None
            removed = await app.state.attachment_inputs.cleanup_once(now=datetime.now(UTC) + timedelta(hours=25))
            assert removed == 2
            assert await app.state.execution.input_files.inspect(blob.storage_key) is None
            assert (await client.get(path + "/" + data["id"], headers=headers)).status_code == 404


async def test_model_saves_original_binary_attachment_to_its_personal_workspace(
        test_database, composed_database, tmp_path, monkeypatch):  # noqa: F811
    from app.modules.run.public import RunService

    original = b"\x00\xff\x81original-binary-not-a-text-preview"
    reference = None
    saved_results = []
    def provider(request):
        if request.method == "GET":
            return httpx.Response(404)
        body = json.loads(request.content)
        names = {tool["function"]["name"] for tool in body.get("tools", [])}
        if "capability_probe" in names:
            return call("capability_probe", "probe", {"value": "ok"})
        results = {message.get("tool_call_id"): message for message in body["messages"] if message["role"] == "tool"}
        if "save_attachment" not in names:
            return call("search_tools", "find-saver", {"query": "save_attachment"})
        if "save-original" not in results:
            return call("save_attachment", "save-original", {"reference": reference,
                "path": "files/original.bin", "expected_revision": None})
        saved_results.append(results["save-original"])
        assert '"saved_original":true' in results["save-original"]["content"]
        return response({"content": "Original bytes saved."})
    monkeypatch.setattr(composition, "create_stateless_http_client", lambda **kwargs:
        create_stateless_http_client(transport=httpx.MockTransport(provider), **kwargs))
    app = create_app(configured(tmp_path))
    async with app.router.lifespan_context(app):
        principal, agent, _ = await configure_agent(app.state.execution, test_database.sessions)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            headers = await login(client, app, principal)
            made = await client.post("/api/sessions", headers=headers, json={"agent_id": str(agent.id)})
            session_id = made.json()["id"]
            uploaded = await client.post(f"/api/sessions/{session_id}/attachments", headers=headers,
                params={"upload_source_key": "save", "filename": "original.bin", "media_type": "application/octet-stream"},
                content=original)
            assert uploaded.status_code == 201, uploaded.text
            reference = uploaded.json()["reference"]
            submitted = await client.post(f"/api/sessions/{session_id}/inputs", headers=headers,
                json={"source_key": "save", "text": "Save the original file in my Workspace",
                    "references": [{"reference": reference, "name": "original.bin", "media_type": "application/octet-stream"}]})
            assert submitted.status_code == 202 and submitted.json()["error"] is None, submitted.text
            run_id = UUID(submitted.json()["run"]["run_id"])
            await eventually(test_database.sessions, principal.tenant_id, run_id, "Completed")
            async with transaction(test_database.sessions) as tx:
                snapshot = await RunService(tx).read_snapshot(tenant_id=principal.tenant_id, run_id=run_id)
            assert snapshot.workspace.output.kind == "membership"
            assert snapshot.workspace.output.id == principal.membership_id
            stored = await app.state.execution.workspace.read(snapshot.workspace, snapshot.workspace.output, "files/original.bin")
            assert stored.content == original and saved_results


async def test_actual_a2a_tool_delegates_only_its_explicit_authorized_file_subset(
        test_database, composed_database, tmp_path, monkeypatch):  # noqa: F811
    from app.execution_dependencies.provisioning import provision_builtin_tools
    from app.modules.a2a.public import A2AService
    from app.modules.agent.public import AgentService
    from app.modules.permission.public import PermissionService

    allowed, not_delegated, target_id = None, None, None
    request_ids = []
    source_results = []
    target_finished_reading = []
    def provider(request):
        if request.method == "GET":
            return httpx.Response(404)
        body = json.loads(request.content)
        names = {tool["function"]["name"] for tool in body.get("tools", [])}
        if "capability_probe" in names:
            return call("capability_probe", "probe", {"value": "ok"})
        is_target = "Attachment receiver" in json.dumps(body["messages"][0])
        results = {message.get("tool_call_id"): message for message in body["messages"] if message["role"] == "tool"}
        if is_target:
            if "read_attachment" not in names:
                return call("search_tools", "find-reader", {"query": "read_attachment"})
            if "allowed-read" not in results:
                return call("read_attachment", "allowed-read", {"reference": allowed})
            assert "delegated-file-marker" in json.dumps(body)
            if "denied-read" not in results:
                return call("read_attachment", "denied-read", {"reference": not_delegated})
            assert "not explicitly delegated" in json.dumps(results["denied-read"])
            assert "unselected-file-private-marker" not in json.dumps(body)
            target_finished_reading.append(True)
            return response({"content": "Target inspected its authorized subset"})
        if "send_message_to_agent" not in names:
            return call("search_tools", "find-a2a", {"query": "send_message_to_agent"})
        if "delegate-file" not in results:
            return call("send_message_to_agent", "delegate-file", {"target_agent_id": str(target_id), "intent": "notify",
                "text": "Read only the delegated file", "references": [{"reference": allowed}]})
        raw = results["delegate-file"]["content"]
        payload = json.loads(raw if isinstance(raw, str) else raw[0]["text"])
        source_results.append(payload)
        if payload.get("accepted") is True:
            request_ids.append(UUID(payload["request_id"]))
        return response({"content": "Delegation accepted"})

    monkeypatch.setattr(composition, "create_stateless_http_client", lambda **kwargs:
        create_stateless_http_client(transport=httpx.MockTransport(provider), **kwargs))
    app = create_app(configured(tmp_path))
    async with app.router.lifespan_context(app):
        principal, source_agent, _ = await configure_agent(app.state.execution, test_database.sessions)
        async with transaction(test_database.sessions) as tx:
            target = await AgentService(tx).create(principal, name="Attachment receiver", soul="Use only delegated input files",
                timezone="UTC", model_id=source_agent.model_id)
            target_id = target.id
            await provision_builtin_tools(tx, principal, agent_id=target.id)
            await PermissionService(tx).set_visibility(principal, agent_id=target.id, visibility="tenant")
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            headers = await login(client, app, principal)
            made = await client.post("/api/sessions", headers=headers, json={"agent_id": str(source_agent.id)})
            session_id = made.json()["id"]
            path = f"/api/sessions/{session_id}/attachments"
            first = await client.post(path, headers=headers, params={"upload_source_key": "one", "filename": "allowed.txt", "media_type": "text/plain"},
                content=b"delegated-file-marker")
            second = await client.post(path, headers=headers, params={"upload_source_key": "two", "filename": "private.txt", "media_type": "text/plain"},
                content=b"unselected-file-private-marker")
            assert first.status_code == second.status_code == 201
            allowed, not_delegated = first.json()["reference"], second.json()["reference"]
            submitted = await client.post(f"/api/sessions/{session_id}/inputs", headers=headers, json={"source_key": "delegate",
                "text": "Delegate the selected file", "references": [{"reference": allowed}, {"reference": not_delegated}]})
            assert submitted.status_code == 202 and submitted.json()["error"] is None, submitted.text
            await eventually(test_database.sessions, principal.tenant_id, UUID(submitted.json()["run"]["run_id"]), "Completed")
            assert source_results and source_results[0].get("accepted") is True, source_results
            assert request_ids
            async with transaction(test_database.sessions) as tx:
                request = await A2AService(tx).get(tenant_id=principal.tenant_id, request_id=request_ids[0])
            assert request.target_run_id is not None
            await eventually(test_database.sessions, principal.tenant_id, request.target_run_id, "Completed")
            assert target_finished_reading


async def test_cancelled_upload_drains_started_storage_before_duplicate_publication(
        test_database, composed_database, tmp_path, monkeypatch):  # noqa: F811
    def provider(request):
        return httpx.Response(404) if request.method == "GET" else call("capability_probe", "probe", {"value": "ok"})
    monkeypatch.setattr(composition, "create_stateless_http_client", lambda **kwargs:
        create_stateless_http_client(transport=httpx.MockTransport(provider), **kwargs))
    app = create_app(configured(tmp_path))
    async with app.router.lifespan_context(app):
        principal, agent, _ = await configure_agent(app.state.execution, test_database.sessions)
        entered, release = asyncio.Event(), asyncio.Event()
        original = app.state.execution.input_files.put_if_absent
        writes = []
        async def delayed(key, content):
            if not writes:
                writes.append("started")
                entered.set()
                await release.wait()
            result = await original(key, content)
            writes.append("settled")
            return result
        monkeypatch.setattr(app.state.execution.input_files, "put_if_absent", delayed)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            headers = await login(client, app, principal)
            made = await client.post("/api/sessions", headers=headers, json={"agent_id": str(agent.id)})
            path = f"/api/sessions/{made.json()['id']}/attachments"
            params = {"upload_source_key": "same", "filename": "file.txt", "media_type": "text/plain"}
            first = asyncio.create_task(client.post(path, headers=headers, params=params, content=b"persist once"))
            second = None
            try:
                await asyncio.wait_for(entered.wait(), 2)
                first.cancel()
                await asyncio.sleep(.01)
                assert not first.done(), "Cancellation must not release a running storage write"
                second = asyncio.create_task(client.post(path, headers=headers, params=params, content=b"persist once"))
                await asyncio.sleep(.01)
                release.set()
                result = await asyncio.wait_for(second, 3)
                assert result.status_code == 201, result.text
                assert (await client.get(path + "/" + result.json()["id"], headers=headers)).content == b"persist once"
            finally:
                release.set()
                first.cancel()
                if second is not None:
                    second.cancel()
                await asyncio.gather(first, *(() if second is None else (second,)), return_exceptions=True)
            assert first.cancelled() and writes.count("settled") == 2


async def test_a2a_mutation_boundary_rejects_file_references_without_source_authorizer(transaction_factory):
    from modules.a2a.test_service import setup

    from app.infrastructure.errors import AccessDenied
    from app.modules.a2a.public import A2AService
    from app.modules.run.public import InputContent, InputReference

    principal, source, target = await setup(transaction_factory)
    async with transaction_factory() as tx:
        with pytest.raises(AccessDenied, match="source attachment authorization"):
            await A2AService(tx).accept(tenant_id=principal.tenant_id, source_run_id=source.id,
                step_id="step", call_id="call", target_agent_id=target, intent="notify",
                input=InputContent("Delegation", (InputReference(f"attachment:session:{uuid4()}"),)))
