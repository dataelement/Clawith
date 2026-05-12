"""Unit tests for the Agent API calling feature (app/api/agent_api.py).

Tests cover:
- Token key generation format and uniqueness
- Bearer token parsing and authentication
- Relationship enforcement between caller and target agents
- Target agent not found / expired / no model configured
- Self-calling bypass (no relationship needed)
- Successful LLM invocation end-to-end
- Token Key management endpoints (get / regenerate)
"""

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from app.api import agent_api
from app.api.agent_api import (
    generate_token_key,
    _get_caller_agent,
    _check_relationship,
    agent_api_chat,
    get_token_key,
    regenerate_token_key,
)


# ---------------------------------------------------------------------------
# Helpers / fakes
# ---------------------------------------------------------------------------

class DummyResult:
    """Mimics SQLAlchemy async result for execute() calls."""

    def __init__(self, values=None):
        self._values = list(values or [])

    def scalar_one_or_none(self):
        return self._values[0] if self._values else None

    def scalars(self):
        return self

    def all(self):
        return list(self._values)


class RecordingDB:
    """Minimal fake async DB session that returns pre-configured results."""

    def __init__(self, responses=None):
        self.responses = list(responses or [])
        self.added = []
        self.committed = False

    async def execute(self, _statement, _params=None):
        if not self.responses:
            return DummyResult()
        return self.responses.pop(0)

    def add(self, value):
        self.added.append(value)

    async def commit(self):
        self.committed = True

    async def refresh(self, value):
        pass

    async def flush(self):
        pass


def _make_agent(*, name="TestBot", token_key=None, token_key_suffix=None,
                primary_model_id=None, fallback_model_id=None,
                is_expired=False, creator_id=None, role_description="helper",
                status="idle", agent_type="native"):
    """Create a fake Agent-like object."""
    agent_id = uuid.uuid4()
    return SimpleNamespace(
        id=agent_id,
        name=name,
        role_description=role_description,
        token_key=token_key,
        token_key_suffix=token_key_suffix,
        primary_model_id=primary_model_id,
        fallback_model_id=fallback_model_id,
        is_expired=is_expired,
        creator_id=creator_id or uuid.uuid4(),
        status=status,
        agent_type=agent_type,
        tenant_id=uuid.uuid4(),
    )


def _make_model(*, enabled=True, model_name="gpt-4"):
    """Create a fake LLMModel-like object."""
    return SimpleNamespace(
        id=uuid.uuid4(),
        model=model_name,
        enabled=enabled,
    )


def _make_relationship(agent_id, target_agent_id):
    """Create a fake AgentAgentRelationship-like object."""
    return SimpleNamespace(
        id=uuid.uuid4(),
        agent_id=agent_id,
        target_agent_id=target_agent_id,
        relation="collaborator",
    )


def _make_chat_request(agent_id, prompt="Hello"):
    """Create a fake AgentApiChatRequest-like object."""
    return SimpleNamespace(
        agent_id=agent_id,
        prompt=prompt,
    )


# ---------------------------------------------------------------------------
# Token key generation tests
# ---------------------------------------------------------------------------


class TestGenerateTokenKey:
    def test_format(self):
        """Token key must start with 'clw_' and be 36 chars total."""
        key, suffix = generate_token_key()
        assert key.startswith("clw_")
        assert len(key) == 4 + 32  # "clw_" + 32 hex chars
        assert suffix == key[-4:]

    def test_uniqueness(self):
        """Two calls should produce different keys."""
        key1, _ = generate_token_key()
        key2, _ = generate_token_key()
        assert key1 != key2

    def test_suffix_matches_last_four(self):
        """Suffix must be exactly the last 4 characters of the full key."""
        for _ in range(10):
            key, suffix = generate_token_key()
            assert suffix == key[-4:]


# ---------------------------------------------------------------------------
# _get_caller_agent tests
# ---------------------------------------------------------------------------


class TestGetCallerAgent:
    @pytest.mark.asyncio
    async def test_valid_token(self):
        """Valid token key returns the matching agent."""
        agent = _make_agent(token_key="clw_abc123")
        db = RecordingDB(responses=[DummyResult(values=[agent])])
        result = await _get_caller_agent("clw_abc123", db)
        assert result.id == agent.id

    @pytest.mark.asyncio
    async def test_invalid_token(self):
        """Invalid token key raises 401."""
        db = RecordingDB(responses=[DummyResult()])
        with pytest.raises(HTTPException) as exc:
            await _get_caller_agent("clw_nonexistent", db)
        assert exc.value.status_code == 401
        assert "Invalid token key" in exc.value.detail


# ---------------------------------------------------------------------------
# _check_relationship tests
# ---------------------------------------------------------------------------


class TestCheckRelationship:
    @pytest.mark.asyncio
    async def test_has_relationship(self):
        """Returns True when relationship exists."""
        caller_id = uuid.uuid4()
        target_id = uuid.uuid4()
        rel = _make_relationship(caller_id, target_id)
        db = RecordingDB(responses=[DummyResult(values=[rel])])
        assert await _check_relationship(db, caller_id, target_id) is True

    @pytest.mark.asyncio
    async def test_no_relationship(self):
        """Returns False when no relationship exists."""
        db = RecordingDB(responses=[DummyResult()])
        assert await _check_relationship(db, uuid.uuid4(), uuid.uuid4()) is False


# ---------------------------------------------------------------------------
# agent_api_chat endpoint tests
# ---------------------------------------------------------------------------


class TestAgentApiChat:
    @pytest.mark.asyncio
    async def test_missing_bearer_prefix(self):
        """Non-Bearer auth header returns 401."""
        body = _make_chat_request(uuid.uuid4())
        with pytest.raises(HTTPException) as exc:
            await agent_api_chat(body, authorization="Basic abc123")
        assert exc.value.status_code == 401
        assert "Bearer" in exc.value.detail

    @pytest.mark.asyncio
    async def test_empty_token_key(self):
        """Empty token key after Bearer returns 401."""
        body = _make_chat_request(uuid.uuid4())
        with pytest.raises(HTTPException) as exc:
            await agent_api_chat(body, authorization="Bearer ")
        assert exc.value.status_code == 401
        assert "required" in exc.value.detail.lower()

    @pytest.mark.asyncio
    async def test_invalid_token_key(self):
        """Invalid token key returns 401."""
        body = _make_chat_request(uuid.uuid4())
        db = RecordingDB(responses=[DummyResult()])  # no agent found
        with pytest.raises(HTTPException) as exc:
            await agent_api_chat(body, authorization="Bearer clw_bad", db=db)
        assert exc.value.status_code == 401

    @pytest.mark.asyncio
    async def test_target_not_found(self):
        """Non-existent target agent returns 404."""
        caller = _make_agent(name="Caller", token_key="clw_callerkey")
        db = RecordingDB(responses=[
            DummyResult(values=[caller]),  # caller lookup
            DummyResult(),                  # target lookup: not found
        ])
        body = _make_chat_request(uuid.uuid4(), prompt="test")
        with pytest.raises(HTTPException) as exc:
            await agent_api_chat(body, authorization="Bearer clw_callerkey", db=db)
        assert exc.value.status_code == 404
        assert "not found" in exc.value.detail.lower()

    @pytest.mark.asyncio
    async def test_no_relationship_returns_403(self):
        """Calling an agent without relationship returns 403."""
        caller = _make_agent(name="Caller", token_key="clw_callerkey")
        target = _make_agent(name="Target")
        db = RecordingDB(responses=[
            DummyResult(values=[caller]),   # caller lookup
            DummyResult(values=[target]),    # target lookup
            DummyResult(),                   # relationship check: not found
        ])
        body = _make_chat_request(target.id, prompt="test")
        with pytest.raises(HTTPException) as exc:
            await agent_api_chat(body, authorization="Bearer clw_callerkey", db=db)
        assert exc.value.status_code == 403
        assert "no relationship" in exc.value.detail.lower()

    @pytest.mark.asyncio
    async def test_expired_target_returns_403(self):
        """Calling an expired agent returns 403."""
        caller = _make_agent(name="Caller", token_key="clw_callerkey")
        target = _make_agent(name="Target", is_expired=True)
        rel = _make_relationship(caller.id, target.id)
        db = RecordingDB(responses=[
            DummyResult(values=[caller]),   # caller lookup
            DummyResult(values=[target]),    # target lookup
            DummyResult(values=[rel]),       # relationship check
        ])
        body = _make_chat_request(target.id, prompt="test")
        with patch("app.core.permissions.is_agent_expired", return_value=True):
            with pytest.raises(HTTPException) as exc:
                await agent_api_chat(body, authorization="Bearer clw_callerkey", db=db)
        assert exc.value.status_code == 403
        assert "expired" in exc.value.detail.lower()

    @pytest.mark.asyncio
    async def test_no_model_configured_returns_400(self):
        """Target agent with no configured model returns 400."""
        caller = _make_agent(name="Caller", token_key="clw_callerkey")
        target = _make_agent(name="Target", primary_model_id=None)
        rel = _make_relationship(caller.id, target.id)
        db = RecordingDB(responses=[
            DummyResult(values=[caller]),   # caller lookup
            DummyResult(values=[target]),    # target lookup
            DummyResult(values=[rel]),       # relationship check
        ])
        body = _make_chat_request(target.id, prompt="test")
        with patch("app.core.permissions.is_agent_expired", return_value=False):
            with pytest.raises(HTTPException) as exc:
                await agent_api_chat(body, authorization="Bearer clw_callerkey", db=db)
        assert exc.value.status_code == 400
        assert "no llm model" in exc.value.detail.lower()

    @pytest.mark.asyncio
    async def test_self_call_skips_relationship(self):
        """Calling yourself should skip the relationship check."""
        model_id = uuid.uuid4()
        caller = _make_agent(
            name="SelfBot", token_key="clw_selfkey",
            primary_model_id=model_id,
        )
        model = _make_model()
        db = RecordingDB(responses=[
            DummyResult(values=[caller]),   # caller lookup
            DummyResult(values=[caller]),   # target lookup (same agent)
            # No relationship query — self-call skips it
            DummyResult(values=[model]),    # primary model lookup
        ])
        body = _make_chat_request(caller.id, prompt="talk to yourself")

        with patch("app.core.permissions.is_agent_expired", return_value=False):
            with patch("app.services.llm.call_llm", new_callable=AsyncMock, return_value="Self-reply"):
                with patch("app.services.activity_logger.log_activity", new_callable=AsyncMock):
                    result = await agent_api_chat(
                        body, authorization="Bearer clw_selfkey", db=db,
                    )
        assert result.reply == "Self-reply"

    @pytest.mark.asyncio
    async def test_successful_call_with_relationship(self):
        """Full successful call flow: auth → relationship → LLM → response."""
        model_id = uuid.uuid4()
        caller = _make_agent(name="Caller", token_key="clw_callerkey")
        target = _make_agent(name="Target", primary_model_id=model_id)
        rel = _make_relationship(caller.id, target.id)
        model = _make_model()
        db = RecordingDB(responses=[
            DummyResult(values=[caller]),   # caller lookup
            DummyResult(values=[target]),    # target lookup
            DummyResult(values=[rel]),       # relationship check
            DummyResult(values=[model]),     # primary model lookup
        ])
        body = _make_chat_request(target.id, prompt="What is 1+1?")

        with patch("app.core.permissions.is_agent_expired", return_value=False):
            with patch("app.services.llm.call_llm", new_callable=AsyncMock, return_value="The answer is 2."):
                with patch("app.services.activity_logger.log_activity", new_callable=AsyncMock) as mock_log:
                    result = await agent_api_chat(
                        body, authorization="Bearer clw_callerkey", db=db,
                    )

        assert result.reply == "The answer is 2."
        # Activity logs: one for the target (api_call) and one for the caller (api_call_out)
        assert mock_log.call_count == 2
        call_args = [c.args for c in mock_log.call_args_list]
        action_types = [a[1] for a in call_args]
        assert "api_call" in action_types
        assert "api_call_out" in action_types

    @pytest.mark.asyncio
    async def test_llm_error_returns_502(self):
        """LLM call failure returns 502."""
        model_id = uuid.uuid4()
        caller = _make_agent(name="Caller", token_key="clw_callerkey")
        target = _make_agent(name="Target", primary_model_id=model_id)
        rel = _make_relationship(caller.id, target.id)
        model = _make_model()
        db = RecordingDB(responses=[
            DummyResult(values=[caller]),
            DummyResult(values=[target]),
            DummyResult(values=[rel]),
            DummyResult(values=[model]),
        ])
        body = _make_chat_request(target.id, prompt="error test")

        with patch("app.core.permissions.is_agent_expired", return_value=False):
            with patch("app.services.llm.call_llm", new_callable=AsyncMock, side_effect=RuntimeError("Model crashed")):
                with pytest.raises(HTTPException) as exc:
                    await agent_api_chat(body, authorization="Bearer clw_callerkey", db=db)
        assert exc.value.status_code == 502
        assert "LLM call failed" in exc.value.detail

    @pytest.mark.asyncio
    async def test_fallback_model_used_when_primary_disabled(self):
        """When primary model is disabled, fallback is promoted."""
        primary_model_id = uuid.uuid4()
        fallback_model_id = uuid.uuid4()
        caller = _make_agent(name="Caller", token_key="clw_callerkey")
        target = _make_agent(
            name="Target",
            primary_model_id=primary_model_id,
            fallback_model_id=fallback_model_id,
        )
        rel = _make_relationship(caller.id, target.id)
        disabled_model = _make_model(enabled=False, model_name="disabled-model")
        fallback_model = _make_model(enabled=True, model_name="fallback-model")
        db = RecordingDB(responses=[
            DummyResult(values=[caller]),
            DummyResult(values=[target]),
            DummyResult(values=[rel]),
            DummyResult(values=[disabled_model]),   # primary model: disabled
            DummyResult(values=[fallback_model]),    # fallback model: enabled
        ])
        body = _make_chat_request(target.id, prompt="test fallback")

        captured_model = {}

        async def fake_call_llm(*args, **kwargs):
            # Extract model from positional or keyword args
            model = kwargs.get('model') or (args[0] if args else None)
            if model:
                captured_model["model"] = model.model
            return "Fallback used"

        with patch("app.core.permissions.is_agent_expired", return_value=False):
            with patch("app.services.llm.call_llm", side_effect=fake_call_llm):
                with patch("app.services.activity_logger.log_activity", new_callable=AsyncMock):
                    result = await agent_api_chat(
                        body, authorization="Bearer clw_callerkey", db=db,
                    )
        assert result.reply == "Fallback used"
        assert captured_model["model"] == "fallback-model"


# ---------------------------------------------------------------------------
# get_token_key endpoint tests
# ---------------------------------------------------------------------------


class TestGetTokenKey:
    @pytest.mark.asyncio
    async def test_missing_bearer_prefix(self):
        """Non-Bearer auth returns 401."""
        with pytest.raises(HTTPException) as exc:
            await get_token_key(uuid.uuid4(), authorization="Basic xyz")
        assert exc.value.status_code == 401

    @pytest.mark.asyncio
    async def test_returns_existing_key(self):
        """Returns existing token_key when present."""
        agent_id = uuid.uuid4()
        user_id = uuid.uuid4()
        agent = _make_agent(token_key="clw_existing1234", token_key_suffix="1234")
        agent.id = agent_id
        user = SimpleNamespace(id=user_id)

        db = RecordingDB(responses=[
            DummyResult(values=[user]),  # user lookup
        ])

        with patch("app.core.security.decode_access_token", return_value={"sub": str(user_id)}):
            with patch("app.core.permissions.check_agent_access", new_callable=AsyncMock, return_value=(agent, "manage")):
                result = await get_token_key(agent_id, authorization="Bearer jwt.token.here", db=db)

        assert result["token_key"] == "clw_existing1234"
        assert result["token_key_suffix"] == "1234"

    @pytest.mark.asyncio
    async def test_generates_key_on_demand(self):
        """Generates a new key when agent has no token_key."""
        agent_id = uuid.uuid4()
        user_id = uuid.uuid4()
        agent = _make_agent(token_key=None, token_key_suffix=None)
        agent.id = agent_id
        user = SimpleNamespace(id=user_id)

        db = RecordingDB(responses=[
            DummyResult(values=[user]),
        ])

        with patch("app.core.security.decode_access_token", return_value={"sub": str(user_id)}):
            with patch("app.core.permissions.check_agent_access", new_callable=AsyncMock, return_value=(agent, "manage")):
                result = await get_token_key(agent_id, authorization="Bearer jwt.token.here", db=db)

        assert result["token_key"].startswith("clw_")
        assert result["token_key_suffix"] == result["token_key"][-4:]
        assert db.committed is True  # key was persisted

    @pytest.mark.asyncio
    async def test_non_manage_access_returns_403(self):
        """Use-only access should be denied."""
        agent_id = uuid.uuid4()
        user_id = uuid.uuid4()
        agent = _make_agent(token_key="clw_existing")
        user = SimpleNamespace(id=user_id)

        db = RecordingDB(responses=[
            DummyResult(values=[user]),
        ])

        with patch("app.core.security.decode_access_token", return_value={"sub": str(user_id)}):
            with patch("app.core.permissions.check_agent_access", new_callable=AsyncMock, return_value=(agent, "use")):
                with pytest.raises(HTTPException) as exc:
                    await get_token_key(agent_id, authorization="Bearer jwt.token.here", db=db)
        assert exc.value.status_code == 403


# ---------------------------------------------------------------------------
# regenerate_token_key endpoint tests
# ---------------------------------------------------------------------------


class TestRegenerateTokenKey:
    @pytest.mark.asyncio
    async def test_regenerate_produces_new_key(self):
        """Regenerating creates a fresh key and returns it."""
        agent_id = uuid.uuid4()
        user_id = uuid.uuid4()
        old_key = "clw_oldkey1234567890abcdef12345678"
        agent = _make_agent(token_key=old_key, token_key_suffix=old_key[-4:])
        agent.id = agent_id
        user = SimpleNamespace(id=user_id)

        db = RecordingDB(responses=[
            DummyResult(values=[user]),
        ])

        with patch("app.core.security.decode_access_token", return_value={"sub": str(user_id)}):
            with patch("app.core.permissions.check_agent_access", new_callable=AsyncMock, return_value=(agent, "manage")):
                result = await regenerate_token_key(agent_id, authorization="Bearer jwt.token.here", db=db)

        assert result["token_key"].startswith("clw_")
        assert result["token_key"] != old_key
        assert result["token_key_suffix"] == result["token_key"][-4:]
        assert db.committed is True
        # Agent object should be updated
        assert agent.token_key == result["token_key"]
        assert agent.token_key_suffix == result["token_key_suffix"]

    @pytest.mark.asyncio
    async def test_regenerate_non_manage_returns_403(self):
        """Use-only access on regenerate should be denied."""
        agent_id = uuid.uuid4()
        user_id = uuid.uuid4()
        agent = _make_agent(token_key="clw_key")
        user = SimpleNamespace(id=user_id)

        db = RecordingDB(responses=[
            DummyResult(values=[user]),
        ])

        with patch("app.core.security.decode_access_token", return_value={"sub": str(user_id)}):
            with patch("app.core.permissions.check_agent_access", new_callable=AsyncMock, return_value=(agent, "use")):
                with pytest.raises(HTTPException) as exc:
                    await regenerate_token_key(agent_id, authorization="Bearer jwt.token.here", db=db)
        assert exc.value.status_code == 403

    @pytest.mark.asyncio
    async def test_regenerate_missing_bearer(self):
        """Missing Bearer prefix returns 401."""
        with pytest.raises(HTTPException) as exc:
            await regenerate_token_key(uuid.uuid4(), authorization="Token xyz")
        assert exc.value.status_code == 401


# ---------------------------------------------------------------------------
# Integration-style: agent creation with token key
# ---------------------------------------------------------------------------


class TestAgentCreationTokenKey:
    def test_generate_token_key_imported_by_agents(self):
        """Ensure generate_token_key is importable from agent_api module."""
        from app.api.agent_api import generate_token_key as gk
        key, suffix = gk()
        assert key.startswith("clw_")
        assert len(suffix) == 4


# ---------------------------------------------------------------------------
# Schema validation tests
# ---------------------------------------------------------------------------


class TestSchemas:
    def test_agent_api_chat_request_schema(self):
        """AgentApiChatRequest should accept valid data."""
        from app.schemas.schemas import AgentApiChatRequest
        req = AgentApiChatRequest(agent_id=uuid.uuid4(), prompt="Hello world")
        assert req.prompt == "Hello world"

    def test_agent_api_chat_request_empty_prompt_rejected(self):
        """AgentApiChatRequest should reject empty prompt."""
        from app.schemas.schemas import AgentApiChatRequest
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            AgentApiChatRequest(agent_id=uuid.uuid4(), prompt="")

    def test_agent_api_chat_response_schema(self):
        """AgentApiChatResponse should serialize properly."""
        from app.schemas.schemas import AgentApiChatResponse
        resp = AgentApiChatResponse(reply="Hello!", usage={"total_tokens": 100})
        assert resp.reply == "Hello!"
        assert resp.usage["total_tokens"] == 100

    def test_agent_out_has_token_key_suffix(self):
        """AgentOut schema should include token_key_suffix field."""
        from app.schemas.schemas import AgentOut
        fields = AgentOut.model_fields
        assert "token_key_suffix" in fields
