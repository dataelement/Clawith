from uuid import UUID, uuid4

import pytest

from app.runtime.scheduler import FairReadyQueue, RunKey


def run(tenant: int, agent: int, identity: int) -> RunKey:
    return RunKey(UUID(int=tenant), UUID(int=agent), UUID(int=identity))


def test_rotation_is_tenant_then_agent_then_fifo_run():
    queue = FairReadyQueue(capacity=6)
    a1, a2, a3 = run(1, 11, 111), run(1, 11, 112), run(1, 12, 121)
    b1, b2, b3 = run(2, 21, 211), run(2, 21, 212), run(2, 22, 221)
    for value in (a1, a2, a3, b1, b2, b3):
        assert queue.enqueue(value)
    assert [queue.take() for _ in range(6)] == [a1, b1, a3, b3, a2, b2]
    assert queue.take() is None
    assert not queue._tenants
    assert len(queue) == 0


def test_quantum_reentry_rotates_agents_and_runs_without_completion():
    queue = FairReadyQueue(capacity=3)
    a1, a2, b1 = run(1, 11, 111), run(1, 11, 112), run(1, 12, 121)
    for value in (a1, a2, b1):
        queue.enqueue(value)
    observed = []
    for _ in range(8):
        selected = queue.take()
        observed.append(selected)
        assert selected is not None
        queue.enqueue(selected)
    assert observed == [a1, b1, a2, b1, a1, b1, a2, b1]
    assert len(queue) == 3


@pytest.mark.parametrize("initial_allocations", [0, 1, 17, 50, 71])
def test_fifty_nonterminating_a_runs_cannot_delay_b_beyond_second_allocation(initial_allocations):
    queue = FairReadyQueue(capacity=51)
    for index in range(50):
        queue.enqueue(run(1, index + 100, index + 1000))
    for _ in range(initial_allocations):
        selected = queue.take()
        assert selected is not None
        queue.enqueue(selected)
    b = run(2, 200, 2000)
    queue.enqueue(b)
    after_ready = []
    for _ in range(2):
        selected = queue.take()
        assert selected is not None
        after_ready.append(selected)
        queue.enqueue(selected)
    assert b in after_ready
    assert len(queue) == 51


def test_duplicate_wakes_do_not_reorder_or_use_capacity():
    queue = FairReadyQueue(capacity=2)
    first, second = run(1, 1, 1), run(1, 1, 2)
    queue.enqueue(first)
    queue.enqueue(second)
    for _ in range(100):
        assert not queue.enqueue(first)
    assert len(queue) == 2
    assert queue.take() == first
    assert queue.take() == second


@pytest.mark.parametrize("changed", [run(2, 1, 1), run(1, 2, 1)])
def test_duplicate_run_cannot_change_owner_even_when_full(changed):
    queue = FairReadyQueue(capacity=1)
    original = run(1, 1, 1)
    queue.enqueue(original)
    with pytest.raises(ValueError, match="ownership"):
        queue.enqueue(changed)
    assert queue.take() == original
    assert queue.take() is None


def test_capacity_overflow_does_not_drop_or_reorder_and_discard_frees_space():
    queue = FairReadyQueue(capacity=2)
    first, second, third = run(1, 1, 1), run(2, 2, 2), run(3, 3, 3)
    queue.enqueue(first)
    queue.enqueue(second)
    with pytest.raises(OverflowError):
        queue.enqueue(third)
    assert len(queue) == 2
    assert not queue.discard(uuid4())
    assert queue.discard(first.run_id)
    assert first.tenant_id not in queue._tenants
    assert not queue.discard(first.run_id)
    queue.enqueue(third)
    assert queue.take() == second
    assert queue.take() == third


def test_discard_prunes_only_empty_branches_and_preserves_rotation():
    queue = FairReadyQueue(capacity=3)
    first, second, third = run(1, 11, 1), run(1, 11, 2), run(1, 12, 3)
    for value in (first, second, third):
        queue.enqueue(value)
    queue.discard(first.run_id)
    assert first.agent_id in queue._tenants[first.tenant_id]
    queue.discard(second.run_id)
    assert first.agent_id not in queue._tenants[first.tenant_id]
    assert queue.take() == third
    assert not queue._tenants


def test_clear_removes_all_indexes_and_allows_fresh_order():
    queue = FairReadyQueue(capacity=2)
    first, second = run(1, 1, 1), run(2, 2, 2)
    queue.enqueue(first)
    queue.enqueue(second)
    queue.clear()
    assert not queue._tenants
    assert len(queue) == 0
    assert not queue.discard(first.run_id)
    queue.enqueue(second)
    queue.enqueue(first)
    assert queue.take() == second
    assert queue.take() == first


@pytest.mark.parametrize("capacity", [0, -1, True, 1.5])
def test_capacity_is_required_positive_integer(capacity):
    with pytest.raises(ValueError):
        FairReadyQueue(capacity=capacity)
