from __future__ import annotations

import uuid
from dataclasses import dataclass

import pytest

from app.services.sandbox import execution_lease
from app.services.sandbox.execution_lease import (
    SandboxExecutionLease,
    SandboxExecutionLeaseStore,
)


@dataclass(frozen=True)
class LeaseScope:
    tenant_id: uuid.UUID
    agent_id: uuid.UUID
    session_id: uuid.UUID


class FakeRedis:
    def __init__(self, *, acquired: bool = True, eval_result: object = 1) -> None:
        self.acquired = acquired
        self.eval_result = eval_result
        self.set_calls: list[tuple[str, str, bool, int]] = []
        self.eval_calls: list[tuple[str, int, tuple[object, ...]]] = []
        self.eval_error: Exception | None = None

    async def set(
        self,
        key: str,
        value: str,
        *,
        nx: bool,
        px: int,
    ) -> object:
        self.set_calls.append((key, value, nx, px))
        return self.acquired

    async def eval(
        self,
        script: str,
        numkeys: int,
        *keys_and_args: object,
    ) -> object:
        self.eval_calls.append((script, numkeys, keys_and_args))
        if self.eval_error is not None:
            raise self.eval_error
        return self.eval_result


def _scope() -> LeaseScope:
    return LeaseScope(
        tenant_id=uuid.UUID("00000000-0000-0000-0000-000000000001"),
        agent_id=uuid.UUID("00000000-0000-0000-0000-000000000002"),
        session_id=uuid.UUID("00000000-0000-0000-0000-000000000003"),
    )


@pytest.mark.asyncio
async def test_acquire_preserves_key_nx_and_millisecond_ttl() -> None:
    redis = FakeRedis()
    store = SandboxExecutionLeaseStore(redis)

    lease = await store.acquire(_scope(), ttl_seconds=45)

    assert lease is not None
    assert lease.key == (
        "tenant:00000000-0000-0000-0000-000000000001:sandbox-execution:"
        "00000000-0000-0000-0000-000000000002:"
        "00000000-0000-0000-0000-000000000003"
    )
    [(key, value, nx, px)] = redis.set_calls
    assert key == lease.key
    assert value.startswith("v1|")
    assert nx is True
    assert px == 45_000


@pytest.mark.asyncio
async def test_acquire_returns_none_when_scope_is_already_owned() -> None:
    redis = FakeRedis(acquired=False)

    lease = await SandboxExecutionLeaseStore(redis).acquire(_scope())

    assert lease is None


@pytest.mark.asyncio
async def test_renew_uses_owner_checked_script_and_marks_ownership_lost() -> None:
    redis = FakeRedis(eval_result=0)
    lease = SandboxExecutionLease(redis, "lease-key", "owner-value", 60)

    renewed = await lease._renew(12)

    assert renewed is False
    assert lease.ownership_lost is True
    assert redis.eval_calls == [
        (
            execution_lease._RENEW_SCRIPT,
            1,
            ("lease-key", "owner-value", 12_000),
        )
    ]


@pytest.mark.asyncio
async def test_unverifiable_renewal_marks_ownership_lost() -> None:
    redis = FakeRedis()
    redis.eval_error = RuntimeError("redis unavailable")
    lease = SandboxExecutionLease(redis, "lease-key", "owner-value", 60)

    assert await lease._renew(60) is False
    assert lease.ownership_lost is True


@pytest.mark.asyncio
async def test_heartbeat_renews_after_one_third_ttl(monkeypatch) -> None:
    redis = FakeRedis(eval_result=0)
    lease = SandboxExecutionLease(redis, "lease-key", "owner-value", 9)
    observed_timeouts: list[int] = []

    async def expire_once(awaitable, *, timeout):
        observed_timeouts.append(timeout)
        awaitable.close()
        raise TimeoutError

    monkeypatch.setattr(execution_lease.asyncio, "wait_for", expire_once)

    await lease.start_heartbeat()
    assert lease._heartbeat_task is not None
    await lease._heartbeat_task

    assert observed_timeouts == [3]
    assert lease.ownership_lost is True
    assert redis.eval_calls[-1][2][-1] == 9_000


@pytest.mark.asyncio
async def test_publication_window_stops_heartbeat_and_renews_requested_ttl() -> None:
    redis = FakeRedis()
    lease = SandboxExecutionLease(redis, "lease-key", "owner-value", 60)
    await lease.start_heartbeat()

    assert await lease.ensure_publication_window(15) is True

    assert lease._heartbeat_task is None
    assert redis.eval_calls[-1] == (
        execution_lease._RENEW_SCRIPT,
        1,
        ("lease-key", "owner-value", 15_000),
    )


@pytest.mark.asyncio
async def test_release_uses_owner_checked_script_and_stops_heartbeat() -> None:
    redis = FakeRedis()
    lease = SandboxExecutionLease(redis, "lease-key", "owner-value", 60)
    await lease.start_heartbeat()

    await lease.release()

    assert redis.eval_calls[-1] == (
        execution_lease._RELEASE_SCRIPT,
        1,
        ("lease-key", "owner-value"),
    )
    assert lease._heartbeat_task is not None
    assert lease._heartbeat_task.done()


@pytest.mark.asyncio
async def test_release_failure_propagates_after_heartbeat_cleanup() -> None:
    redis = FakeRedis()
    lease = SandboxExecutionLease(redis, "lease-key", "owner-value", 60)
    await lease.start_heartbeat()
    redis.eval_error = RuntimeError("redis unavailable")

    with pytest.raises(RuntimeError, match="redis unavailable"):
        await lease.release()

    assert lease._heartbeat_task is not None
    assert lease._heartbeat_task.done()
    assert lease.ownership_lost is False
