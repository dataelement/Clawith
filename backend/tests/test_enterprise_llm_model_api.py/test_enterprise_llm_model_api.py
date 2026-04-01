import uuid
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from app.api import enterprise as enterprise_api
from app.models.llm import LLMModel
from app.schemas.schemas import LLMModelCreate, LLMModelUpdate


class DummyResult:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class RecordingDB:
    def __init__(self, *, execute_results=None):
        self.execute_results = list(execute_results or [])
        self.added = []
        self.flushed = False
        self.committed = False
        self.refreshed = []

    def add(self, obj):
        self.added.append(obj)

    async def flush(self):
        for obj in self.added:
            if getattr(obj, "id", None) is None:
                obj.id = uuid.uuid4()
            if getattr(obj, "created_at", None) is None:
                obj.created_at = datetime.now(UTC)
        self.flushed = True

    async def execute(self, _statement):
        return DummyResult(self.execute_results.pop(0) if self.execute_results else None)

    async def commit(self):
        self.committed = True

    async def refresh(self, obj):
        if getattr(obj, "id", None) is None:
            obj.id = uuid.uuid4()
        if getattr(obj, "created_at", None) is None:
            obj.created_at = datetime.now(UTC)
        self.refreshed.append(obj)


@pytest.mark.asyncio
async def test_add_llm_model_persists_temperature_and_max_output_tokens():
    tenant_id = uuid.uuid4()
    db = RecordingDB()
    current_user = SimpleNamespace(tenant_id=tenant_id)

    created = await enterprise_api.add_llm_model(
        data=LLMModelCreate(
            provider="openai",
            model="gpt-4.1",
            api_key="sk-test",
            base_url="https://api.example.com/v1",
            label="Primary",
            temperature=0.4,
            max_tokens_per_day=1000,
            enabled=True,
            supports_vision=False,
            max_output_tokens=4096,
        ),
        tenant_id=str(tenant_id),
        current_user=current_user,
        db=db,
    )

    assert db.flushed is True
    assert len(db.added) == 1
    model = db.added[0]
    assert model.temperature == 0.4
    assert model.max_output_tokens == 4096
    assert created.temperature == 0.4
    assert created.max_output_tokens == 4096


@pytest.mark.asyncio
async def test_update_llm_model_persists_temperature_and_max_output_tokens():
    existing = LLMModel(
        provider="openai",
        model="gpt-4.1",
        api_key_encrypted="sk-test",
        base_url="https://api.example.com/v1",
        label="Primary",
        temperature=None,
        max_tokens_per_day=1000,
        enabled=True,
        supports_vision=False,
        max_output_tokens=None,
    )
    existing.id = uuid.uuid4()
    existing.created_at = datetime.now(UTC)
    db = RecordingDB(execute_results=[existing])

    updated = await enterprise_api.update_llm_model(
        model_id=existing.id,
        data=LLMModelUpdate(
            temperature=0.2,
            max_output_tokens=2048,
        ),
        current_user=SimpleNamespace(),
        db=db,
    )

    assert db.committed is True
    assert db.refreshed == [existing]
    assert existing.temperature == 0.2
    assert existing.max_output_tokens == 2048
    assert updated.temperature == 0.2
    assert updated.max_output_tokens == 2048
