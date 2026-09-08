"""Opt-in wall-clock core benchmark. Short smoke coverage is not load acceptance."""

import asyncio
import json
import math
import os
import platform
import subprocess
from collections import Counter, defaultdict
from dataclasses import replace
from pathlib import Path
from time import perf_counter
from uuid import uuid4

import httpx
import pytest
from execution_dependencies.test_resources import configured
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app import application
from app.execution_dependencies import resources as composition
from app.execution_dependencies.provisioning import provision_builtin_tools
from app.execution_dependencies.runtime import RuntimeToolBatches, capture_snapshot
from app.infrastructure.database import DatabaseResources
from app.infrastructure.http import create_stateless_http_client
from app.infrastructure.transactions import transaction
from app.modules.agent.public import AgentService
from app.modules.context.public import ContextAssembler, ContextSource, ContextState, ContextUnit
from app.modules.credential.public import Secret
from app.modules.identity_tenant.public import IdentityService, TenantPrincipal
from app.modules.model.public import ModelContent, ModelHardLimits, ModelMessage, ModelService
from app.modules.run.public import InputContent, RunRuntime, RunService, SourceIdentity, ToolResultPayload
from app.modules.tool.public import ToolResolutionScope
from app.modules.workspace.public import WorkspaceSubject

PROFILE = Path(__file__).parent / "profiles/backend_50.json"
MAX_LATENCY_BUCKET_MS = 60000


class Measurements:
    def __init__(self):
        self.phase = "setup"
        self.samples = defaultdict(Counter)
        self.maxima = defaultdict(float)
        self.counts = defaultdict(int)
        self.emitted = {}
        self.overflow = False

    def sample(self, name, value):
        if self.phase != "measurement":
            return
        self.counts[name] += 1
        bucket = math.ceil(value)
        self.maxima[name] = max(self.maxima[name], value)
        if bucket > MAX_LATENCY_BUCKET_MS:
            self.overflow = True
            bucket = MAX_LATENCY_BUCKET_MS + 1
        self.samples[name][bucket] += 1

    def percentiles(self):
        result = {}
        for name, histogram in self.samples.items():
            def percentile(fraction, *, name=name, histogram=histogram):
                target = math.ceil(self.counts[name] * fraction)
                seen = 0
                for upper, count in sorted(histogram.items()):
                    seen += count
                    if seen >= target:
                        return upper if upper <= MAX_LATENCY_BUCKET_MS else None
                raise AssertionError("Histogram count is incomplete")
            result[name] = {"count": self.counts[name], "histogram_resolution_ms": 1,
                "p50_ms": percentile(.50), "p95_ms": percentile(.95),
                "p99_ms": percentile(.99), "max_ms": self.maxima[name]}
        return result


def host_environment():
    result = {"platform": platform.platform(), "backend_cpu_vcpus": os.cpu_count(), "backend_memory_bytes": None,
        "postgresql": "disposable_local_container", "redis": "not_used_by_core", "object_storage": "local_filesystem"}
    if platform.system() == "Darwin":
        value = subprocess.run(["sysctl", "-n", "hw.memsize"], capture_output=True, text=True, check=True, timeout=10)
        result["backend_memory_bytes"] = int(value.stdout)
    elif hasattr(os, "sysconf"):
        result["backend_memory_bytes"] = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
    docker = subprocess.run(["docker", "info", "--format", "{{json .}}"], capture_output=True, text=True, timeout=30, check=True)
    data = json.loads(docker.stdout)
    result["docker_cpu_vcpus"], result["docker_memory_bytes"] = data["NCPU"], data["MemTotal"]
    return result


class ProviderStream(httpx.AsyncByteStream):
    def __init__(self, tracker, profile, *, tool):
        self.tracker, self.profile, self.tool = tracker, profile, tool

    async def __aiter__(self):
        await asyncio.sleep(self.profile["provider"]["first_delta_ms"] / 1000)
        if self.tool:
            call_id = uuid4().hex
            args = json.dumps({"workspace": "current", "path": "files/fixture.txt", "offset": 0, "limit": 16000})
            content = {"tool_calls": [{"index": 0, "id": call_id, "type": "function", "function": {"name": "read_file", "arguments": args}}]}
            key = (args, call_id)
            event = {"choices": [{"delta": content, "finish_reason": None}]}
            self.tracker.emitted[key] = (perf_counter(), self.tracker.phase == "measurement")
            yield ("data: " + json.dumps(event) + "\n\n").encode()
            await asyncio.sleep((self.profile["provider"]["completion_ms"] - self.profile["provider"]["first_delta_ms"]) / 1000)
            reason = "tool_calls"
        else:
            first = (uuid4().hex + ":").ljust(self.profile["fixture_payload_bytes"]["provider_delta"], "d")
            remaining = "r" * (self.profile["fixture_payload_bytes"]["provider_completion"] - len(first))
            self.tracker.emitted[(first, None)] = (perf_counter(), self.tracker.phase == "measurement")
            yield ("data: " + json.dumps({"choices": [{"delta": {"content": first}, "finish_reason": None}]}) + "\n\n").encode()
            await asyncio.sleep((self.profile["provider"]["completion_ms"] - self.profile["provider"]["first_delta_ms"]) / 1000)
            # A unique prefix preserves loss/forwarding attribution for each concurrent stream.
            remaining = first[:33] + remaining[33:]
            self.tracker.emitted[(remaining, None)] = (perf_counter(), self.tracker.phase == "measurement")
            yield ("data: " + json.dumps({"choices": [{"delta": {"content": remaining}, "finish_reason": None}]}) + "\n\n").encode()
            reason = "stop"
        yield ("data: " + json.dumps({"choices": [{"delta": {}, "finish_reason": reason}]}) + "\n\ndata: [DONE]\n\n").encode()


async def configure(execution, database, count):
    async with transaction(database.control_sessions) as tx:
        identity = IdentityService(tx)
        account = await identity.create_account()
        tenant = await identity.create_tenant(name="Core benchmark fixture")
        member = await identity.create_membership(tenant_id=tenant.id, account_id=account.id, display_name="Owner", role="tenant_admin")
        principal = TenantPrincipal(account.id, member.id, tenant.id, "tenant_admin")
        credential = await execution.credentials(tx).create(principal, kind="api_key", provider="fixture",
            label="Deterministic benchmark", secret=Secret("load-fixture-only"), owner_kind="tenant")
        model = await ModelService(tx).create(principal, credential_id=credential.id, provider="fixture", model_name="fixture",
            endpoint="https://provider.invalid/v1", context_limit=1048576, output_limit=32768,
            capability_source="administrator", capabilities={"supports_tool_calling": True, "supports_streaming": True},
            settings_version=1, settings={"protocol": "openai_chat"}, enabled=False)
    accepted = await execution.model.validate_configuration(tenant_id=tenant.id, credential_id=credential.id,
        provider="fixture", protocol="openai_chat", model_name="fixture", endpoint=model.endpoint,
        administrator_limits=ModelHardLimits(1048576, 32768), settings=model.settings, capabilities=model.capabilities)
    agents = []
    async with transaction(database.control_sessions) as tx:
        await ModelService(tx).set_enabled(principal, model_id=model.id, enabled=True, acceptance=accepted)
        for index in range(count):
            agent = await AgentService(tx).create(principal, name=f"Benchmark {index}", soul="Complete the requested work.",
                timezone="UTC", model_id=model.id)
            await provision_builtin_tools(tx, principal, agent_id=agent.id)
            agents.append(agent)
    resolved = await execution.model.resolve_policy(tenant_id=tenant.id, model_id=model.id, protocol="openai_chat")
    snapshots = []
    for agent in agents:
        scope = await execution.workspace.direct_scope(principal, agent_id=agent.id, run_id=uuid4())
        await execution.workspace.ensure(scope, scope.output)
        await execution.workspace.ensure(scope, WorkspaceSubject("agent", agent.id))
        snapshots.append(await capture_snapshot(execution, database, agent=agent, model=resolved, workspace=scope,
            tools=ToolResolutionScope(principal, agent.id, "main")))
    await execution.workspace.write(snapshots[0].workspace, snapshots[0].workspace.output, "files/fixture.txt",
        b"w" * 65536, expected_revision=None)
    return principal, snapshots


async def exercise(test_database, tmp_path, monkeypatch, profile, *, smoke=False):
    tracker = Measurements()
    async def provider(request):
        if request.method == "GET":
            return httpx.Response(404)
        body = json.loads(request.content)
        if any(t["function"]["name"] == "capability_probe" for t in body.get("tools", [])):
            return httpx.Response(200, json={"choices": [{"finish_reason": "tool_calls", "message": {
                "tool_calls": [{"id": "probe", "function": {"name": "capability_probe", "arguments": '{"value":"ok"}'}}]}}]})
        assert body["stream"] is True
        return httpx.Response(200, stream=ProviderStream(tracker, profile,
            tool=not any(m["role"] == "tool" for m in body["messages"])))
    monkeypatch.setattr(composition, "create_stateless_http_client",
        lambda **kwargs: create_stateless_http_client(transport=httpx.MockTransport(provider), **kwargs))
    engines = [create_async_engine(test_database.engine.url, pool_size=profile["capacity"]["database_pools"][role],
        max_overflow=0).execution_options(schema_translate_map={None: test_database.schema}) for role in ("control", "execution")]
    database = DatabaseResources(*engines, *(async_sessionmaker(engine, expire_on_commit=False) for engine in engines))
    async def database_resources(settings):
        return database
    monkeypatch.setattr(application.database, "create_database_resources", database_resources)
    app = application.create_app(configured(tmp_path))
    accepted = completed = failures = durable_loss = tool_failures = platform_failures = 0
    max_active = 0
    actual_durations = {}
    async with app.router.lifespan_context(app):
        await app.state.runtime.close()
        batches = RuntimeToolBatches(app.state.execution)
        async def observe(key, event):
            if event.kind != "model_event" or event.event is None:
                return
            observed = tracker.emitted.pop((event.event.text, event.event.call_id), None)
            if observed is not None and observed[1]:
                # Retain late forwarding samples during drain for events emitted in measurement.
                previous = tracker.phase
                tracker.phase = "measurement"
                tracker.sample("provider_delta_forwarding", (perf_counter() - observed[0]) * 1000)
                tracker.phase = previous
        runtime = RunRuntime(control_sessions=database.control_sessions, execution_sessions=database.execution_sessions,
            model=app.state.execution.model, tools=batches, observer=observe,
            slots=profile["capacity"]["run_pool"], capacity=profile["capacity"]["run_pool"] + profile["capacity"]["admission_queue"])
        batches.runtime = runtime
        await runtime.startup()
        try:
            principal, snapshots = await configure(app.state.execution, database, 2 if smoke else 50)
            workspace = app.state.execution.workspace
            original_read = workspace.read
            async def delayed_read(*args, **kwargs):
                await asyncio.sleep(profile["tools"]["ordinary_io_latency_ms"] / 1000)
                return await original_read(*args, **kwargs)
            monkeypatch.setattr(workspace, "read", delayed_read)
            stop = asyncio.Event()
            measurement_started = asyncio.Event()
            async def worker(snapshot):
                nonlocal accepted, completed, failures, durable_loss, tool_failures, platform_failures, max_active
                if smoke:
                    await measurement_started.wait()
                while not stop.is_set():
                    current = replace(snapshot, workspace=replace(snapshot.workspace, run_id=uuid4()))
                    measured = tracker.phase == "measurement"
                    started_at = perf_counter()
                    source = SourceIdentity("core_performance_fixture", principal.membership_id, str(current.workspace.run_id))
                    start = await runtime.start(snapshot=current,
                        input=InputContent("Read the fixture file, then complete. ".ljust(profile["fixture_payload_bytes"]["session_input"], "q")),
                        source=source)
                    tracker.sample("run_input_acceptance", (perf_counter() - started_at) * 1000)
                    accepted += int(measured)
                    while True:
                        now = perf_counter()
                        async with transaction(database.control_sessions) as tx:
                            run = await RunService(tx).get(tenant_id=principal.tenant_id, run_id=start.run.id)
                        tracker.sample("run_control_read", (perf_counter() - now) * 1000)
                        max_active = max(max_active, runtime.dispatcher.active)
                        if run.status in ("Completed", "Failed", "Cancelled", "Interrupted"):
                            break
                        await asyncio.sleep(.02)
                    if measured:
                        completed += int(run.status == "Completed")
                        failures += int(run.status != "Completed")
                        previous = tracker.phase
                        tracker.phase = "measurement"
                        tracker.sample("run_end_to_end", (perf_counter() - started_at) * 1000)
                        tracker.phase = previous
                        async with transaction(database.control_sessions) as tx:
                            page = await RunService(tx).read_history(tenant_id=principal.tenant_id, run_id=run.id,
                                after_sequence=0, limit=20)
                            durable_loss += int(not page.entries or page.entries[0].source != source)
                            results = [entry.payload for entry in page.entries if isinstance(entry.payload, ToolResultPayload)]
                            tool_failed = len(results) != 1 or any(value.result.status != "success" for value in results)
                            tool_failures += int(tool_failed)
                            platform_failures += int(tool_failed or run.status != "Completed")
                            if page.has_more:
                                raise RuntimeError("Fixed benchmark interaction exceeded its expected bounded History")
                    now = perf_counter()
                    result = await workspace.read(current.workspace, current.workspace.output, "files/fixture.txt")
                    tracker.sample("bounded_workspace_operation", (perf_counter() - now) * 1000)
                    assert len(result.content) == profile["fixture_payload_bytes"]["workspace_operation"]
            async def contexts():
                sources = (ContextSource("Instructions", "Be accurate.", "system"),)
                assembler = ContextAssembler(sources=sources, profile=snapshots[0].model.profile)
                hot_base = await assembler.prepare(state=ContextState(), additions=(ContextUnit(1, (
                    ModelMessage("user", (ModelContent("text", "c" * (profile["fixture_payload_bytes"]["hot_context"] - 1)),)),)),), tools=())
                while not stop.is_set():
                    for label in ("hot", "cold"):
                        now = perf_counter()
                        if label == "hot":
                            await assembler.prepare(state=hot_base.state,
                                additions=(ContextUnit(2, (ModelMessage("user", (ModelContent("text", "c"),)),)),), tools=())
                        else:
                            cold = ContextAssembler(sources=sources, profile=snapshots[0].model.profile)
                            unit = ContextUnit(1, (ModelMessage("user", (ModelContent("text", "c" * profile["fixture_payload_bytes"]["cold_context"]),)),))
                            await cold.prepare(state=ContextState(), additions=(unit,), tools=())
                        tracker.sample(f"{label}_context_assembly", (perf_counter() - now) * 1000)
                    await asyncio.sleep(.1)
            jobs = [asyncio.create_task(worker(snapshot)) for snapshot in snapshots]
            jobs.append(asyncio.create_task(contexts()))
            try:
                for phase, seconds in (("warmup", profile["duration"]["warmup_seconds"]), ("measurement", profile["duration"]["measurement_seconds"])):
                    tracker.phase = phase
                    if phase == "measurement":
                        measurement_started.set()
                    beginning = perf_counter()
                    deadline = beginning + seconds
                    while perf_counter() < deadline:
                        await asyncio.sleep(min(1, deadline - perf_counter()))
                        for job in jobs:
                            if job.done():
                                job.result()
                                raise RuntimeError("Benchmark worker exited before its phase completed")
                    actual_durations[phase] = perf_counter() - beginning
                    print(f"{phase} completed: {actual_durations[phase]:.3f}s", flush=True)
            finally:
                tracker.phase = "drain"
                stop.set()
                try:
                    async with asyncio.timeout(60):
                        await asyncio.gather(*jobs)
                finally:
                    for job in jobs:
                        job.cancel()
                    await asyncio.gather(*jobs, return_exceptions=True)
        finally:
            await runtime.close()
    stream_loss = sum(measured for _, measured in tracker.emitted.values())
    metrics = tracker.percentiles()
    reasons = ["Local filesystem storage differs from the required object-storage container",
        "Core fixture does not exercise G006 product API/workload mix or CPU Tools",
        "Slow Tool-result payload fixture is not exercised by the current read_file Tool"]
    thresholds = profile["thresholds"]
    for name, threshold in thresholds["p95_ms"].items():
        if name in metrics and (metrics[name]["p95_ms"] is None or metrics[name]["p95_ms"] > threshold):
            reasons.append(f"{name} p95 exceeds {threshold} ms")
    if failures or tool_failures or durable_loss or stream_loss or tracker.overflow:
        reasons.append("Execution/loss/sample bounds did not pass")
    return {"schema_version": 1, "scenario": "core", "qualification": "smoke_only" if smoke else "not_qualified",
        "reasons": reasons, "durations_seconds": actual_durations, "metrics": metrics,
        "accepted_runs": accepted, "completed_runs": completed, "failed_runs": failures,
        "platform_error_rate": platform_failures / accepted if accepted else None,
        "runs_with_failed_or_missing_tool_result": tool_failures,
        "accepted_durable_event_loss": durable_loss, "stream_event_loss": stream_loss,
        "max_active_slots_observed": max_active, "agent_count": len(snapshots),
        "runtime_capacity": profile["capacity"], "payload_targets": profile["fixture_payload_bytes"],
        "unmeasured": ["non_model_api", "session_input_acceptance", "hostile_fairness_during_load", "cpu_tool_concurrency"],
        "scope": "Actual RunRuntime, Model HTTP adapter, Workspace Tool, PostgreSQL. Hot/cold are isolated Context owner calls under the same load, not full Run cold-start timings."}


@pytest.mark.skipif("CLAWITH_CORE_LOAD_PROFILE" not in os.environ, reason="18-minute canonical load is opt-in")
async def test_full_core_profile(test_database, tmp_path, monkeypatch):
    profile = json.loads(Path(os.environ["CLAWITH_CORE_LOAD_PROFILE"]).read_text())
    assert profile == json.loads(PROFILE.read_text()), "Only the immutable profile is accepted"
    environment = await asyncio.to_thread(host_environment)
    output = Path(os.environ["CLAWITH_CORE_LOAD_OUT"])
    output.parent.mkdir(parents=True, exist_ok=True)
    try:
        report = await exercise(test_database, tmp_path, monkeypatch, profile)
    except Exception as error:
        output.write_text(json.dumps({"qualification": "failed", "failure_type": type(error).__name__,
            "environment": environment, "note": "The full measurement did not complete; no performance acceptance."}, indent=2) + "\n")
        raise
    report["environment"] = environment
    if environment["backend_cpu_vcpus"] != 8 or environment["backend_memory_bytes"] != 16 * 1024**3:
        report["reasons"].append("Backend CPU/RAM do not match the required 8 vCPU/16 GiB envelope")
    if environment["docker_memory_bytes"] < 16 * 1024**3:
        report["reasons"].append("Docker memory allocation is below the required envelope")
    output.write_text(json.dumps(report, indent=2) + "\n")


async def test_core_load_driver_smoke(test_database, tmp_path, monkeypatch):
    profile = json.loads(PROFILE.read_text())
    profile["duration"] = {"warmup_seconds": .1, "measurement_seconds": 1.5}
    report = await exercise(test_database, tmp_path, monkeypatch, profile, smoke=True)
    assert report["qualification"] == "smoke_only"
    assert report["durations_seconds"]["measurement"] >= 1.5
    assert report["metrics"]["provider_delta_forwarding"]["count"] > 0
    assert report["accepted_runs"] > 0 and report["failed_runs"] == 0
    assert report["platform_error_rate"] == 0
    assert report["accepted_durable_event_loss"] == report["stream_event_loss"] == 0


def test_histogram_keeps_all_observations_without_unbounded_sample_storage():
    measurements = Measurements()
    measurements.phase = "measurement"
    for _ in range(200_001):
        measurements.sample("read", .25)
    values = measurements.percentiles()["read"]
    assert values["count"] == 200_001
    assert values["p95_ms"] == 1
    assert values["max_ms"] == .25
    assert len(measurements.samples["read"]) == 1
    assert not measurements.overflow
