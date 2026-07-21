"""Focused database-chain tests for the Agent delegation cycle guard."""

from collections import deque
import inspect
from types import SimpleNamespace
import uuid

import pytest
from sqlalchemy.dialects import postgresql

from app.services.agent_runtime import cycle_guard


class _Result:
    def __init__(self, row):
        self.row = row

    def one_or_none(self):
        return self.row


class _FakeSession:
    def __init__(self, *rows):
        self.rows = deque(rows)
        self.statements = []

    async def execute(self, statement):
        self.statements.append(statement)
        if not self.rows:
            raise AssertionError("unexpected database execute")
        return _Result(self.rows.popleft())

    async def commit(self):
        raise AssertionError("cycle guard must not commit the caller transaction")

    async def rollback(self):
        raise AssertionError("cycle guard must not roll back the caller transaction")


def _run(
    *,
    tenant_id: uuid.UUID,
    agent_id: uuid.UUID | None,
    run_kind: str,
    origin_agent_id: uuid.UUID | None = None,
    parent_run_id: uuid.UUID | None = None,
    root_run_id: uuid.UUID | None = None,
    system_role: str | None = None,
    run_id: uuid.UUID | None = None,
):
    return SimpleNamespace(
        id=run_id or uuid.uuid4(),
        tenant_id=tenant_id,
        run_kind=run_kind,
        agent_id=agent_id,
        origin_agent_id=origin_agent_id,
        parent_run_id=parent_run_id,
        root_run_id=root_run_id,
        system_role=system_role,
    )


def _agent_chain(tenant_id: uuid.UUID, agents: list[uuid.UUID]):
    """Build root + delegated children and return database lookup order."""
    root = _run(
        tenant_id=tenant_id,
        agent_id=agents[0],
        run_kind="foreground",
    )
    chronological = [root]
    parent = root
    for source_agent, target_agent in zip(agents, agents[1:]):
        child = _run(
            tenant_id=tenant_id,
            agent_id=target_agent,
            origin_agent_id=source_agent,
            run_kind="delegated",
            parent_run_id=parent.id,
            root_run_id=root.id,
        )
        chronological.append(child)
        parent = child
    return chronological, list(reversed(chronological))


@pytest.mark.asyncio
async def test_normal_a_b_c_chain_continues_without_a_cycle():
    tenant_id = uuid.uuid4()
    agent_a, agent_b, agent_c, agent_d = [uuid.uuid4() for _ in range(4)]
    chronological, lookup_order = _agent_chain(
        tenant_id,
        [agent_a, agent_b, agent_c],
    )
    db = _FakeSession(*lookup_order)

    result = await cycle_guard.AgentCycleGuard().ensure_delegation_allowed(
        db,
        tenant_id=tenant_id,
        source_run_id=chronological[-1].id,
        source_agent_id=agent_c,
        target_agent_id=agent_d,
    )

    assert result.cycle_count == 0
    assert result.ancestor_depth == 3
    assert {(edge.source_agent_id, edge.target_agent_id, edge.count) for edge in result.edge_counts} == {
        (agent_a, agent_b, 1),
        (agent_b, agent_c, 1),
        (agent_c, agent_d, 1),
    }


@pytest.mark.asyncio
async def test_a_b_a_b_counts_as_one_repeated_directed_edge():
    tenant_id = uuid.uuid4()
    agent_a, agent_b, agent_c = [uuid.uuid4() for _ in range(3)]
    chronological, lookup_order = _agent_chain(
        tenant_id,
        [agent_a, agent_b, agent_a, agent_b],
    )

    result = await cycle_guard.AgentCycleGuard().ensure_delegation_allowed(
        _FakeSession(*lookup_order),
        tenant_id=tenant_id,
        source_run_id=chronological[-1].id,
        source_agent_id=agent_b,
        target_agent_id=agent_c,
    )

    assert result.cycle_count == 1
    counts = {(edge.source_agent_id, edge.target_agent_id): edge.count for edge in result.edge_counts}
    assert counts[(agent_a, agent_b)] == 2
    assert counts[(agent_b, agent_a)] == 1


@pytest.mark.asyncio
async def test_candidate_reaching_cycle_limit_five_is_rejected():
    tenant_id = uuid.uuid4()
    agent_a, agent_b = uuid.uuid4(), uuid.uuid4()
    chronological, lookup_order = _agent_chain(
        tenant_id,
        [agent_a, agent_b, agent_a, agent_b, agent_a, agent_b, agent_a],
    )

    with pytest.raises(cycle_guard.AgentCycleGuardError) as exc_info:
        await cycle_guard.AgentCycleGuard().ensure_delegation_allowed(
            _FakeSession(*lookup_order),
            tenant_id=tenant_id,
            source_run_id=chronological[-1].id,
            source_agent_id=agent_a,
            target_agent_id=agent_b,
        )

    assert exc_info.value.code == "agent_cycle_limit_reached"
    assert "5 >= 5" in str(exc_info.value)


@pytest.mark.asyncio
async def test_human_and_planning_ancestors_do_not_add_edges():
    tenant_id = uuid.uuid4()
    agent_a, agent_b, agent_c = [uuid.uuid4() for _ in range(3)]
    planning = _run(
        tenant_id=tenant_id,
        agent_id=None,
        run_kind="orchestration",
        system_role="group_planning",
    )
    initial_agent_step = _run(
        tenant_id=tenant_id,
        agent_id=agent_a,
        run_kind="foreground",
        parent_run_id=planning.id,
        root_run_id=planning.id,
    )
    source = _run(
        tenant_id=tenant_id,
        agent_id=agent_b,
        origin_agent_id=agent_a,
        run_kind="delegated",
        parent_run_id=initial_agent_step.id,
        root_run_id=planning.id,
    )

    result = await cycle_guard.AgentCycleGuard().ensure_delegation_allowed(
        _FakeSession(source, initial_agent_step, planning),
        tenant_id=tenant_id,
        source_run_id=source.id,
        source_agent_id=agent_b,
        target_agent_id=agent_c,
    )

    assert result.cycle_count == 0
    assert {(edge.source_agent_id, edge.target_agent_id) for edge in result.edge_counts} == {
        (agent_a, agent_b),
        (agent_b, agent_c),
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("missing_field", ["origin_agent_id", "agent_id"])
async def test_delegated_ancestor_missing_agent_identity_fails_closed(missing_field):
    tenant_id = uuid.uuid4()
    agent_a, agent_b, agent_c = [uuid.uuid4() for _ in range(3)]
    source_values = {
        "tenant_id": tenant_id,
        "agent_id": agent_b,
        "origin_agent_id": agent_a,
        "run_kind": "delegated",
    }
    source_values[missing_field] = None
    source = _run(**source_values)

    with pytest.raises(cycle_guard.AgentCycleGuardError) as exc_info:
        await cycle_guard.AgentCycleGuard().ensure_delegation_allowed(
            _FakeSession(source),
            tenant_id=tenant_id,
            source_run_id=source.id,
            source_agent_id=agent_b,
            target_agent_id=agent_c,
        )

    assert exc_info.value.code == "agent_cycle_chain_invalid"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("source_agent_id", "target_agent_id"),
    [(None, uuid.uuid4()), (uuid.uuid4(), None)],
)
async def test_candidate_missing_agent_identity_fails_before_reading_the_chain(
    source_agent_id,
    target_agent_id,
):
    db = _FakeSession()

    with pytest.raises(cycle_guard.AgentCycleGuardError) as exc_info:
        await cycle_guard.AgentCycleGuard().ensure_delegation_allowed(
            db,
            tenant_id=uuid.uuid4(),
            source_run_id=uuid.uuid4(),
            source_agent_id=source_agent_id,
            target_agent_id=target_agent_id,
        )

    assert exc_info.value.code == "agent_cycle_chain_invalid"
    assert db.statements == []


@pytest.mark.asyncio
async def test_broken_or_cross_tenant_parent_chain_fails_closed():
    tenant_id = uuid.uuid4()
    agent_a, agent_b, agent_c = [uuid.uuid4() for _ in range(3)]
    missing_parent_id = uuid.uuid4()
    source = _run(
        tenant_id=tenant_id,
        agent_id=agent_b,
        origin_agent_id=agent_a,
        run_kind="delegated",
        parent_run_id=missing_parent_id,
    )
    db = _FakeSession(source, None)

    with pytest.raises(cycle_guard.AgentCycleGuardError) as exc_info:
        await cycle_guard.AgentCycleGuard().ensure_delegation_allowed(
            db,
            tenant_id=tenant_id,
            source_run_id=source.id,
            source_agent_id=agent_b,
            target_agent_id=agent_c,
        )

    assert exc_info.value.code == "agent_cycle_chain_invalid"
    parent_sql = str(
        db.statements[-1].compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )
    assert f"agent_runs.tenant_id = '{tenant_id}'" in parent_sql
    assert f"agent_runs.id = '{missing_parent_id}'" in parent_sql


@pytest.mark.asyncio
async def test_delegated_origin_must_match_the_parent_run_agent():
    tenant_id = uuid.uuid4()
    agent_a, agent_b, unrelated_agent = [uuid.uuid4() for _ in range(3)]
    parent = _run(
        tenant_id=tenant_id,
        agent_id=unrelated_agent,
        run_kind="foreground",
    )
    source = _run(
        tenant_id=tenant_id,
        agent_id=agent_b,
        origin_agent_id=agent_a,
        run_kind="delegated",
        parent_run_id=parent.id,
    )

    with pytest.raises(cycle_guard.AgentCycleGuardError) as exc_info:
        await cycle_guard.AgentCycleGuard().ensure_delegation_allowed(
            _FakeSession(source, parent),
            tenant_id=tenant_id,
            source_run_id=source.id,
            source_agent_id=agent_b,
            target_agent_id=uuid.uuid4(),
        )

    assert exc_info.value.code == "agent_cycle_chain_invalid"
    assert "delegated child origin" in str(exc_info.value)


@pytest.mark.asyncio
async def test_parent_run_cycle_fails_before_reloading_a_visited_run():
    tenant_id = uuid.uuid4()
    agent_a, agent_b = uuid.uuid4(), uuid.uuid4()
    source_id, parent_id = uuid.uuid4(), uuid.uuid4()
    source = _run(
        tenant_id=tenant_id,
        run_id=source_id,
        agent_id=agent_b,
        origin_agent_id=agent_a,
        run_kind="delegated",
        parent_run_id=parent_id,
    )
    parent = _run(
        tenant_id=tenant_id,
        run_id=parent_id,
        agent_id=agent_a,
        origin_agent_id=agent_b,
        run_kind="delegated",
        parent_run_id=source_id,
    )
    db = _FakeSession(source, parent)

    with pytest.raises(cycle_guard.AgentCycleGuardError) as exc_info:
        await cycle_guard.AgentCycleGuard().ensure_delegation_allowed(
            db,
            tenant_id=tenant_id,
            source_run_id=source.id,
            source_agent_id=agent_b,
            target_agent_id=agent_a,
        )

    assert exc_info.value.code == "agent_cycle_chain_invalid"
    assert "parent cycle" in str(exc_info.value)
    assert len(db.statements) == 2


@pytest.mark.asyncio
async def test_ancestor_depth_limit_fails_closed_on_bad_data():
    tenant_id = uuid.uuid4()
    agents = [uuid.uuid4() for _ in range(4)]
    chronological, lookup_order = _agent_chain(tenant_id, agents)
    db = _FakeSession(*lookup_order)

    with pytest.raises(cycle_guard.AgentCycleGuardError) as exc_info:
        await cycle_guard.AgentCycleGuard(max_ancestor_depth=2).ensure_delegation_allowed(
            db,
            tenant_id=tenant_id,
            source_run_id=chronological[-1].id,
            source_agent_id=agents[-1],
            target_agent_id=uuid.uuid4(),
        )

    assert exc_info.value.code == "agent_cycle_chain_invalid"
    assert "depth limit" in str(exc_info.value)
    assert len(db.statements) == 2


@pytest.mark.asyncio
async def test_each_check_reloads_the_parent_chain_from_database():
    tenant_id = uuid.uuid4()
    agent_a, agent_b, agent_c = [uuid.uuid4() for _ in range(3)]
    chronological, lookup_order = _agent_chain(tenant_id, [agent_a, agent_b])
    db = _FakeSession(*lookup_order, *lookup_order)
    guard = cycle_guard.AgentCycleGuard()

    for _ in range(2):
        result = await guard.ensure_delegation_allowed(
            db,
            tenant_id=tenant_id,
            source_run_id=chronological[-1].id,
            source_agent_id=agent_b,
            target_agent_id=agent_c,
        )
        assert result.cycle_count == 0

    assert len(db.statements) == 4


def test_cycle_formula_sums_repeats_per_directed_edge():
    agent_a, agent_b, agent_c = [uuid.uuid4() for _ in range(3)]
    edges = [
        (agent_a, agent_b),
        (agent_b, agent_a),
        (agent_a, agent_b),
        (agent_b, agent_a),
        (agent_a, agent_b),
        (agent_b, agent_c),
    ]

    assert cycle_guard.count_agent_cycles(edges) == 3


def test_cycle_guard_query_has_no_execution_projection_dependency():
    source = inspect.getsource(cycle_guard)

    assert "projected_" not in source
    assert "Counter(" in source
    assert "AgentRun.parent_run_id" in source
