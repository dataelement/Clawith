"""Intake-only diagnostics: real PostgreSQL, no dispatched Model/Tool execution."""

import asyncio
import json
import math
from collections import defaultdict
from contextvars import ContextVar
from dataclasses import replace
from time import perf_counter
from uuid import uuid4

import httpx
from execution_dependencies.test_resources import configured
from performance.test_execution_scheduler_fairness import snapshot_for
from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app import application
from app.execution_dependencies import resources as composition
from app.infrastructure.database import DatabaseResources
from app.infrastructure.http import create_stateless_http_client
from app.infrastructure.transactions import transaction
from app.modules.run import snapshot as snapshot_codec
from app.modules.run.public import InputContent, RunService, SourceIdentity


def distribution(values):
    values = sorted(values)
    return {"count": len(values), "p50_ms": values[math.ceil(len(values) * .5) - 1],
        "p95_ms": values[math.ceil(len(values) * .95) - 1], "max_ms": values[-1]}


async def test_real_fifty_concurrent_start_latency(test_database, tmp_path, monkeypatch):
    def provider(request):
        if request.method == "GET":
            return httpx.Response(404)
        return httpx.Response(200, json={"choices": [{"finish_reason": "tool_calls", "message": {
            "tool_calls": [{"id": "probe", "function": {"name": "capability_probe", "arguments": '{"value":"ok"}'}}]}}]})
    monkeypatch.setattr(composition, "create_stateless_http_client",
        lambda **kwargs: create_stateless_http_client(transport=httpx.MockTransport(provider), **kwargs))
    engines = [create_async_engine(test_database.engine.url, pool_size=20,
        max_overflow=0).execution_options(schema_translate_map={None: test_database.schema}) for _ in range(2)]
    database = DatabaseResources(*engines, *(async_sessionmaker(engine, expire_on_commit=False) for engine in engines))
    async def database_resources(settings):
        return database
    monkeypatch.setattr(application.database, "create_database_resources", database_resources)
    app = application.create_app(configured(tmp_path))
    current = ContextVar("start_latency_request", default=None)
    timings = defaultdict(lambda: defaultdict(float))
    counts = defaultdict(lambda: defaultdict(int))

    def record(name, started):
        label = current.get()
        if label is not None:
            timings[label][name] += (perf_counter() - started) * 1000
            counts[label][name] += 1

    async with app.router.lifespan_context(app):
        principal, snapshot = await snapshot_for(app.state.execution, app.state.database, test_database.sessions)
        runtime = app.state.runtime
        wakeups = []
        monkeypatch.setattr(runtime.dispatcher, "wake", lambda key: wakeups.append(key))
        admission = getattr(runtime, "_admission", None)

        class TimedLock:
            async def __aenter__(self):
                started = perf_counter()
                await admission.acquire()
                record("admission_lock_wait", started)
                self.entered = perf_counter()
                return self

            async def __aexit__(self, *args):
                record("admission_lock_held", self.entered)
                admission.release()

        if admission is not None:
            monkeypatch.setattr(runtime, "_admission", TimedLock())

        def async_wrapper(original, name):
            async def wrapped(*args, **kwargs):
                started = perf_counter()
                try:
                    return await original(*args, **kwargs)
                finally:
                    record(name, started)
            return wrapped

        def sync_wrapper(original, name):
            def wrapped(*args, **kwargs):
                started = perf_counter()
                try:
                    return original(*args, **kwargs)
                finally:
                    record(name, started)
            return wrapped

        monkeypatch.setattr(RunService, "find_by_source", async_wrapper(RunService.find_by_source, "find_source"))
        monkeypatch.setattr(RunService, "start", async_wrapper(RunService.start, "owner_start"))
        encode_name = "_encode_with_dto" if hasattr(snapshot_codec, "_encode_with_dto") else "encode_snapshot"
        monkeypatch.setattr(snapshot_codec, encode_name, sync_wrapper(getattr(snapshot_codec, encode_name), "snapshot_encode"))
        monkeypatch.setattr(snapshot_codec, "decode_snapshot", sync_wrapper(snapshot_codec.decode_snapshot, "snapshot_decode"))
        pool = app.state.database.control_engine.pool
        monkeypatch.setattr(pool, "_do_get", sync_wrapper(pool._do_get, "pool_acquire"))
        def before_sql(connection, cursor, statement, parameters, context, executemany):
            context._latency_started = perf_counter()
        def after_sql(connection, cursor, statement, parameters, context, executemany):
            record("sql", context._latency_started)
        engine = app.state.database.control_engine.sync_engine
        event.listen(engine, "before_cursor_execute", before_sql)
        event.listen(engine, "after_cursor_execute", after_sql)
        rounds = []
        try:
            for round_number in range(3):
                async def start_one(index, *, round_number=round_number):
                    token = current.set(f"round-{round_number}-new-{index}")
                    try:
                        instance = replace(snapshot, workspace=replace(snapshot.workspace, run_id=uuid4()))
                        source = SourceIdentity("start_latency_fixture", principal.membership_id, f"{round_number}-{index}")
                        started = perf_counter()
                        result = await runtime.start(snapshot=instance, input=InputContent("x" * 4096), source=source)
                        record("total", started)
                        return instance, source, result
                    finally:
                        current.reset(token)
                beginning = perf_counter()
                results = await asyncio.gather(*(start_one(index) for index in range(50)))
                elapsed = (perf_counter() - beginning) * 1000
                assert all(result.created for _, _, result in results)
                assert runtime.dispatcher.admitted == 50
                assert runtime.dispatcher.active == 0
                instance, source, original = results[0]
                async def duplicate(index, *, round_number=round_number, instance=instance, source=source, original=original):
                    token = current.set(f"round-{round_number}-duplicate-{index}")
                    try:
                        started = perf_counter()
                        result = await runtime.start(snapshot=instance, input=InputContent("different retry body"), source=source)
                        record("total", started)
                        assert not result.created and result.run.id == original.run.id
                    finally:
                        current.reset(token)
                await asyncio.gather(*(duplicate(index) for index in range(50)))
                assert runtime.dispatcher.admitted == 50
                for _, _, result in results:
                    await runtime.cancel(tenant_id=principal.tenant_id, run_id=result.run.id)
                assert runtime.dispatcher.admitted == 0
                async with transaction(test_database.sessions) as tx:
                    assert (await RunService(tx).get(tenant_id=principal.tenant_id, run_id=original.run.id)).status == "Cancelled"
                round_result = {"round": round_number, "connection_phase": "first_burst" if round_number == 0 else "warm_burst",
                    "batch_elapsed_ms": elapsed}
                for kind in ("new", "duplicate"):
                    labels = [label for label in timings if label.startswith(f"round-{round_number}-{kind}-")]
                    names = set().union(*(timings[label] for label in labels))
                    round_result[kind] = {name: {**distribution([timings[label][name] for label in labels]),
                        "total_calls": sum(counts[label][name] for label in labels)} for name in sorted(names)}
                rounds.append(round_result)
            assert len(wakeups) == 150
        finally:
            event.remove(engine, "before_cursor_execute", before_sql)
            event.remove(engine, "after_cursor_execute", after_sql)
        print("START_LATENCY_DIAGNOSTIC=" + json.dumps({"scope": "intake-only, dispatcher wake disabled; local real PostgreSQL",
            "admission_lock": "instrumented" if admission is not None else "not_applicable_no_global_lock",
            "snapshot_encode_observation": encode_name,
            "control_pool_size": pool.size(), "rounds": rounds}, sort_keys=True))
