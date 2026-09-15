"""Independent lifecycle ordering and committed Goal admission failure regressions."""

from execution_dependencies.test_resources import composed_database, configured  # noqa: F401
from modules.session.test_goal import complete, decision, goal_setup
from sqlalchemy import text

from app.application import create_app
from app.execution_dependencies.channel_inputs import ChannelInputs
from app.execution_dependencies.goal_inputs import GoalInputs
from app.execution_dependencies.other_product_inputs import OtherProductInputs
from app.execution_dependencies.scheduled_inputs import ScheduledInputs
from app.infrastructure.transactions import transaction
from app.modules.agent.public import AgentService
from app.modules.run.public import RunRuntime
from app.modules.session.public import SessionService


async def test_real_lifespan_stops_all_input_producers_before_runtime(
        composed_database, tmp_path, monkeypatch):  # noqa: F811
    events = []
    instances = {}
    original_channel_start = ChannelInputs.startup
    original_runtime_close = RunRuntime.close

    async def channel_start(self):
        assert self.products.runtime._accepting
        events.append("channel-start-runtime-ready")
        instances["channel"] = self
        await original_channel_start(self)

    def wrap_close(owner, label):
        original = owner.close
        async def close(self):
            await original(self)
            instances[label] = self
            events.append(label + "-closed")
        monkeypatch.setattr(owner, "close", close)

    for owner, label in ((ChannelInputs, "channel"), (OtherProductInputs, "other"),
            (GoalInputs, "goal"), (ScheduledInputs, "scheduled")):
        wrap_close(owner, label)

    async def runtime_close(self):
        assert {"channel-closed", "other-closed", "goal-closed", "scheduled-closed"} <= set(events)
        assert instances["channel"]._supervisor.done() and not instances["channel"]._listeners
        assert all(instances[name]._task.done() for name in ("channel", "other", "goal", "scheduled"))
        events.append("runtime-close")
        await original_runtime_close(self)

    monkeypatch.setattr(ChannelInputs, "startup", channel_start)
    monkeypatch.setattr(RunRuntime, "close", runtime_close)
    app = create_app(configured(tmp_path))
    async with app.router.lifespan_context(app):
        assert app.state.runtime._accepting
    assert events[0] == "channel-start-runtime-ready" and events[-1] == "runtime-close"


async def test_real_sql_failure_after_goal_claim_stops_goal_and_marks_link_failed(
        test_database, transaction_factory, composed_database, tmp_path, monkeypatch):  # noqa: F811
    app = create_app(configured(tmp_path))
    async with app.router.lifespan_context(app):
        await app.state.products.goal.close()
        principal, session, _, run = await goal_setup(transaction_factory)
        await complete(transaction_factory, run, decision("continue"))
        async with transaction_factory() as tx:
            goal = await SessionService(tx).get_goal(principal, session_id=session.id)
        assert goal.enabled and goal.due_at is not None

        async def fail_with_real_sql(self, *, tenant_id, agent_id):
            # Separate connection proves the admission claim committed before the failing query.
            async with transaction(test_database.sessions) as tx:
                observed = await SessionService(tx).get_goal(principal, session_id=session.id)
                assert observed.enabled and observed.due_at is None
            await self._repository._session.execute(text("SELECT 1 / 0"))
            raise AssertionError("PostgreSQL division by zero must fail")

        monkeypatch.setattr(AgentService, "get_for_agent_execution", fail_with_real_sql)
        await app.state.products.goal._start_goal(goal)
        async with transaction_factory() as tx:
            service = SessionService(tx)
            stopped = await service.get_goal(principal, session_id=session.id)
            link = await service.get_link(principal, session_id=session.id, link_id=goal.current_link_id)
            assert not stopped.enabled and stopped.stopped_reason == "admission_failed"
            assert link.admission == "failed" and link.admission_error == "persistence_failure" and link.run_id is None
