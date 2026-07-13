"""External channel intake tests for the durable Runtime."""

from __future__ import annotations

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

    async def fake_waiting_resume(_db, **kwargs):
        captured["resume_scope"] = kwargs
        return waiting_run_id, "approval-7"

    async def fake_enqueue(_db, **kwargs):
        captured["enqueue"] = kwargs
        return expected

    monkeypatch.setattr(channel_chat, "_waiting_resume", fake_waiting_resume)
    monkeypatch.setattr(channel_chat, "enqueue_chat_runtime", fake_enqueue)

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
    }
    enqueue = captured["enqueue"]
    assert isinstance(enqueue, dict)
    assert enqueue["resume_run_id"] == waiting_run_id
    assert enqueue["resume_correlation_id"] == "approval-7"
    assert enqueue["source_channel"] == "wechat"
    assert enqueue["channel_delivery_target"] == {"user_id": "wechat-user-1"}
