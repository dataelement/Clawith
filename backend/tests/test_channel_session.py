"""Unified Schema invariants for external-channel sessions."""

from __future__ import annotations

from collections import deque
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
import uuid

import pytest

from app.models.chat_session import ChatSession
from app.services.channel_session import find_or_create_channel_session


class _Result:
    def __init__(self, value: object) -> None:
        self.value = value

    def scalar_one_or_none(self):
        return self.value


class _Session:
    def __init__(self, *results: object) -> None:
        self.results = deque(results)
        self.added: list[object] = []
        self.flushes = 0

    async def execute(self, _statement):
        return _Result(self.results.popleft())

    def add(self, value: object) -> None:
        self.added.append(value)

    async def flush(self) -> None:
        self.flushes += 1


@pytest.mark.asyncio
async def test_external_group_session_writes_required_unified_schema_fields() -> None:
    tenant_id = uuid.uuid4()
    agent_id = uuid.uuid4()
    owner_id = uuid.uuid4()
    sender_id = uuid.uuid4()
    agent = SimpleNamespace(id=agent_id, tenant_id=tenant_id)
    sender = SimpleNamespace(
        id=sender_id,
        display_name="Ada",
        avatar_url=None,
    )
    owner = SimpleNamespace(id=owner_id)
    participant = SimpleNamespace(id=uuid.uuid4())
    db = _Session(agent, sender, owner, None, None)

    with patch(
        "app.services.channel_session.get_or_create_user_participant",
        new=AsyncMock(return_value=participant),
    ):
        session = await find_or_create_channel_session(
            db,  # type: ignore[arg-type]
            agent_id=agent_id,
            user_id=owner_id,
            created_by_user_id=sender_id,
            external_conv_id="feishu_group_oc_123",
            source_channel="feishu",
            first_message_title="Review this",
            is_group=True,
            group_name="Delivery Group",
        )

    assert isinstance(session, ChatSession)
    assert session.tenant_id == tenant_id
    assert session.session_type == "group"
    assert session.group_id is None
    assert session.agent_id == agent_id
    assert session.user_id == owner_id
    assert session.created_by_participant_id == participant.id
    assert session.external_conv_id == "feishu_group_oc_123"
    assert session.source_channel == "feishu"
    assert session.is_primary is False
    assert db.added == [session]
    assert db.flushes == 1
