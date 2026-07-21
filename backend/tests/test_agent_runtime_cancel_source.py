"""Durable cooperative cancellation source tests."""

from __future__ import annotations

from datetime import UTC, datetime
import uuid

import pytest

from app.models.agent_run_command import AgentRunCommand
from app.services.agent_runtime.cancel_source import (
    DatabaseRuntimeCancelSource,
    RuntimeCancelSourceError,
)
from app.services.agent_runtime.state import (
    RunInputSnapshots,
    RunRegistrySnapshot,
    RuntimeContext,
    RuntimeGraphState,
)


class _ScalarResult:
    def __init__(self, values: list[AgentRunCommand]) -> None:
        self._values = values

    def scalars(self) -> "_ScalarResult":
        return self

    def all(self) -> list[AgentRunCommand]:
        return self._values


class _Session:
    def __init__(self, commands: list[AgentRunCommand]) -> None:
        self.commands = commands
        self.statements: list[object] = []

    async def __aenter__(self) -> "_Session":
        return self

    async def __aexit__(self, exc_type, exc, traceback) -> bool:
        return False

    async def execute(self, statement) -> _ScalarResult:
        self.statements.append(statement)
        return _ScalarResult(self.commands)


class _SessionFactory:
    def __init__(self, session: _Session) -> None:
        self.session = session
        self.calls = 0

    def __call__(self) -> _Session:
        self.calls += 1
        return self.session


class _Executor:
    async def execute(self, *args, **kwargs):
        raise AssertionError("not used")


def _command(
    tenant_id: uuid.UUID,
    run_id: uuid.UUID,
    *,
    reason: object = "user_abort",
) -> AgentRunCommand:
    return AgentRunCommand(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        run_id=run_id,
        command_type="cancel",
        payload={"reason": reason},
        idempotency_key=f"cancel:{uuid.uuid4()}",
        status="pending",
        attempt_count=0,
        created_at=datetime.now(UTC),
    )


def _state(
    tenant_id: uuid.UUID,
    run_id: uuid.UUID,
) -> RuntimeGraphState:
    return {
        "registry": RunRegistrySnapshot(
            tenant_id=str(tenant_id),
            run_id=str(run_id),
            goal="finish",
            run_kind="foreground",
            source_type="chat",
            model_id=str(uuid.uuid4()),
            graph_name="runtime_graph",
            graph_version="v1",
        ),
        "snapshots": RunInputSnapshots(
            session_context={},
            session_context_version=0,
            recent_session_messages=(),
            related_run_summaries=(),
            initial_input={},
        ),
        "lifecycle": {
            "status": "running",
            "next_route": "model",
        },
    }


def _context(tenant_id: uuid.UUID, run_id: uuid.UUID) -> RuntimeContext:
    return RuntimeContext(
        tenant_id=str(tenant_id),
        run_id=str(run_id),
        command_id=str(uuid.uuid4()),
        executor=_Executor(),
    )


@pytest.mark.asyncio
async def test_returns_first_active_durable_cancel_without_checkpoint_receipts() -> None:
    tenant_id = uuid.uuid4()
    run_id = uuid.uuid4()
    pending = _command(tenant_id, run_id, reason=" user_abort ")
    session = _Session([pending])
    source = DatabaseRuntimeCancelSource(
        session_factory=_SessionFactory(session),  # type: ignore[arg-type]
    )

    signal = await source.get_cancel(
        _state(tenant_id, run_id),
        _context(tenant_id, run_id),
    )

    assert signal is not None
    assert signal.command_id == str(pending.id)
    assert signal.reason == "user_abort"
    sql = str(session.statements[0])
    assert "agent_run_commands.tenant_id" in sql
    assert "agent_run_commands.run_id" in sql
    assert "agent_run_commands.command_type" in sql
    assert "agent_run_commands.status" in sql
    assert "projected_" not in sql


@pytest.mark.asyncio
async def test_returns_none_when_no_pending_or_claimed_cancel_exists() -> None:
    tenant_id = uuid.uuid4()
    run_id = uuid.uuid4()
    source = DatabaseRuntimeCancelSource(
        session_factory=_SessionFactory(_Session([])),  # type: ignore[arg-type]
    )

    signal = await source.get_cancel(
        _state(tenant_id, run_id),
        _context(tenant_id, run_id),
    )

    assert signal is None


@pytest.mark.asyncio
async def test_legacy_checkpoint_registry_does_not_override_runtime_context_scope() -> None:
    tenant_id = uuid.uuid4()
    run_id = uuid.uuid4()
    factory = _SessionFactory(_Session([]))
    source = DatabaseRuntimeCancelSource(session_factory=factory)  # type: ignore[arg-type]

    signal = await source.get_cancel(
        _state(tenant_id, run_id),
        _context(uuid.uuid4(), run_id),
    )

    assert signal is None
    assert factory.calls == 1


@pytest.mark.asyncio
async def test_rejects_malformed_persisted_cancel_reason() -> None:
    tenant_id = uuid.uuid4()
    run_id = uuid.uuid4()
    source = DatabaseRuntimeCancelSource(
        session_factory=_SessionFactory(_Session([_command(tenant_id, run_id, reason=123)])),  # type: ignore[arg-type]
    )

    with pytest.raises(RuntimeCancelSourceError, match="reason") as raised:
        await source.get_cancel(
            _state(tenant_id, run_id),
            _context(tenant_id, run_id),
        )

    assert raised.value.code == "invalid_cancel_payload"
