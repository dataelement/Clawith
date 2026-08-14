"""Regression contracts for tenant-safe chat persistence and LLM budgets."""

from __future__ import annotations

import ast
import uuid
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.api import enterprise
from app.models.audit import ChatMessage
from app.models.llm import LLMModel
from app.schemas.schemas import LLMModelUpdate


def test_chat_message_tenant_is_required_by_the_database_model() -> None:
    assert ChatMessage.__table__.c.tenant_id.nullable is False


def test_every_product_chat_message_constructor_has_explicit_tenant() -> None:
    app_root = Path(__file__).parents[1] / "app"
    missing: list[str] = []
    for path in app_root.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if not isinstance(node.func, ast.Name) or node.func.id != "ChatMessage":
                continue
            if not any(keyword.arg == "tenant_id" for keyword in node.keywords):
                missing.append(f"{path.relative_to(app_root)}:{node.lineno}")
    assert missing == []


def test_shared_context_must_leave_room_for_input() -> None:
    with pytest.raises(HTTPException) as raised:
        enterprise._validate_llm_token_limits(
            max_output_tokens=262_144,
            context_window_tokens=262_144,
        )
    assert raised.value.status_code == 422


class _Result:
    def __init__(self, model: LLMModel) -> None:
        self.model = model

    def scalar_one_or_none(self) -> LLMModel:
        return self.model


class _DB:
    def __init__(self, model: LLMModel) -> None:
        self.model = model

    async def execute(self, _statement) -> _Result:
        return _Result(self.model)

    async def commit(self) -> None:
        return None

    async def refresh(self, _model: LLMModel) -> None:
        return None

    async def rollback(self) -> None:
        raise AssertionError("valid update must not roll back")


@pytest.mark.asyncio
async def test_optional_llm_limits_can_be_cleared() -> None:
    tenant_id = uuid.uuid4()
    model = LLMModel(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        provider="custom",
        model="test-model",
        api_key_encrypted="stored-key",
        label="Test",
        enabled=True,
        supports_vision=False,
        max_output_tokens=8_192,
        context_window_tokens=131_072,
        request_timeout=120,
        created_at=datetime.now(UTC),
    )

    await enterprise.update_llm_model(
        model.id,
        LLMModelUpdate(
            max_output_tokens=None,
            context_window_tokens=None,
            request_timeout=None,
        ),
        current_user=SimpleNamespace(tenant_id=tenant_id, role="org_admin"),
        db=_DB(model),  # type: ignore[arg-type]
    )

    assert model.max_output_tokens is None
    assert model.context_window_tokens is None
    assert model.request_timeout is None
