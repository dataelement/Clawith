"""Durable onboarding completion tests."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch
import uuid

import pytest

from app.services.agent_runtime.command_worker import (
    CheckpointObservation,
    RuntimeRunRecord,
)
from app.services.agent_runtime.onboarding_completion import (
    OnboardingRuntimeCompletionHandler,
)
from app.services.agent_runtime.state import (
    RunInputSnapshots,
    RunRegistrySnapshot,
    RuntimeGraphState,
)
from app.services.onboarding import PHASE_GREETED


class _Session:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False


def _records(*, status: str = "completed", include_phase: bool = True):
    tenant_id = uuid.uuid4()
    agent_id = uuid.uuid4()
    user_id = uuid.uuid4()
    run_id = uuid.uuid4()
    registry = RunRegistrySnapshot(
        tenant_id=str(tenant_id),
        run_id=str(run_id),
        goal="Greet the user",
        run_kind="foreground",
        source_type="chat",
        model_id=str(uuid.uuid4()),
        graph_name="runtime",
        graph_version="v1",
        agent_id=str(agent_id),
        session_id=str(uuid.uuid4()),
    )
    initial_input = {"user_id": str(user_id)}
    if include_phase:
        initial_input["onboarding_target_phase"] = PHASE_GREETED
    state: RuntimeGraphState = {
        "registry": registry,
        "snapshots": RunInputSnapshots(
            session_context={},
            session_context_version=0,
            recent_session_messages=(),
            related_run_summaries=(),
            initial_input=initial_input,
        ),
        "lifecycle": {
            "status": status,
            "next_route": "terminal",
            "run_messages": [],
            "pending_tool_calls": [],
        },
    }
    return (
        RuntimeRunRecord(
            tenant_id=tenant_id,
            run_id=run_id,
            thread_id=str(run_id),
            runtime_type="langgraph",
            goal=registry.goal,
            run_kind=registry.run_kind,
            source_type=registry.source_type,
            model_id=registry.model_id,
            graph_name=registry.graph_name,
            graph_version=registry.graph_version,
            agent_id=registry.agent_id,
            session_id=registry.session_id,
            system_role=registry.system_role,
            parent_run_id=registry.parent_run_id,
            root_run_id=registry.root_run_id,
        ),
        CheckpointObservation(checkpoint_id="checkpoint-1", state=state),
        agent_id,
        user_id,
    )


@pytest.mark.asyncio
async def test_completed_onboarding_advances_without_a_live_socket() -> None:
    run, checkpoint, agent_id, user_id = _records()
    handler = OnboardingRuntimeCompletionHandler(session_factory=lambda: _Session())

    with patch(
        "app.services.agent_runtime.onboarding_completion.mark_onboarding_phase",
        new=AsyncMock(),
    ) as mark:
        await handler.handle(run=run, checkpoint=checkpoint)

    mark.assert_awaited_once()
    assert mark.await_args.args[1:] == (agent_id, user_id, PHASE_GREETED)


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["failed", "cancelled"])
async def test_unsuccessful_onboarding_does_not_advance(status: str) -> None:
    run, checkpoint, _, _ = _records(status=status)
    handler = OnboardingRuntimeCompletionHandler(session_factory=lambda: _Session())

    with patch(
        "app.services.agent_runtime.onboarding_completion.mark_onboarding_phase",
        new=AsyncMock(),
    ) as mark:
        await handler.handle(run=run, checkpoint=checkpoint)

    mark.assert_not_awaited()


@pytest.mark.asyncio
async def test_normal_chat_run_is_ignored() -> None:
    run, checkpoint, _, _ = _records(include_phase=False)
    handler = OnboardingRuntimeCompletionHandler(session_factory=lambda: _Session())

    with patch(
        "app.services.agent_runtime.onboarding_completion.mark_onboarding_phase",
        new=AsyncMock(),
    ) as mark:
        await handler.handle(run=run, checkpoint=checkpoint)

    mark.assert_not_awaited()
