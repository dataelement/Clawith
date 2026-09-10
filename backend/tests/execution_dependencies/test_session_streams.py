import json
from dataclasses import replace
from uuid import uuid4

import pytest
from modules.session.test_session import accept, setup
from sqlalchemy import event

from app.execution_dependencies.session_streams import SessionExecutionStreams, StreamSubscription
from app.infrastructure.errors import AccessDenied, Conflict, NotFound
from app.modules.model.public import ModelStreamEvent
from app.modules.run.public import InputContent, RunKey, RunService, RunStreamEvent, SourceIdentity
from app.modules.session.public import SessionService
from execution_dependencies.test_resources import composed_database  # noqa: F401
from execution_dependencies.test_session_tools import started


def push(subscription, *, run="run", attempt=1, kind="model_event", text="x"):
    subscription.push(json.dumps({"type": "execution", "kind": kind, "text": text}), run_id=run,
        step_id="step", attempt=attempt, starts_attempt=kind == "attempt_started")


@pytest.mark.parametrize("large", [False, True])
def test_stream_overflow_discards_all_partial_attempts_and_is_bounded(large):
    subscription = StreamSubscription()
    push(subscription, kind="attempt_started")
    if large:
        push(subscription, text="x" * (256 * 1024))
    else:
        for _ in range(64):
            push(subscription)
    assert json.loads(subscription.pop())["type"] == "execution_resync"
    assert subscription.pop() is None
    push(subscription, run="other", kind="attempt_started")
    push(subscription, text="late invalid original attempt")
    assert "late invalid" not in subscription.pop()
    assert subscription.pop() is None
    push(subscription, attempt=2, kind="attempt_started")
    push(subscription, attempt=2, text="new valid delta")
    assert subscription.pop() and "new valid" in subscription.pop()
    subscription.close()
    assert subscription.closed and subscription.ready.is_set() and subscription.pop() is None


def test_attempt_registry_capacity_resynchronizes_instead_of_silently_forgetting_old_run():
    subscription = StreamSubscription()
    for index in range(256):
        push(subscription, run=str(index), kind="attempt_started")
        assert subscription.pop()
    push(subscription, run="new", kind="attempt_started")
    assert json.loads(subscription.pop())["type"] == "execution_resync"
    assert subscription.pop()
    push(subscription, run="0", text="old partial attempt")
    assert subscription.pop() is None


async def test_stream_routes_authorized_session_only_and_reuses_immutable_run_binding(test_database, transaction_factory, composed_database):  # noqa: F811
    p, session = await setup(transaction_factory)
    receipt = await accept(transaction_factory, p, session)
    run = await started(transaction_factory, p, session, receipt)
    async with transaction_factory() as tx:
        other = await SessionService(tx).create(p, agent_id=session.agent_id)
    streams = SessionExecutionStreams(composed_database[0])
    own = await streams.subscribe(p, session_id=session.id)
    unrelated = await streams.subscribe(p, session_id=other.id)
    with pytest.raises((AccessDenied, NotFound)):
        await streams.subscribe(replace(p, membership_id=uuid4()), session_id=session.id)
    statements = []
    def observe_sql(*args):
        statements.append(args[2])
    event.listen(test_database.engine.sync_engine, "before_cursor_execute", observe_sql)
    try:
        key = RunKey(p.tenant_id, session.agent_id, run.id)
        await streams.observe(key, RunStreamEvent("step", 1, "attempt_started"))
        initial_reads = len(statements)
        assert initial_reads > 0
        for _ in range(10):
            await streams.observe(key, RunStreamEvent("step", 1, "model_event", ModelStreamEvent("text", "delta")))
        assert len(statements) == initial_reads
        assert own.ready.is_set() and unrelated.pop() is None
        assert json.loads(own.pop())["run_id"] == str(run.id)
        other_run = uuid4()
        async with transaction_factory() as tx:
            original = await RunService(tx).read_snapshot(tenant_id=p.tenant_id, run_id=run.id)
            await RunService(tx).start(tenant_id=p.tenant_id, agent_id=run.agent_id, run_id=other_run,
                snapshot=replace(original, workspace=replace(original.workspace, run_id=other_run)),
                input=InputContent("autonomous input"), source=SourceIdentity("trigger", uuid4(), "occurrence"))
        other_key = RunKey(p.tenant_id, run.agent_id, other_run)
        await streams.observe(other_key, RunStreamEvent("other", 1, "attempt_started"))
        negative_reads = len(statements)
        for _ in range(10):
            await streams.observe(other_key, RunStreamEvent("other", 1, "model_event", ModelStreamEvent("text", "must not route")))
        assert len(statements) == negative_reads and streams._routes[other_key] is None
        streams.unsubscribe(p, session_id=session.id, subscription=own)
        assert own.closed and streams.subscriptions == 1
        await streams.close()
        assert unrelated.closed and unrelated.ready.is_set() and streams.subscriptions == 0
    finally:
        event.remove(test_database.engine.sync_engine, "before_cursor_execute", observe_sql)
        await streams.close()


async def test_subscription_limit_and_close_are_enforced_without_unbounded_waiters(transaction_factory, composed_database):  # noqa: F811
    p, session = await setup(transaction_factory)
    streams = SessionExecutionStreams(composed_database[0])
    try:
        subscriptions = [await streams.subscribe(p, session_id=session.id) for _ in range(200)]
        assert streams.subscriptions == 200
        with pytest.raises(Conflict):
            await streams.subscribe(p, session_id=session.id)
    finally:
        await streams.close()
    assert streams.subscriptions == 0 and all(item.closed and item.ready.is_set() for item in subscriptions)
    with pytest.raises(Conflict):
        await streams.subscribe(p, session_id=session.id)
