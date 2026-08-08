"""External channel intake tests for the durable Runtime."""

from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace
import uuid

import pytest

from app.services.agent_runtime import channel_chat
from app.services.agent_runtime.channel_chat import (
    channel_message_id,
    enqueue_channel_chat_runtime,
)
from app.services.agent_runtime.chat_intake import ChatRuntimeIntake
from app.services.agent_runtime.contracts import RunHandle


def test_channel_message_id_is_stable_for_provider_retries() -> None:
    agent_id = uuid.uuid4()

    first = channel_message_id(agent_id, "wechat", "provider-message-1")
    retry = channel_message_id(agent_id, "wechat", "provider-message-1")
    other_channel = channel_message_id(agent_id, "slack", "provider-message-1")

    assert first == retry
    assert first != other_channel


@pytest.mark.asyncio
async def test_waiting_resume_reads_the_lane_holder_checkpoint() -> None:
    tenant_id = uuid.uuid4()
    agent_id = uuid.uuid4()
    session_id = uuid.uuid4()
    user_id = uuid.uuid4()
    run = SimpleNamespace(id=uuid.uuid4())

    class _Scalars:
        def all(self):
            return [run]

    class _Result:
        def scalars(self):
            return _Scalars()

    class _Db:
        async def execute(self, _statement):
            return _Result()

    class _Reader:
        async def get_run_state(self, requested_tenant_id, requested_run_id):
            assert requested_tenant_id == tenant_id
            assert requested_run_id == run.id
            return SimpleNamespace(
                run_id=run.id,
                thread_id=str(session_id),
                session_id=session_id,
                execution_status="waiting_user",
                waiting_correlation_id="checkpoint-correlation",
            )

    resume = await channel_chat._waiting_resume(
        _Db(),  # type: ignore[arg-type]
        tenant_id=tenant_id,
        agent_id=agent_id,
        session_id=session_id,
        user_id=user_id,
        run_state_reader=_Reader(),  # type: ignore[arg-type]
    )

    assert resume == (run.id, "checkpoint-correlation")


@pytest.mark.asyncio
async def test_channel_intake_resumes_the_latest_waiting_run(monkeypatch) -> None:
    tenant_id = uuid.uuid4()
    agent_id = uuid.uuid4()
    user_id = uuid.uuid4()
    session_id = uuid.uuid4()
    waiting_run_id = uuid.uuid4()
    message_id = uuid.uuid4()
    handle = RunHandle(
        tenant_id=tenant_id,
        run_id=waiting_run_id,
        thread_id=str(waiting_run_id),
        command_id=uuid.uuid4(),
        runtime_type="langgraph",
        created=False,
    )
    expected = ChatRuntimeIntake(
        handle=handle,
        message_id=message_id,
        resumed=True,
    )
    captured: dict[str, object] = {}
    reader = object()

    @asynccontextmanager
    async def fake_open_reader(_db):
        yield reader

    async def fake_waiting_resume(_db, **kwargs):
        captured["resume_scope"] = kwargs
        return waiting_run_id, "approval-7"

    async def fake_enqueue(_db, **kwargs):
        captured["enqueue"] = kwargs
        return expected

    monkeypatch.setattr(channel_chat, "_waiting_resume", fake_waiting_resume)
    monkeypatch.setattr(channel_chat, "enqueue_chat_runtime", fake_enqueue)
    monkeypatch.setattr(channel_chat, "open_run_state_reader", fake_open_reader)

    result = await enqueue_channel_chat_runtime(
        object(),  # type: ignore[arg-type]
        agent=SimpleNamespace(id=agent_id, tenant_id=tenant_id),  # type: ignore[arg-type]
        user=SimpleNamespace(id=user_id),  # type: ignore[arg-type]
        session=SimpleNamespace(id=session_id),  # type: ignore[arg-type]
        model=SimpleNamespace(id=uuid.uuid4()),  # type: ignore[arg-type]
        content="approve",
        source_channel="wechat",
        channel_delivery_target={"user_id": "wechat-user-1"},
        message_id=message_id,
    )

    assert result is expected
    assert captured["resume_scope"] == {
        "tenant_id": tenant_id,
        "agent_id": agent_id,
        "session_id": session_id,
        "user_id": user_id,
        "run_state_reader": reader,
    }
    enqueue = captured["enqueue"]
    assert isinstance(enqueue, dict)
    assert enqueue["resume_run_id"] == waiting_run_id
    assert enqueue["resume_correlation_id"] == "approval-7"
    assert enqueue["source_channel"] == "wechat"
    assert enqueue["channel_delivery_target"] == {"user_id": "wechat-user-1"}
    assert enqueue["run_state_reader"] is reader
