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
    monkeypatch.setattr(focus_service, "_serialize_focus_item", lambda value: {"key": "system:okr_reports"})

    result = await focus_service._upsert_focus_item_impl(
        session,
        uuid.uuid4(),
        "system:okr_reports",
        None,
        "OKR reports",
        "in_progress",
        "system",
        "trigger",
        None,
        should_commit=False,
    )

    assert result == {"key": "system:okr_reports"}
    assert events == ["flush", "refresh"]
