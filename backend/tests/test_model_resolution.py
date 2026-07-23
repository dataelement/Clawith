import uuid
from types import SimpleNamespace

import pytest


class DummyResult:
    def __init__(self, values=()):
        self._values = list(values)

    def scalar_one_or_none(self):
        return self._values[0] if self._values else None

    def scalars(self):
        return self

    def all(self):
        return list(self._values)


class SequenceDB:
    def __init__(self, responses):
        self.responses = list(responses)
        self.statements = []

    async def execute(self, statement):
        self.statements.append(statement)
        return self.responses.pop(0)


def make_model(model_id, tenant_id, **overrides):
    values = {
        "id": model_id,
        "tenant_id": tenant_id,
        "enabled": True,
        "deleted_at": None,
        "supports_tool_calling": True,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


@pytest.mark.asyncio
async def test_candidates_follow_primary_fallback_tenant_default_order():
    from app.services.llm.model_resolution import active_agent_model_candidates

    tenant_id = uuid.uuid4()
    primary_id = uuid.uuid4()
    fallback_id = uuid.uuid4()
    default_id = uuid.uuid4()
    agent = SimpleNamespace(
        tenant_id=tenant_id,
        primary_model_id=primary_id,
        fallback_model_id=fallback_id,
    )
    primary = make_model(primary_id, tenant_id)
    fallback = make_model(fallback_id, tenant_id)
    default = make_model(default_id, tenant_id)
    db = SequenceDB([
        DummyResult([default_id]),
        DummyResult([default, fallback, primary]),
    ])

    candidates = await active_agent_model_candidates(db, agent)

    assert candidates == (primary, fallback, default)
    assert "llm_models.deleted_at IS NULL" in str(db.statements[1])


@pytest.mark.asyncio
async def test_candidates_skip_deleted_disabled_cross_tenant_and_duplicate_models():
    from app.services.llm.model_resolution import active_agent_model_candidates

    tenant_id = uuid.uuid4()
    deleted_id = uuid.uuid4()
    fallback_id = uuid.uuid4()
    agent = SimpleNamespace(
        tenant_id=tenant_id,
        primary_model_id=deleted_id,
        fallback_model_id=fallback_id,
    )
    deleted = make_model(deleted_id, tenant_id, deleted_at=object())
    fallback = make_model(fallback_id, tenant_id)
    db = SequenceDB([
        DummyResult([fallback_id]),
        DummyResult([deleted, fallback]),
    ])

    candidates = await active_agent_model_candidates(db, agent)

    assert candidates == (fallback,)


@pytest.mark.asyncio
async def test_tool_calling_diagnostic_does_not_filter_saved_candidate():
    from app.services.llm.model_resolution import active_agent_model_candidates

    tenant_id = uuid.uuid4()
    primary_id = uuid.uuid4()
    fallback_id = uuid.uuid4()
    agent = SimpleNamespace(
        tenant_id=tenant_id,
        primary_model_id=primary_id,
        fallback_model_id=fallback_id,
    )
    primary = make_model(primary_id, tenant_id, supports_tool_calling=False)
    fallback = make_model(fallback_id, tenant_id)
    db = SequenceDB([
        DummyResult(),
        DummyResult([primary, fallback]),
    ])

    candidates = await active_agent_model_candidates(db, agent)

    assert candidates == (primary, fallback)


@pytest.mark.asyncio
async def test_deleted_agent_has_no_model_candidates():
    from app.services.llm.model_resolution import active_agent_model_candidates

    agent = SimpleNamespace(
        tenant_id=uuid.uuid4(),
        primary_model_id=uuid.uuid4(),
        fallback_model_id=uuid.uuid4(),
        deleted_at=object(),
    )
    db = SequenceDB([])

    assert await active_agent_model_candidates(db, agent) == ()
    assert db.statements == []
