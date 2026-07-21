"""Immutable group Runtime context snapshot tests."""

from __future__ import annotations

from collections import deque
from datetime import UTC, datetime
import uuid

import pytest

from app.config import Settings
from app.models.agent import Agent
from app.models.audit import ChatMessage
from app.models.chat_session import ChatSession
from app.models.group import Group, GroupMember
from app.models.org import OrgMember
from app.models.participant import Participant
from app.models.user import User
from app.services import group_file_service
from app.services.agent_runtime.context_builder import ContextBuildError
from app.services.agent_runtime.group_context_builder import GroupContextBuilder


NOW = datetime(2026, 7, 14, 12, 0, tzinfo=UTC)


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


def _participant(kind: str, ref_id: uuid.UUID, name: str) -> Participant:
    return Participant(
        id=uuid.uuid4(),
        type=kind,
        ref_id=ref_id,
        display_name=name,
    )


@pytest.mark.asyncio
async def test_group_context_freezes_authoritative_scope_files_and_sender_metadata(
    monkeypatch,
) -> None:
    tenant_id = uuid.uuid4()
    group_id = uuid.uuid4()
    session_id = uuid.uuid4()
    agent_id = uuid.uuid4()
    user_id = uuid.uuid4()
    target = _participant("agent", agent_id, "Research Agent")
    sender = _participant("user", user_id, "Alice")
    group = Group(
        id=group_id,
        tenant_id=tenant_id,
        name="Launch",
        description="Ship the release",
        created_by_participant_id=sender.id,
        created_at=NOW,
        updated_at=NOW,
    )
    membership = GroupMember(
        id=uuid.uuid4(),
        group_id=group_id,
        participant_id=target.id,
        role="member",
        joined_at=NOW,
        session_read_state={},
    )
    session = ChatSession(
        id=session_id,
        tenant_id=tenant_id,
        session_type="group",
        group_id=group_id,
        title="Launch plan",
        source_channel="web",
        is_group=True,
        is_primary=True,
        created_at=NOW,
        updated_at=NOW,
    )
    trigger = ChatMessage(
        id=uuid.uuid4(),
        role="user",
        content="@Research Agent prepare the plan",
        conversation_id=str(session_id),
        participant_id=sender.id,
        mentions=[
            {
                "participant_id": str(target.id),
                "participant_type": "agent",
                "participant_ref_id": str(agent_id),
                "display_name": target.display_name,
                "valid": True,
                "triggers_agent": True,
                "reason": None,
            }
        ],
        created_at=NOW,
    )
    agent = Agent(
        id=agent_id,
        tenant_id=tenant_id,
        creator_id=user_id,
        name="Research Agent",
        role_description="Investigates launch risks",
        status="idle",
        is_expired=False,
    )
    user = User(
        id=user_id,
        tenant_id=tenant_id,
        display_name="Alice",
        title="PM",
        role="member",
        is_active=True,
    )
    org_member = OrgMember(
        id=uuid.uuid4(),
        name="Alice",
        title="Product Lead",
        department_path="/Product/Launch",
        tenant_id=tenant_id,
        user_id=user_id,
        status="active",
    )
    db = _DB(
        _Result([trigger]),
        _Result([agent]),
        _Result([user]),
        _Result([org_member]),
        _Result([sender]),
        _Result([sender]),
    )

    async def authorize_session(_db, **_kwargs):
        return session

    async def authorize_member(_db, **kwargs):
        participant = target if kwargs["participant_id"] == target.id else sender
        return group, membership, participant

    async def announcement(*_args, **_kwargs):
        return group_file_service.GroupTextFile(
            path="announcement.md",
            content="123456789",
            exists=True,
            version_token="a1",
            modified_at="now",
        )

    async def memory(*_args, **_kwargs):
        return group_file_service.GroupTextFile(
            path="memory.md",
            content="abcdefghi",
            exists=True,
            version_token="m1",
            modified_at="now",
        )

    async def workspace(*_args, **_kwargs):
        return (
            group_file_service.GroupWorkspaceEntry(
                path="reports/final.md",
                name="final.md",
                is_dir=False,
                size=42,
                modified_at="now",
                version_token="w1",
            ),
        )

    monkeypatch.setattr(
        "app.services.agent_runtime.group_context_builder.group_chat_service.authorize_group_session",
        authorize_session,
    )
    monkeypatch.setattr(
        "app.services.agent_runtime.group_context_builder.group_chat_service.authorize_group_member",
        authorize_member,
    )
    monkeypatch.setattr(
        "app.services.agent_runtime.group_context_builder.group_file_service.read_announcement",
        announcement,
    )
    monkeypatch.setattr(
        "app.services.agent_runtime.group_context_builder.group_file_service.read_agent_memory",
        memory,
    )
    monkeypatch.setattr(
        "app.services.agent_runtime.group_context_builder.group_file_service.index_workspace",
        workspace,
    )
    builder = GroupContextBuilder(
        settings=Settings(
            GROUP_CONTEXT_ANNOUNCEMENT_MAX_CHARS=5,
            GROUP_CONTEXT_MEMORY_MAX_CHARS=6,
            GROUP_CONTEXT_WORKSPACE_MAX_ENTRIES=10,
        )
    )

    captured = await builder.capture(
        db,
        tenant_id=tenant_id,
        session_id=session_id,
        agent_id=agent_id,
        initial_input={
            "message_id": str(trigger.id),
            "group_id": str(group_id),
            "session_id": str(session_id),
            "sender_participant_id": str(sender.id),
            "target_participant_id": str(target.id),
            "mention_targets": [{"participant_id": str(uuid.uuid4())}],
            "current_responsibility": "Prepare the risk plan",
            "mode": "enforced",
            "plan_prompt": "Research, then hand off to review.",
        },
        pending_messages=(
            {
                "id": str(uuid.uuid4()),
                "role": "assistant",
                "content": "Earlier group context",
                "created_at": NOW.isoformat(),
                "participant_id": str(sender.id),
                "mentions": [],
            },
        ),
        recent_messages=(
            {
                "id": str(trigger.id),
                "role": "user",
                "content": trigger.content,
                "created_at": NOW.isoformat(),
                "participant_id": str(sender.id),
                "mentions": [],
            },
        ),
    )

    context = captured.initial_input["group_context"]
    assert context["trigger"]["content"] == trigger.content
    assert context["trigger"]["mention_targets"][0]["participant_id"] == str(target.id)
    assert context["trigger"]["sender"]["title"] == "Product Lead"
    assert context["trigger"]["sender"]["department"] == "/Product/Launch"
    assert context["agent"]["agent_id"] == str(agent_id)
    assert context["announcement"] == {
        "source": "group announcement",
        "content": "12345",
        "truncated": True,
        "original_chars": 9,
    }
    assert context["agent_group_memory"]["content"] == "abcdef"
    assert context["workspace_index"][0]["path"] == "reports/final.md"
    assert "scope_rules" not in context
    assert "role_description" not in context["agent"]
    assert "tool_permissions" not in context["agent"]
    assert context["planning_hint"] == {
        "mode": "enforced",
        "plan_prompt": "Research, then hand off to review.",
        "current_responsibility": "Prepare the risk plan",
    }
    assert "planning_step_id" not in captured.initial_input
    assert "planning_instruction" not in captured.initial_input
    assert "related_run_summaries" not in context
    assert captured.pending_messages[0]["sender_name"] == "Alice"
    assert captured.recent_messages[0]["sender_name"] == "Alice"
    assert captured.recent_messages[0]["sender_type"] == "user"


@pytest.mark.asyncio
async def test_group_context_rejects_target_agent_identity_mismatch(monkeypatch) -> None:
    tenant_id = uuid.uuid4()
    group_id = uuid.uuid4()
    session_id = uuid.uuid4()
    target = _participant("agent", uuid.uuid4(), "Target")
    sender = _participant("user", uuid.uuid4(), "Sender")
    group = Group(
        id=group_id,
        tenant_id=tenant_id,
        name="Group",
        created_by_participant_id=sender.id,
        created_at=NOW,
        updated_at=NOW,
    )
    membership = GroupMember(
        id=uuid.uuid4(),
        group_id=group_id,
        participant_id=target.id,
        role="member",
        joined_at=NOW,
        session_read_state={},
    )
    session = ChatSession(
        id=session_id,
        tenant_id=tenant_id,
        session_type="group",
        group_id=group_id,
        title="Session",
        source_channel="web",
        is_group=True,
        is_primary=True,
        created_at=NOW,
        updated_at=NOW,
    )

    async def authorize_session(_db, **_kwargs):
        return session

    async def authorize_member(_db, **kwargs):
        participant = target if kwargs["participant_id"] == target.id else sender
        return group, membership, participant

    monkeypatch.setattr(
        "app.services.agent_runtime.group_context_builder.group_chat_service.authorize_group_session",
        authorize_session,
    )
    monkeypatch.setattr(
        "app.services.agent_runtime.group_context_builder.group_chat_service.authorize_group_member",
        authorize_member,
    )

    with pytest.raises(ContextBuildError) as exc_info:
        await GroupContextBuilder(settings=Settings()).capture(
            _DB(),
            tenant_id=tenant_id,
            session_id=session_id,
            agent_id=uuid.uuid4(),
            initial_input={
                "message_id": str(uuid.uuid4()),
                "group_id": str(group_id),
                "session_id": str(session_id),
                "sender_participant_id": str(sender.id),
                "target_participant_id": str(target.id),
            },
            recent_messages=(),
        )

    assert exc_info.value.code == "invalid_group_runtime_scope"
