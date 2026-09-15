"""Goal configuration and ordinary Main associations; no scheduler or new Goal identity."""

import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from modules.run.test_lifecycle import snapshot
from modules.session.test_session import accept, setup, start
from sqlalchemy import select, update

from app.infrastructure.errors import Conflict, InvalidInput
from app.modules.model.public import ModelStepResult, ModelUsage
from app.modules.run.public import ModelStepPayload, RunService, SourceIdentity
from app.modules.session.models import SessionRecord, SessionRunLinkRecord
from app.modules.session.public import SessionConsumers, SessionService


async def goal_setup(factory):
    principal, session = await setup(factory)
    receipt = await accept(factory, principal, session)
    async with factory() as tx:
        goal = await SessionService(tx).enable_goal(principal, session_id=session.id, input_id=receipt.entry.id, objective="Finish research")
        assert goal.current_link_id == receipt.link.id
    run = await start(factory, principal, session, receipt)
    return principal, session, receipt, run


async def complete(factory, run, text):
    async with factory() as tx:
        service = RunService(tx)
        boundary = (await service.get(tenant_id=run.tenant_id, run_id=run.id)).latest_history_sequence
        await service.record_model_step(tenant_id=run.tenant_id, run_id=run.id,
            payload=ModelStepPayload("final", boundary, ModelStepResult(text, (), "stop", ModelUsage(), "final", False)))
        await service.complete(tenant_id=run.tenant_id, run_id=run.id, step_id="final", output=text, consumer=SessionConsumers())


def decision(disposition, wake_at=None):
    return json.dumps({"goal": {"disposition": disposition, "progress": "Sources reviewed", "wake_at": wake_at}})


async def test_continue_commits_progress_and_next_original_input_association(transaction_factory):
    boot = datetime.now(UTC)
    p, session, receipt, run = await goal_setup(transaction_factory)
    await complete(transaction_factory, run, decision("continue"))
    async with transaction_factory() as tx:
        service = SessionService(tx)
        goal = await service.get_goal(p, session_id=session.id)
        assert goal.enabled and goal.progress == "Sources reviewed" and goal.current_link_id != receipt.link.id
        context = await service.get_goal_context(tenant_id=p.tenant_id, session_id=session.id)
        assert context.input.id == receipt.entry.id
        assert context.link.history_cutoff == receipt.link.history_cutoff
        due = await service.goal_due(now=datetime.now(UTC), not_before=boot)
        assert [item.session_id for item in due.goals] == [session.id]
        claimed = await service.prepare_goal_admission(tenant_id=p.tenant_id, session_id=session.id,
            expected_link_id=goal.current_link_id, now=datetime.now(UTC))
        assert claimed.id == goal.current_link_id
        assert await service.prepare_goal_admission(tenant_id=p.tenant_id, session_id=session.id,
            expected_link_id=goal.current_link_id, now=datetime.now(UTC)) is None
    run_id = uuid4()
    async with transaction_factory() as tx:
        started = await RunService(tx).start(tenant_id=p.tenant_id, agent_id=session.agent_id, run_id=run_id,
            snapshot=snapshot(p.tenant_id, session.agent_id, run_id), input=context.input.content,
            source=SourceIdentity("session", session.id, str(claimed.id)), start_consumer=SessionConsumers())
        assert started.created
        assert len((await SessionService(tx).read_history(p, session_id=session.id)).entries) == 1


async def test_timed_wait_is_future_and_old_process_due_is_not_replayed(transaction_factory):
    boot = datetime.now(UTC)
    p, session, _, run = await goal_setup(transaction_factory)
    wake = datetime.now(UTC) + timedelta(minutes=1)
    await complete(transaction_factory, run, decision("wait", wake.isoformat()))
    async with transaction_factory() as tx:
        service = SessionService(tx)
        assert not (await service.goal_due(now=datetime.now(UTC), not_before=boot)).goals
        assert (await service.goal_due(now=wake + timedelta(seconds=1), not_before=boot)).goals
    restart = datetime.now(UTC) + timedelta(seconds=1)
    await accept(transaction_factory, p, session, "unrelated", "new conversation activity")
    async with transaction_factory() as tx:
        assert not (await SessionService(tx).goal_due(now=wake + timedelta(seconds=1), not_before=restart)).goals


@pytest.mark.parametrize("body,reason", [(decision("achieved"), "achieved"), ("not json", "malformed_goal_result"),
    (decision("wait"), "malformed_goal_result"), (decision("wait", "2000-01-01T00:00:00+00:00"), "malformed_goal_result")])
async def test_achieved_and_malformed_dispositions_stop_without_blocking_run_settlement(transaction_factory, body, reason):
    p, session, _, run = await goal_setup(transaction_factory)
    await complete(transaction_factory, run, body)
    async with transaction_factory() as tx:
        goal = await SessionService(tx).get_goal(p, session_id=session.id)
        assert not goal.enabled and goal.stopped_reason == reason
        assert (await RunService(tx).get(tenant_id=p.tenant_id, run_id=run.id)).status == "Completed"
        assert len((await tx.session.scalars(select(SessionRunLinkRecord))).all()) == 1


@pytest.mark.parametrize("status", ["Failed", "Interrupted", "Cancelled"])
async def test_terminal_failure_stops_automatic_goal_without_new_iteration(transaction_factory, status):
    p, session, _, run = await goal_setup(transaction_factory)
    async with transaction_factory() as tx:
        await RunService(tx).terminate(tenant_id=p.tenant_id, run_id=run.id, status=status, reason="failure", consumer=SessionConsumers())
    async with transaction_factory() as tx:
        goal = await SessionService(tx).get_goal(p, session_id=session.id)
        assert not goal.enabled and goal.stopped_reason == status.lower()
        assert len((await tx.session.scalars(select(SessionRunLinkRecord))).all()) == 1


async def test_failed_goal_admission_stops_and_preserves_pending_input(transaction_factory):
    p, session, receipt, run = await goal_setup(transaction_factory)
    await complete(transaction_factory, run, decision("continue"))
    async with transaction_factory() as tx:
        service = SessionService(tx)
        goal = await service.get_goal(p, session_id=session.id)
        await service.prepare_goal_admission(tenant_id=p.tenant_id, session_id=session.id,
            expected_link_id=goal.current_link_id, now=datetime.now(UTC))
        await service.fail_goal_admission(tenant_id=p.tenant_id, session_id=session.id, expected_link_id=goal.current_link_id, reason="capacity")
        stopped = await service.get_goal(p, session_id=session.id)
        link = await service.get_link(p, session_id=session.id, link_id=goal.current_link_id)
        assert not stopped.enabled and link.admission == "failed" and link.input_id == receipt.entry.id


async def test_cancelled_pending_goal_cannot_start_after_cancel(transaction_factory):
    p, session = await setup(transaction_factory)
    receipt = await accept(transaction_factory, p, session)
    async with transaction_factory() as tx:
        service = SessionService(tx)
        await service.enable_goal(p, session_id=session.id, input_id=receipt.entry.id, objective="work")
        cancelled = await service.cancel_goal(p, session_id=session.id)
        assert cancelled.active_run_id is None and not cancelled.goal.enabled
    with pytest.raises(Conflict):
        await start(transaction_factory, p, session, receipt)


async def test_human_source_cannot_collide_with_goal_iteration_key(transaction_factory):
    p, session, receipt, run = await goal_setup(transaction_factory)
    malicious = await accept(transaction_factory, p, session, "goal:" + str(receipt.entry.id) + ":" + str(run.id))
    assert malicious.link.source_key.startswith("input:")
    await complete(transaction_factory, run, decision("continue"))
    async with transaction_factory() as tx:
        goal = await SessionService(tx).get_goal_context(tenant_id=p.tenant_id, session_id=session.id)
        assert goal.input.id == receipt.entry.id and goal.link.id != malicious.link.id


async def test_initial_goal_admission_failure_does_not_leave_enabled_hanging_goal(transaction_factory):
    p, session = await setup(transaction_factory)
    receipt = await accept(transaction_factory, p, session)
    async with transaction_factory() as tx:
        service = SessionService(tx)
        await service.enable_goal(p, session_id=session.id, input_id=receipt.entry.id, objective="work")
        await service.admission_failed(p, session_id=session.id, link_id=receipt.link.id, reason="model unavailable")
        stopped = await service.get_goal(p, session_id=session.id)
        assert not stopped.enabled and stopped.stopped_reason == "admission_failed"


async def test_goal_waiting_does_not_consume_or_create_an_iteration(transaction_factory):
    from modules.session.test_session import model_step

    from app.modules.run.public import WaitingPayload
    p, session, receipt, run = await goal_setup(transaction_factory)
    boundary = await model_step(transaction_factory, run, name="need_input")
    async with transaction_factory() as tx:
        await RunService(tx).wait(tenant_id=p.tenant_id, run_id=run.id,
            payload=WaitingPayload("step", "wait", "Question?", boundary), waiting_consumer=SessionConsumers())
        goal = await SessionService(tx).get_goal(p, session_id=session.id)
        assert goal.enabled and goal.current_link_id == receipt.link.id and goal.due_at is None


async def test_goal_progress_is_atomic_with_terminal_settlement(transaction_factory):
    p, session, receipt, run = await goal_setup(transaction_factory)
    class Reject(SessionConsumers):
        async def record_outcome(self, transaction, **kwargs):
            await super().record_outcome(transaction, **kwargs)
            raise RuntimeError("rollback Goal and outcome")
    text = decision("continue")
    async with transaction_factory() as tx:
        await RunService(tx).record_model_step(tenant_id=p.tenant_id, run_id=run.id,
            payload=ModelStepPayload("final", 1, ModelStepResult(text, (), "stop", ModelUsage(), "final", False)))
    with pytest.raises(RuntimeError):
        async with transaction_factory() as tx:
            await RunService(tx).complete(tenant_id=p.tenant_id, run_id=run.id, step_id="final", output=text, consumer=Reject())
    async with transaction_factory() as tx:
        goal = await SessionService(tx).get_goal(p, session_id=session.id)
        assert goal.enabled and goal.progress == "" and goal.current_link_id == receipt.link.id
        assert len((await tx.session.scalars(select(SessionRunLinkRecord))).all()) == 1


async def test_null_character_goal_result_stops_instead_of_failing_sql_settlement(transaction_factory):
    p, session, _, run = await goal_setup(transaction_factory)
    await complete(transaction_factory, run, json.dumps({"goal": {"disposition": "continue", "progress": "bad\x00progress", "wake_at": None}}))
    async with transaction_factory() as tx:
        goal = await SessionService(tx).get_goal(p, session_id=session.id)
        assert not goal.enabled and goal.stopped_reason == "malformed_goal_result"


async def test_goal_start_consumer_rejects_unclaimed_future_iteration(transaction_factory):
    p, session, _, run = await goal_setup(transaction_factory)
    await complete(transaction_factory, run, decision("wait", (datetime.now(UTC) + timedelta(minutes=1)).isoformat()))
    async with transaction_factory() as tx:
        context = await SessionService(tx).get_goal_context(tenant_id=p.tenant_id, session_id=session.id)
    child = uuid4()
    with pytest.raises(Conflict):
        async with transaction_factory() as tx:
            await RunService(tx).start(tenant_id=p.tenant_id, agent_id=session.agent_id, run_id=child,
                snapshot=snapshot(p.tenant_id, session.agent_id, child), input=context.input.content,
                source=SourceIdentity("session", session.id, str(context.link.id)), start_consumer=SessionConsumers())


async def test_invalid_goal_does_not_block_unrelated_input_start_failure_or_terminal(transaction_factory):
    principal, session, _, _ = await goal_setup(transaction_factory)
    async with transaction_factory() as tx:
        await tx.session.execute(update(SessionRecord).where(SessionRecord.id == session.id).values(goal_configuration_version=2))
    ordinary = await accept(transaction_factory, principal, session, "ordinary", "ordinary question")
    async with transaction_factory() as tx:
        service = SessionService(tx)
        assert await service.get_goal(principal, session_id=session.id, expected_input_id=ordinary.entry.id) is None
        with pytest.raises(InvalidInput):
            await service.get_goal(principal, session_id=session.id)
    run = await start(transaction_factory, principal, session, ordinary)
    await complete(transaction_factory, run, "ordinary final")
    failed = await accept(transaction_factory, principal, session, "failed", "another question")
    async with transaction_factory() as tx:
        await SessionService(tx).admission_failed(principal, session_id=session.id, link_id=failed.link.id, reason="capacity")
        assert (await RunService(tx).get(tenant_id=principal.tenant_id, run_id=run.id)).status == "Completed"


@pytest.mark.parametrize("corruption", ["version", "oversized"])
async def test_due_scan_reports_invalid_goal_and_advances_to_other_sessions(transaction_factory, corruption):
    boot = datetime.now(UTC)
    _, bad_session, _, bad_run = await goal_setup(transaction_factory)
    await complete(transaction_factory, bad_run, decision("continue"))
    _, good_session, _, good_run = await goal_setup(transaction_factory)
    await complete(transaction_factory, good_run, decision("continue"))
    async with transaction_factory() as tx:
        bad = await tx.session.get(SessionRecord, bad_session.id)
        if corruption == "version":
            bad.goal_configuration_version = 2
        else:
            bad.goal_configuration = {**bad.goal_configuration, "progress": "x" * 70000}
    async with transaction_factory() as tx:
        service = SessionService(tx)
        first = await service.goal_due(now=datetime.now(UTC), not_before=boot, limit=1)
        second = await service.goal_due(now=datetime.now(UTC), not_before=boot, after_session_id=first.next_after_id, limit=1)
        assert first.has_more and not second.has_more
        assert [goal.session_id for page in (first, second) for goal in page.goals] == [good_session.id]
        assert [identity for page in (first, second) for identity in page.invalid_session_ids] == [bad_session.id]
