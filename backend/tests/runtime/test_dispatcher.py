import asyncio
from dataclasses import replace
from uuid import uuid4

import pytest

from app.runtime.dispatcher import ExecutionDispatcher
from app.runtime.scheduler import RunKey


def key(tenant=None, agent=None):
    return RunKey(tenant or uuid4(), agent or uuid4(), uuid4())


async def wait_until(predicate):
    async with asyncio.timeout(2):
        while not predicate():
            await asyncio.sleep(0)


async def unexpected_failure(run, error):
    raise AssertionError("Unexpected quantum failure") from error


@pytest.mark.parametrize("slots,capacity", [(0, 1), (-1, 1), (2, 1), (True, 2), (1, True), (1.5, 2)])
def test_capacity_is_explicit_positive_integer(slots, capacity):
    with pytest.raises(ValueError):
        ExecutionDispatcher(lambda run: None, unexpected_failure, slots=slots, capacity=capacity)


async def test_reservation_identity_and_capacity_without_starting_execution():
    calls = []

    async def quantum(run):
        calls.append(run)
        return False

    dispatcher = ExecutionDispatcher(quantum, unexpected_failure, slots=1, capacity=1)
    run, other = key(), key()
    try:
        assert dispatcher.reserve(run)
        assert not dispatcher.reserve(run)
        with pytest.raises(ValueError):
            dispatcher.reserve(replace(run, tenant_id=uuid4()))
        with pytest.raises(ValueError):
            dispatcher.wake(other)
        with pytest.raises(ValueError):
            dispatcher.release(replace(run, agent_id=uuid4()))
        with pytest.raises(OverflowError):
            dispatcher.reserve(other)
        assert not calls and dispatcher.admitted == 1
        dispatcher.release(run)
        dispatcher.release(run)
        assert dispatcher.admitted == 0
        assert dispatcher.reserve(other)
    finally:
        await dispatcher.stop()
        dispatcher.release(other)


async def test_waiting_releases_slots_but_retains_admission_and_can_resume_when_full():
    gate = asyncio.Event()
    entered = []

    async def quantum(run):
        entered.append(run)
        await gate.wait()
        return False

    dispatcher = ExecutionDispatcher(quantum, unexpected_failure, slots=2, capacity=3)
    runs = [key() for _ in range(3)]
    for run in runs:
        dispatcher.reserve(run)
        dispatcher.wake(run)
    dispatcher.start()
    try:
        await wait_until(lambda: len(entered) == 2)
        assert dispatcher.active == 2 and dispatcher.admitted == 3
        with pytest.raises(OverflowError):
            dispatcher.reserve(key())
        gate.set()
        await wait_until(lambda: len(entered) == 3 and dispatcher.active == 0)
        assert dispatcher.admitted == 3
        dispatcher.wake(runs[0])
        await wait_until(lambda: len(entered) == 4 and dispatcher.active == 0)
        assert entered[-1] == runs[0] and dispatcher.admitted == 3
    finally:
        gate.set()
        await dispatcher.stop()
        for run in runs:
            dispatcher.release(run)


async def test_duplicate_wakes_never_overlap_and_request_only_one_followup_quantum():
    entered, release = asyncio.Event(), asyncio.Event()
    calls = 0
    active = 0
    maximum = 0

    async def quantum(run):
        nonlocal calls, active, maximum
        calls += 1
        active += 1
        maximum = max(maximum, active)
        try:
            if calls == 1:
                entered.set()
                await release.wait()
            return False
        finally:
            active -= 1

    dispatcher = ExecutionDispatcher(quantum, unexpected_failure, slots=2, capacity=2)
    run = key()
    dispatcher.reserve(run)
    for _ in range(100):
        dispatcher.wake(run)
    dispatcher.start()
    try:
        await asyncio.wait_for(entered.wait(), 2)
        for _ in range(100):
            dispatcher.wake(run)
        release.set()
        await wait_until(lambda: calls == 2 and dispatcher.active == 0)
        assert maximum == 1 and calls == 2
    finally:
        release.set()
        await dispatcher.stop()
        dispatcher.release(run)


async def test_actual_quantum_dispatch_gives_new_tenant_a_turn_within_two_allocations():
    tenant_a, tenant_b = uuid4(), uuid4()
    runs = [key(tenant_a) for _ in range(50)]
    other = key(tenant_b)
    observed = []
    ready_offset = None
    reached = asyncio.Event()

    async def quantum(run):
        nonlocal ready_offset
        observed.append(run)
        if ready_offset is None:
            dispatcher.reserve(other)
            dispatcher.wake(other)
            ready_offset = len(observed)
        if run == other:
            reached.set()
            return False
        await asyncio.sleep(0)
        return True

    dispatcher = ExecutionDispatcher(quantum, unexpected_failure, slots=1, capacity=51)
    for run in runs:
        dispatcher.reserve(run)
        dispatcher.wake(run)
    dispatcher.start()
    try:
        await asyncio.wait_for(reached.wait(), 2)
        assert other in observed[ready_offset:ready_offset + 2]
    finally:
        await dispatcher.stop()
        for run in [*runs, other]:
            dispatcher.release(run)


async def test_quantum_failure_retains_capacity_until_owner_commits_and_releases():
    settling, committed = asyncio.Event(), asyncio.Event()
    calls = 0

    async def quantum(run):
        nonlocal calls
        calls += 1
        raise ValueError("operation failed")

    async def on_failure(run, error):
        assert isinstance(error, ValueError)
        settling.set()
        await committed.wait()
        dispatcher.release(run)

    dispatcher = ExecutionDispatcher(quantum, on_failure, slots=1, capacity=1)
    run = key()
    dispatcher.reserve(run)
    dispatcher.wake(run)
    dispatcher.start()
    try:
        await asyncio.wait_for(settling.wait(), 2)
        assert dispatcher.admitted == dispatcher.active == 1
        dispatcher.wake(run)
        committed.set()
        await wait_until(lambda: dispatcher.active == dispatcher.admitted == 0)
        assert calls == 1
    finally:
        committed.set()
        await dispatcher.stop()
        dispatcher.release(run)


async def test_failed_settlement_retains_permit_and_late_wake_cannot_replay_quantum():
    calls = 0

    async def quantum(run):
        nonlocal calls
        calls += 1
        raise ValueError("operation failed")

    async def on_failure(run, error):
        raise OSError("settlement failed")

    dispatcher = ExecutionDispatcher(quantum, on_failure, slots=1, capacity=1)
    run = key()
    dispatcher.reserve(run)
    dispatcher.wake(run)
    dispatcher.start()
    try:
        await wait_until(lambda: run.run_id in dispatcher.failures and dispatcher.active == 0)
        assert dispatcher.admitted == 1
        for _ in range(10):
            dispatcher.wake(run)
            await asyncio.sleep(0)
        assert calls == 1
        assert dispatcher.admitted == 1
        dispatcher.release(run)
        assert not dispatcher.failures
    finally:
        await dispatcher.stop()
        dispatcher.release(run)


@pytest.mark.parametrize("cancel_count", [0, 1, 2])
async def test_stop_waits_for_operation_cleanup_but_keeps_permits_for_owner_interruption(cancel_count):
    entered, cancelling, finish_cleanup = asyncio.Event(), asyncio.Event(), asyncio.Event()
    failures = []

    async def quantum(run):
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelling.set()
            await finish_cleanup.wait()

    async def on_failure(run, error):
        failures.append(error)

    dispatcher = ExecutionDispatcher(quantum, on_failure, slots=1, capacity=2)
    running, queued = key(), key()
    for run in (running, queued):
        dispatcher.reserve(run)
        dispatcher.wake(run)
    dispatcher.start()
    stop = None
    try:
        await asyncio.wait_for(entered.wait(), 2)
        stop = asyncio.create_task(dispatcher.stop())
        await asyncio.wait_for(cancelling.wait(), 2)
        for _ in range(cancel_count):
            stop.cancel()
            await asyncio.sleep(0)
        assert not stop.done()
        assert dispatcher.admitted == 2
        with pytest.raises(RuntimeError):
            dispatcher.reserve(key())
        dispatcher.wake(queued)
        finish_cleanup.set()
        if cancel_count:
            with pytest.raises(asyncio.CancelledError):
                await stop
        else:
            await stop
        assert dispatcher.active == 0 and dispatcher.admitted == 2
        assert not failures
        await dispatcher.stop()
    finally:
        finish_cleanup.set()
        if stop is not None:
            await asyncio.gather(stop, return_exceptions=True)
        await asyncio.gather(dispatcher.stop(), return_exceptions=True)
        for run in (running, queued):
            dispatcher.release(run)


async def test_explicit_release_cancels_active_and_prevents_late_reentry():
    entered, cancelled = asyncio.Event(), asyncio.Event()
    calls = 0

    async def quantum(run):
        nonlocal calls
        calls += 1
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    dispatcher = ExecutionDispatcher(quantum, unexpected_failure, slots=1, capacity=1)
    run = key()
    dispatcher.reserve(run)
    dispatcher.wake(run)
    dispatcher.start()
    try:
        await asyncio.wait_for(entered.wait(), 2)
        dispatcher.wake(run)
        dispatcher.release(run)
        await asyncio.wait_for(cancelled.wait(), 2)
        await wait_until(lambda: dispatcher.active == 0)
        assert dispatcher.admitted == 0 and calls == 1
    finally:
        await dispatcher.stop()


async def test_start_is_single_use_and_stopped_dispatcher_cannot_restart():
    async def quantum(run):
        return False

    dispatcher = ExecutionDispatcher(quantum, unexpected_failure, slots=1, capacity=1)
    dispatcher.start()
    try:
        with pytest.raises(RuntimeError):
            dispatcher.start()
    finally:
        await dispatcher.stop()
    with pytest.raises(RuntimeError):
        dispatcher.start()
