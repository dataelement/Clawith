import uuid
from types import SimpleNamespace

import pytest

from app.services import experience_retrieval


class _Result:
    def __init__(self, values):
        self._values = values

    def scalars(self):
        return self

    def all(self):
        return list(self._values)


class _Session:
    def __init__(self, *, valid, existing):
        self._results = iter((_Result(valid), _Result(existing)))
        self.added = []
        self.commits = 0

    async def execute(self, _statement):
        return next(self._results)

    def add(self, value):
        self.added.append(value)

    async def commit(self):
        self.commits += 1


class _SessionContext:
    def __init__(self, session):
        self._session = session

    async def __aenter__(self):
        return self._session

    async def __aexit__(self, exc_type, exc, traceback):
        return False


@pytest.mark.asyncio
@pytest.mark.parametrize("already_recorded", [False, True])
async def test_record_experience_citations_is_idempotent_per_message(
    monkeypatch,
    already_recorded,
):
    agent_id = uuid.uuid4()
    tenant_id = uuid.uuid4()
    entry_id = uuid.uuid4()
    session_id = uuid.uuid4()
    message_id = uuid.uuid4()
    session = _Session(
        valid=[entry_id],
        existing=[entry_id] if already_recorded else [],
    )

    async def resolve_agent(_db, requested_agent_id):
        assert requested_agent_id == agent_id
        return SimpleNamespace(id=agent_id, tenant_id=tenant_id)

    async def department_ids(_db, _agent):
        return []

    monkeypatch.setattr(experience_retrieval, "_resolve_agent", resolve_agent)
    monkeypatch.setattr(experience_retrieval, "_agent_department_ids", department_ids)
    monkeypatch.setattr(
        experience_retrieval,
        "async_session",
        lambda: _SessionContext(session),
    )

    recorded = await experience_retrieval.record_experience_citations(
        f"Used [[exp:{entry_id}]]",
        agent_id=agent_id,
        session_id=session_id,
        message_id=message_id,
    )

    assert recorded == (0 if already_recorded else 1)
    assert session.commits == (0 if already_recorded else 1)
    assert len(session.added) == (0 if already_recorded else 1)
    if session.added:
        citation = session.added[0]
        assert citation.entry_id == entry_id
        assert citation.kind == "cited"
        assert citation.tenant_id == tenant_id
        assert citation.agent_id == agent_id
        assert citation.session_id == session_id
        assert citation.message_id == message_id
