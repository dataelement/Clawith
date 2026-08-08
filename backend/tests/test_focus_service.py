from types import SimpleNamespace
from unittest.mock import AsyncMock
import uuid

import pytest

from app.services import focus_service


class _Result:
    def __init__(self, item):
        self.item = item

    def scalar_one_or_none(self):
        return self.item


@pytest.mark.asyncio
async def test_upsert_with_caller_session_refreshes_before_serializing(monkeypatch):
    """Server-generated timestamps must be loaded inside the async context."""
    item = SimpleNamespace(
        title=None,
        description="Previous description",
        status="in_progress",
        kind="normal",
        source="user",
        item_metadata={},
        completed_at=None,
    )
    events: list[str] = []

    async def flush():
        events.append("flush")

    async def refresh(value):
        assert value is item
        events.append("refresh")

    session = SimpleNamespace(
        execute=AsyncMock(return_value=_Result(item)),
        flush=flush,
        refresh=refresh,
    )
    agent_id = uuid.uuid4()
    item_key = "system:okr_reports"
    monkeypatch.setattr(focus_service, "migrate_legacy_focus_file", AsyncMock(return_value=None))
    monkeypatch.setattr(focus_service, "_serialize_focus_item", lambda value: {"key": item_key})
    monkeypatch.setattr(focus_service.focus_dao, "upsert_item", AsyncMock(return_value=item))

    result = await focus_service.upsert_focus_item(
        agent_id=agent_id,
        key=item_key,
        title=None,
        description="OKR reports",
        status="in_progress",
        kind="system",
        source="trigger",
        metadata=None,
        db=session,
    )

    assert result == {"key": item_key}
    focus_service.focus_dao.upsert_item.assert_awaited_once()
