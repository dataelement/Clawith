"""Model test contract for native Agent tool-calling capability."""

from types import SimpleNamespace
from unittest.mock import AsyncMock
from datetime import UTC, datetime
import json
import uuid

import pytest

from app.api import enterprise
from app.models.llm import LLMModel
from app.schemas.schemas import LLMModelUpdate
from app.services.llm.client import LLMResponse


class _Client:
    def __init__(self, *responses: LLMResponse | Exception) -> None:
        self.responses = list(responses)
        self.calls: list[dict] = []
        self.closed = False

    async def complete(self, **kwargs):
        self.calls.append(kwargs)
        result = self.responses.pop(0)
        if isinstance(result, Exception):
            raise result
        return result

    async def close(self) -> None:
        self.closed = True


def _target(*, model_id: uuid.UUID | None = None):
    return enterprise.LLMTestTarget(
        model_id=model_id,
        provider="ollama",
        model="qwen-local",
        api_key="ollama",
        base_url="http://localhost:11434/v1",
        stored_config_fingerprint="stored-fingerprint" if model_id else None,
    )


@pytest.mark.asyncio
async def test_unsaved_draft_test_separates_capabilities_but_does_not_record_them(
    monkeypatch,
) -> None:
    client = _Client(
        LLMResponse(content="ok"),
        LLMResponse(
            content="",
            tool_calls=[
                {
                    "id": "probe-finish",
                    "type": "function",
                    "function": {
                        "name": "finish",
                        "arguments": json.dumps({"content": "ok"}),
                    },
                }
            ],
        ),
    )
    monkeypatch.setattr(
        enterprise,
        "_resolve_llm_test_target",
        AsyncMock(return_value=_target()),
    )
    monkeypatch.setattr(enterprise, "create_llm_client", lambda **_kwargs: client)

    result = await enterprise.test_llm_model(
        enterprise.LLMTestRequest(
            provider="ollama",
            model="qwen-local",
            api_key="ollama",
            base_url="http://localhost:11434/v1",
        ),
        current_user=SimpleNamespace(id=uuid.uuid4(), role="admin", tenant_id=uuid.uuid4()),
    )

    assert result["success"] is True
    assert result["connection_success"] is True
    assert result["tool_calling_supported"] is True
    assert result["capability_recorded"] is False
    assert len(client.calls) == 2
    assert client.calls[0]["tools"] is None
    assert [tool["function"]["name"] for tool in client.calls[1]["tools"]] == [
        "finish"
    ]
    assert client.closed is True


@pytest.mark.asyncio
async def test_tool_probe_transport_failure_records_unknown_not_unsupported(
    monkeypatch,
) -> None:
    model_id = uuid.uuid4()
    target = _target(model_id=model_id)
    client = _Client(LLMResponse(content="ok"), TimeoutError("local model busy"))
    record = AsyncMock(return_value=True)
    monkeypatch.setattr(
        enterprise,
        "_resolve_llm_test_target",
        AsyncMock(return_value=target),
    )
    monkeypatch.setattr(enterprise, "_record_llm_tool_capability", record)
    monkeypatch.setattr(enterprise, "create_llm_client", lambda **_kwargs: client)

    result = await enterprise.test_llm_model(
        enterprise.LLMTestRequest(
            provider="ollama",
            model="qwen-local",
            model_id=str(model_id),
        ),
        current_user=SimpleNamespace(id=uuid.uuid4(), role="admin", tenant_id=uuid.uuid4()),
    )

    assert result["success"] is False
    assert result["connection_success"] is True
    assert result["tool_calling_supported"] is None
    assert "TimeoutError" in result["tool_calling_error"]
    assert record.await_args.kwargs["supported"] is None
    assert client.closed is True


@pytest.mark.asyncio
async def test_plain_text_probe_is_not_reported_as_agent_compatible_and_is_recorded(
    monkeypatch,
) -> None:
    model_id = uuid.uuid4()
    target = _target(model_id=model_id)
    client = _Client(
        LLMResponse(content="ok"),
        LLMResponse(content="I am done", tool_calls=[]),
    )
    record = AsyncMock(return_value=True)
    monkeypatch.setattr(
        enterprise,
        "_resolve_llm_test_target",
        AsyncMock(return_value=target),
    )
    monkeypatch.setattr(enterprise, "_record_llm_tool_capability", record)
    monkeypatch.setattr(enterprise, "create_llm_client", lambda **_kwargs: client)

    result = await enterprise.test_llm_model(
        enterprise.LLMTestRequest(
            provider="ollama",
            model="qwen-local",
            model_id=str(model_id),
        ),
        current_user=SimpleNamespace(id=uuid.uuid4(), role="admin", tenant_id=uuid.uuid4()),
    )

    assert result["success"] is False
    assert result["connection_success"] is True
    assert result["tool_calling_supported"] is False
    assert result["capability_recorded"] is True
    assert "plain text" in result["tool_calling_error"].lower()
    record.assert_awaited_once()
    assert record.await_args.args[0] is target
    assert record.await_args.kwargs["supported"] is False
    assert client.closed is True


class _Result:
    def __init__(self, model: LLMModel) -> None:
        self.model = model

    def scalar_one_or_none(self) -> LLMModel:
        return self.model


class _DB:
    def __init__(self, model: LLMModel) -> None:
        self.model = model
        self.committed = False
        self.refreshed = False

    async def execute(self, _statement):
        return _Result(self.model)

    async def commit(self) -> None:
        self.committed = True

    async def refresh(self, model: LLMModel) -> None:
        assert model is self.model
        self.refreshed = True

    async def rollback(self) -> None:
        raise AssertionError("update should not roll back")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "update",
    [
        LLMModelUpdate(provider="custom"),
        LLMModelUpdate(model="new-model"),
        LLMModelUpdate(base_url="http://localhost:8000/v1"),
        LLMModelUpdate(api_key="new-local-key"),
    ],
)
async def test_updating_model_identity_invalidates_prior_tool_probe(
    update: LLMModelUpdate,
) -> None:
    tenant_id = uuid.uuid4()
    checked_at = datetime.now(UTC)
    model = LLMModel(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        provider="ollama",
        model="old-model",
        api_key_encrypted="stored-key",
        label="Local",
        enabled=True,
        supports_vision=False,
        supports_tool_calling=True,
        tool_calling_capability_source="probe",
        tool_calling_checked_at=checked_at,
        tool_calling_error=None,
        created_at=checked_at,
    )
    db = _DB(model)

    updated = await enterprise.update_llm_model(
        model.id,
        update,
        current_user=SimpleNamespace(tenant_id=tenant_id, role="admin"),
        db=db,  # type: ignore[arg-type]
    )

    assert updated.supports_tool_calling is None
    assert updated.tool_calling_capability_source is None
    assert updated.tool_calling_checked_at is None
    assert "changed" in (updated.tool_calling_error or "").lower()
    assert db.committed is True
    assert db.refreshed is True
