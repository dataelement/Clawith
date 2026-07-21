"""Message-driven Session Compact policy and service tests."""

from __future__ import annotations

from collections import deque
import uuid

import pytest

from app.config import Settings
from app.models.agent import Agent
from app.models.chat_session import ChatSession
from app.models.llm import LLMModel
from app.services.agent_runtime import session_context_background as background
from app.services.agent_runtime.model_capabilities import ModelCapabilityResolver
from app.services.agent_runtime.session_context_completion import SessionCompactRequest
from app.services.agent_runtime.session_context_service import (
    SessionContextCandidate,
    SessionContextSnapshot,
)
from app.services.llm.utils import get_max_tokens


class _Result:
    def __init__(self, values=()) -> None:
        self.values = list(values)

    def scalar_one_or_none(self):
        return self.values[0] if self.values else None

    def scalars(self):
        return self

    def all(self):
        return list(self.values)


class _DB:
    def __init__(self, *results: _Result) -> None:
        self.results = deque(results)

    async def execute(self, _statement):
        if not self.results:
            raise AssertionError("unexpected database query")
        return self.results.popleft()


def _model(
    tenant_id: uuid.UUID,
    *,
    input_tokens: int,
    platform: bool = False,
) -> LLMModel:
    return LLMModel(
        id=uuid.uuid4(),
        tenant_id=None if platform else tenant_id,
        provider="openai",
        model=f"model-{input_tokens}",
        label="Model",
        api_key_encrypted="encrypted",
        enabled=True,
        max_input_tokens=input_tokens,
        max_output_tokens=256,
    )


def _threshold(model: LLMModel, settings: Settings) -> int:
    return ModelCapabilityResolver.runtime_budget(
        model,
        requested_max_output_tokens=get_max_tokens(
            model.provider,
            model.model,
            model.max_output_tokens,
        ),
        reserved_runtime_tokens=256,
        safety_margin_tokens=256,
        compact_threshold_ratio=settings.AGENT_RUNTIME_SUMMARY_THRESHOLD_RATIO,
    ).compact_threshold


@pytest.mark.asyncio
async def test_group_compact_trigger_uses_the_smallest_active_agent_budget() -> None:
    tenant_id = uuid.uuid4()
    group_id = uuid.uuid4()
    session = ChatSession(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        session_type="group",
        group_id=group_id,
        title="Group",
        source_channel="web",
        is_group=True,
        is_primary=True,
    )
    small = _model(tenant_id, input_tokens=10_000)
    large = _model(tenant_id, input_tokens=50_000, platform=True)
    agents = [
        Agent(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            creator_id=uuid.uuid4(),
            name="Small",
            status="idle",
            is_expired=False,
            access_mode="company",
            primary_model_id=small.id,
        ),
        Agent(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            creator_id=uuid.uuid4(),
            name="Large",
            status="idle",
            is_expired=False,
            access_mode="company",
            primary_model_id=large.id,
        ),
    ]
    settings = Settings(AGENT_RUNTIME_SUMMARY_THRESHOLD_RATIO=0.85)
    db = _DB(
        _Result([session]),
        _Result([group_id]),
        _Result(agents),
        _Result([large, small]),
    )

    policy = await background.SessionCompactPolicyResolver(
        settings=settings
    ).resolve(
        db,  # type: ignore[arg-type]
        tenant_id=tenant_id,
        session_id=session.id,
    )

    assert policy.source_agent_id is None
    assert policy.threshold_tokens == _threshold(small, settings)
    assert set(policy.contributing_model_ids) == {small.id, large.id}


@pytest.mark.asyncio
async def test_direct_session_has_no_second_session_compact_policy() -> None:
    tenant_id = uuid.uuid4()
    session = ChatSession(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        session_type="direct",
        agent_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        title="Direct",
        source_channel="web",
        is_primary=True,
    )
    db = _DB(_Result([session]))

    with pytest.raises(background.SessionContextBackgroundError) as raised:
        await background.SessionCompactPolicyResolver().resolve(
            db,  # type: ignore[arg-type]
            tenant_id=tenant_id,
            session_id=session.id,
        )

    assert raised.value.code == "direct_thread_owns_context"


@pytest.mark.asyncio
async def test_background_scanner_only_selects_group_sessions() -> None:
    captured = []

    class _ScannerDB:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback):
            return False

        async def execute(self, statement):
            captured.append(statement)
            return _Result()

    scanner = background.SessionContextCompactionScanner(
        session_factory=lambda: _ScannerDB(),  # type: ignore[arg-type]
        service=object(),  # type: ignore[arg-type]
        settings=Settings(),
    )

    assert await scanner.scan_once() == 0
    assert len(captured) == 1
    compiled = captured[0].compile()
    assert "session_type" in str(compiled)
    assert "group" in compiled.params.values()


def test_message_trigger_keeps_short_sessions_uncompacted_and_honors_early_count() -> None:
    snapshot = SessionContextSnapshot.empty()
    messages = ({"id": str(uuid.uuid4()), "role": "user", "content": "short"},)
    policy = background.SessionCompactPolicy(
        source_agent_id=None,
        threshold_tokens=100_000,
        contributing_model_ids=(uuid.uuid4(),),
    )
    default = background.SessionCompactPolicyResolver(settings=Settings())
    early = background.SessionCompactPolicyResolver(
        settings=Settings(AGENT_RUNTIME_SESSION_COMPACT_MESSAGE_THRESHOLD=1)
    )

    assert default.should_compact(
        snapshot=snapshot,
        messages=messages,
        policy=policy,
    ) is False
    assert early.should_compact(
        snapshot=snapshot,
        messages=messages,
        policy=policy,
    ) is True
    assert early.should_compact(
        snapshot=snapshot,
        messages=(),
        policy=policy,
    ) is False
    assert default.should_compact(
        snapshot=snapshot,
        messages=messages,
        recent_messages=(
            {
                "id": str(uuid.uuid4()),
                "role": "user",
                "content": "x" * 500_000,
            },
        ),
        policy=policy,
    ) is True


@pytest.mark.asyncio
async def test_message_compaction_advances_context_without_creating_a_run(
    monkeypatch,
) -> None:
    tenant_id = uuid.uuid4()
    session_id = uuid.uuid4()
    message_id = uuid.uuid4()
    request = SessionCompactRequest(
        tenant_id=tenant_id,
        session_id=session_id,
        source_agent_id=None,
        checkpoint_id=f"message-window:0:{message_id}",
        snapshot=SessionContextSnapshot.empty(),
        messages=(
            {"id": str(message_id), "role": "user", "content": "old message"},
        ),
        delta=None,
    )
    candidate = SessionContextCandidate(
        summary="compacted",
        covered_through_message_id=message_id,
    )

    class _Compactor:
        def __init__(self) -> None:
            self.requests = []

        async def compact(self, value):
            self.requests.append(value)
            return candidate

    compactor = _Compactor()
    service = background.SessionContextMessageCompactionService(
        lock_engine=object(),  # type: ignore[arg-type]
        compactor=compactor,  # type: ignore[arg-type]
        context_service=object(),  # type: ignore[arg-type]
        policy_resolver=object(),  # type: ignore[arg-type]
    )
    commits = []

    async def load(_connection, **kwargs):
        assert kwargs == {"tenant_id": tenant_id, "session_id": session_id}
        return request

    async def commit(_connection, **kwargs):
        commits.append(kwargs)

    async def lock(_engine, requested_session_id, callback):
        assert requested_session_id == session_id
        return await callback(object())

    monkeypatch.setattr(service, "_load_request", load)
    monkeypatch.setattr(service, "_commit", commit)
    monkeypatch.setattr(background, "_with_session_lock", lock)

    compacted = await service.compact_session(
        tenant_id=tenant_id,
        session_id=session_id,
    )

    assert compacted is True
    assert compactor.requests == [request]
    assert commits == [{"request": request, "candidate": candidate}]


@pytest.mark.asyncio
async def test_session_lock_ends_implicit_transactions_around_the_cas() -> None:
    session_id = uuid.uuid4()

    class _Scalar:
        def scalar_one(self):
            return True

    class _Connection:
        def __init__(self) -> None:
            self.events = []

        async def execute(self, statement, values):
            self.events.append((str(statement), values))
            return _Scalar()

        async def commit(self):
            self.events.append("commit")

    connection = _Connection()

    class _ConnectionContext:
        async def __aenter__(self):
            return connection

        async def __aexit__(self, exc_type, exc, traceback):
            return False

    class _Engine:
        def connect(self):
            return _ConnectionContext()

    callback_events = []

    async def callback(value):
        callback_events.append(value)
        return True

    assert await background._with_session_lock(  # type: ignore[attr-defined]
        _Engine(),  # type: ignore[arg-type]
        session_id,
        callback,
    ) is True
    assert callback_events == [connection]
    assert connection.events[1] == "commit"
    assert connection.events[-1] == "commit"
