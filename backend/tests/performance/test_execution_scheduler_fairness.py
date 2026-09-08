"""Real Run intake and execution under an adversarial Tenant backlog."""

import asyncio
import json
from dataclasses import replace
from uuid import uuid4

import httpx
import pytest
from e2e.test_runtime_product_owner_fixture import configure_agent
from execution_dependencies.test_resources import composed_database, configured  # noqa: F401

from app import application
from app.execution_dependencies import resources as composition
from app.execution_dependencies.runtime import RuntimeToolBatches, capture_snapshot
from app.infrastructure.http import create_stateless_http_client
from app.infrastructure.transactions import transaction
from app.modules.run.public import InputContent, RunRuntime, RunService, SourceIdentity
from app.modules.tool.public import ToolResolutionScope
from app.modules.workspace.public import WorkspaceSubject


async def snapshot_for(execution, database, sessions):
    principal, agent, model = await configure_agent(execution, sessions)
    scope = await execution.workspace.direct_scope(principal, agent_id=agent.id, run_id=uuid4())
    await execution.workspace.ensure(scope, scope.output)
    await execution.workspace.ensure(scope, WorkspaceSubject("agent", agent.id))
    snapshot = await capture_snapshot(execution, database, agent=agent, model=model, workspace=scope,
        tools=ToolResolutionScope(principal, agent.id, "main"))
    return principal, snapshot


async def wait_until(predicate, timeout=15):
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(0.005)


@pytest.mark.parametrize("slots", [1, 50])
@pytest.mark.usefixtures("composed_database")
async def test_later_tenant_enters_under_flood_and_failed_runs_release_capacity(
        test_database, tmp_path, monkeypatch, slots):
    gates = asyncio.Semaphore(0)
    observed = []

    async def provider(request):
        if request.method == "GET":
            return httpx.Response(404)
        body = json.loads(request.content)
        if any(tool["function"]["name"] == "capability_probe" for tool in body.get("tools", [])):
            return httpx.Response(200, json={"choices": [{"finish_reason": "tool_calls", "message": {
                "tool_calls": [{"id": "probe", "function": {"name": "capability_probe", "arguments": '{"value":"ok"}'}}]}}]})
        label = body["messages"][-1]["content"]
        if isinstance(label, list):
            label = "".join(part.get("text", "") for part in label)
        label = label.split("\n", 1)[-1]
        observed.append(label)
        await gates.acquire()
        if label == "B-fail":
            return httpx.Response(400, json={"error": {"message": "Controlled failure"}})
        return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {"content": "done"}}]})

    monkeypatch.setattr(composition, "create_stateless_http_client",
        lambda **kwargs: create_stateless_http_client(transport=httpx.MockTransport(provider), **kwargs))
    app = application.create_app(configured(tmp_path))
    async with app.router.lifespan_context(app):
        a, a_snapshot = await snapshot_for(app.state.execution, app.state.database, test_database.sessions)
        b, b_snapshot = await snapshot_for(app.state.execution, app.state.database, test_database.sessions)
        # The application supplies the actual Model and Tool composition; only the
        # physical slot count changes for deterministic one-slot boundary coverage.
        original = app.state.runtime
        await original.close()
        batches = RuntimeToolBatches(app.state.execution)
        runtime = RunRuntime(control_sessions=app.state.database.control_sessions,
            execution_sessions=app.state.database.execution_sessions, model=app.state.execution.model,
            tools=batches, slots=slots, capacity=150)
        batches.runtime = runtime
        await runtime.startup()
        cleanup_gate = asyncio.Event()
        cleanup_seen, cleanup_finished = set(), set()
        release_continuation = runtime._model.release_continuation
        async def observed_cleanup(**kwargs):
            cleanup_seen.add(kwargs["run_id"])
            # Once all requests reached the provider, hold real cleanup to distinguish
            # committed admission release from termination of the execution task.
            if len(observed) == 51:
                await cleanup_gate.wait()
            await release_continuation(**kwargs)
            cleanup_finished.add(kwargs["run_id"])
        monkeypatch.setattr(runtime._model, "release_continuation", observed_cleanup)
        try:
            starts = []
            for index in range(50):
                snapshot = replace(a_snapshot, workspace=replace(a_snapshot.workspace, run_id=uuid4()))
                starts.append(await runtime.start(snapshot=snapshot, input=InputContent(f"A-{index}"),
                    source=SourceIdentity("performance_fixture", a.membership_id, str(index))))
            await wait_until(lambda: len(observed) == slots)
            second = await runtime.start(snapshot=b_snapshot, input=InputContent("B-fail"),
                source=SourceIdentity("performance_fixture", b.membership_id, "later-tenant"))
            assert runtime.dispatcher.admitted == 51
            before = len(observed)
            gates.release()
            await wait_until(lambda: len(observed) > before)
            if observed[-1] != "B-fail":
                gates.release()
                await wait_until(lambda: "B-fail" in observed)
            assert observed.index("B-fail") - before <= 1
            for _ in range(60):
                gates.release()
            await wait_until(lambda: runtime.dispatcher.admitted == 0)
            async with transaction(test_database.sessions) as tx:
                service = RunService(tx)
                assert (await service.get(tenant_id=b.tenant_id, run_id=second.run.id)).status == "Failed"
                for start in starts:
                    assert (await service.get(tenant_id=a.tenant_id, run_id=start.run.id)).status == "Completed"
            expected = {start.run.id for start in starts} | {second.run.id}
            await wait_until(lambda: cleanup_seen == expected)
            assert runtime.dispatcher.active > 0
            cleanup_gate.set()
            await wait_until(lambda: runtime.dispatcher.active == 0)
            assert cleanup_finished == expected
            assert runtime.dispatcher.failures == {}
            assert runtime.dispatcher.active == 0
        finally:
            cleanup_gate.set()
            await runtime.close()
