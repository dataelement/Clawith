"""Slow authenticated upload bodies cannot occupy attachment storage-read capacity."""

import asyncio

import httpx
from e2e.test_attachments import login
from e2e.test_direct_session import call
from e2e.test_runtime_product_owner_fixture import configure_agent
from execution_dependencies.test_resources import composed_database, configured  # noqa: F401

from app.application import create_app
from app.execution_dependencies import resources as composition
from app.infrastructure.http import create_stateless_http_client


async def test_four_slow_uploads_leave_download_capacity_and_cancelled_bodies_release_admission(
        test_database, composed_database, tmp_path, monkeypatch):  # noqa: F811
    def provider(request):
        return httpx.Response(404) if request.method == "GET" else call("capability_probe", "probe", {"value":"ok"})
    monkeypatch.setattr(composition, "create_stateless_http_client", lambda **kwargs:
        create_stateless_http_client(transport=httpx.MockTransport(provider), **kwargs))
    app = create_app(configured(tmp_path))
    async with app.router.lifespan_context(app):
        principal, agent, _ = await configure_agent(app.state.execution, test_database.sessions)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            headers = await login(client, app, principal)
            made = await client.post("/api/sessions", headers=headers, json={"agent_id":str(agent.id)})
            path = f"/api/sessions/{made.json()['id']}/attachments"
            uploaded = await client.post(path, headers=headers, params={"upload_source_key":"ready",
                "filename":"ready.txt","media_type":"text/plain"}, content=b"readable while uploads are stalled")
            assert uploaded.status_code == 201, uploaded.text
            started = [asyncio.Event() for _ in range(4)]
            closed = [asyncio.Event() for _ in range(4)]
            release = asyncio.Event()
            async def slow_body(index):
                try:
                    started[index].set()
                    yield b"partial-body"
                    await release.wait()
                    yield b"remainder"
                finally:
                    closed[index].set()
            tasks = [asyncio.create_task(client.post(path, headers=headers, params={
                "upload_source_key":f"slow-{index}","filename":"slow.bin","media_type":"application/octet-stream"},
                content=slow_body(index))) for index in range(4)]
            try:
                await asyncio.wait_for(asyncio.gather(*(event.wait() for event in started)), timeout=5)
                downloaded = await asyncio.wait_for(client.get(path + "/" + uploaded.json()["id"], headers=headers), timeout=2)
                assert downloaded.status_code == 200 and downloaded.content == b"readable while uploads are stalled"
            finally:
                for task in tasks:
                    task.cancel()
                outcomes = await asyncio.gather(*tasks, return_exceptions=True)
                release.set()
            assert all(isinstance(outcome, asyncio.CancelledError) for outcome in outcomes)
            await asyncio.wait_for(asyncio.gather(*(event.wait() for event in closed)), timeout=2)
            after = await asyncio.wait_for(client.post(path, headers=headers, params={"upload_source_key":"after",
                "filename":"after.txt","media_type":"text/plain"}, content=b"admission is available"), timeout=2)
            assert after.status_code == 201, after.text
