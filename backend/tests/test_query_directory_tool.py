import json
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.services import agent_directory, agent_tools, tool_seeder


def _make_agent(**overrides):
    values = {
        "id": uuid.uuid4(),
        "name": "OKR Assistant",
        "role_description": "Tracks OKR progress",
        "tenant_id": uuid.uuid4(),
        "creator_id": uuid.uuid4(),
        "access_mode": "company",
        "status": "running",
        "is_expired": False,
        "expires_at": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _make_member(**overrides):
    values = {
        "id": uuid.uuid4(),
        "tenant_id": uuid.uuid4(),
        "user_id": None,
        "name": "张三",
        "title": "产品经理",
        "department_id": None,
        "department_path": "",
        "status": "active",
        "provider_id": None,
        "open_id": None,
        "external_id": None,
        "unionid": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _make_user(**overrides):
    values = {
        "id": uuid.uuid4(),
        "tenant_id": uuid.uuid4(),
        "display_name": "张三",
        "is_active": True,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


class DummyResult:
    def __init__(self, values=None, scalar_value=None):
        self._values = list(values or [])
        self._scalar_value = scalar_value

    def scalar_one_or_none(self):
        if self._scalar_value is not None:
            return self._scalar_value
        return self._values[0] if self._values else None

    def scalars(self):
        return self

    def all(self):
        return list(self._values)


class RecordingDB:
    def __init__(self, responses=None):
        self.responses = list(responses or [])
        self.execute_count = 0
        self.statements = []

    async def execute(self, statement, _params=None):
        self.execute_count += 1
        self.statements.append(statement)
        if not self.responses:
            raise AssertionError("unexpected execute() call")
        return self.responses.pop(0)


def test_query_directory_tool_is_available_to_agents():
    tool_names = {tool["function"]["name"] for tool in agent_tools.AGENT_TOOLS}

    assert "query_directory" in tool_names
    assert "query_directory" in agent_tools._ALWAYS_INCLUDE_CORE
    assert "query_directory" not in agent_tools._HIDDEN_FROM_LLM_TOOL_NAMES
    assert "query_roster" not in tool_names
    assert "query_roster" not in agent_tools._ALWAYS_INCLUDE_CORE
    assert "query_roster" in agent_tools._HIDDEN_FROM_LLM_TOOL_NAMES
    query_schema = next(tool["function"]["parameters"] for tool in agent_tools.AGENT_TOOLS if tool["function"]["name"] == "query_directory")
    assert "target_member_id" in query_schema["properties"]


def test_a2a_tools_expose_target_agent_id_not_agent_name():
    tools = {tool["function"]["name"]: tool["function"] for tool in agent_tools.AGENT_TOOLS}

    for tool_name in ("send_message_to_agent", "send_file_to_agent"):
        schema = tools[tool_name]["parameters"]
        assert "target_agent_id" in schema["properties"]
        assert "target_agent_id" in schema["required"]
        assert "agent_name" not in schema["properties"]
        assert "agent_name" not in schema["required"]


def test_seeded_a2a_tools_expose_target_agent_id_not_agent_name():
    tools = {tool["name"]: tool for tool in tool_seeder.BUILTIN_TOOLS}

    assert "query_directory" in tools
    assert "query_roster" not in tools

    for tool_name in ("send_message_to_agent", "send_file_to_agent"):
        schema = tools[tool_name]["parameters_schema"]
        assert "target_agent_id" in schema["properties"]
        assert "target_agent_id" in schema["required"]
        assert "agent_name" not in schema["properties"]
        assert "agent_name" not in schema["required"]


@pytest.mark.asyncio
async def test_query_directory_rejects_invalid_member_type_before_db():
    result = json.loads(await agent_tools._query_directory(uuid.uuid4(), {"member_type": "team"}))

    assert result["ok"] is False
    assert result["error"]["code"] == "invalid_member_type"


@pytest.mark.asyncio
async def test_query_directory_rejects_invalid_target_member_id_before_db():
    result = json.loads(await agent_tools._query_directory(uuid.uuid4(), {"target_member_id": "not-a-uuid"}))

    assert result["ok"] is False
    assert result["error"]["code"] == "invalid_target_member_id"


@pytest.mark.asyncio
async def test_query_directory_rejects_agent_type_with_target_member_id_before_db():
    result = json.loads(
        await agent_tools._query_directory(
            uuid.uuid4(),
            {"member_type": "agent", "target_member_id": str(uuid.uuid4())},
        )
    )

    assert result["ok"] is False
    assert result["error"]["code"] == "invalid_member_type"


@pytest.mark.asyncio
async def test_query_directory_target_member_id_returns_exact_human_without_agent_lookup():
    tenant_id = uuid.uuid4()
    source = _make_agent(tenant_id=tenant_id)
    user = _make_user(tenant_id=tenant_id)
    member = _make_member(tenant_id=tenant_id, user_id=user.id)
    db = RecordingDB(
        responses=[
            DummyResult(scalar_value=source),
            DummyResult(values=[(member, None, None, user)]),
        ]
    )

    with patch("app.services.agent_tools.async_session") as mock_session_ctx:
        mock_session_ctx.return_value.__aenter__ = AsyncMock(return_value=db)
        mock_session_ctx.return_value.__aexit__ = AsyncMock(return_value=False)

        result = json.loads(
            await agent_tools._query_directory(
                source.id,
                {"target_member_id": str(member.id), "query": "完全不匹配"},
            )
        )

    assert result["ok"] is True
    assert result["returned_count"] == 1
    assert result["members"][0]["member_type"] == "human"
    assert result["members"][0]["target_member_id"] == str(member.id)
    assert db.execute_count == 2
    assert "lower(org_members.name)" not in str(db.statements[1])


@pytest.mark.asyncio
async def test_query_directory_agent_list_uses_sql_offset_and_limit():
    tenant_id = uuid.uuid4()
    source = _make_agent(tenant_id=tenant_id)
    db = RecordingDB(
        responses=[
            DummyResult(scalar_value=source),
            DummyResult(values=[]),
        ]
    )

    result = await agent_directory.query_agent_directory(
        db,
        source_agent_id=source.id,
        member_type="agent",
        limit=20,
        offset=40,
    )

    assert result["ok"] is True
    statement = str(db.statements[1])
    assert "LIMIT" in statement
    assert "OFFSET" in statement


def test_format_roster_agent_returns_stable_id_and_contact_tool():
    tenant_id = uuid.uuid4()
    source = _make_agent(tenant_id=tenant_id)
    target = _make_agent(tenant_id=tenant_id, access_mode="company")

    payload = agent_tools._format_roster_agent(source, target)

    assert payload["member_type"] == "agent"
    assert payload["target_agent_id"] == str(target.id)
    assert payload["display_name"] == target.name
    assert payload["can_contact"] is True
    assert payload["contact_tools"] == ["send_message_to_agent"]


def test_format_roster_agent_marks_stopped_agent_uncontactable():
    tenant_id = uuid.uuid4()
    source = _make_agent(tenant_id=tenant_id)
    target = _make_agent(tenant_id=tenant_id, status="stopped")

    payload = agent_tools._format_roster_agent(source, target)

    assert payload["can_contact"] is False
    assert payload["contact_tools"] == []
    assert payload["unavailable_reason"] == "agent_stopped"


def test_format_roster_human_prefers_platform_then_channel_tools():
    tenant_id = uuid.uuid4()
    provider_id = uuid.uuid4()
    source = _make_agent(tenant_id=tenant_id)
    user = _make_user(tenant_id=tenant_id)
    member = _make_member(
        tenant_id=tenant_id,
        user_id=user.id,
        provider_id=provider_id,
        external_id="user_xxx",
    )
    provider = SimpleNamespace(id=provider_id, provider_type="feishu")
    department = SimpleNamespace(name="产品部")

    payload = agent_tools._format_roster_human(source, member, provider, department, user)

    assert payload["member_type"] == "human"
    assert payload["target_member_id"] == str(member.id)
    assert payload["platform_user_id"] == str(member.user_id)
    assert payload["department"]["name"] == "产品部"
    assert payload["contact_tools"] == ["send_platform_message", "send_channel_message"]
    assert payload["provider"]["provider_type"] == "feishu"
    assert payload["provider"]["external_id"] == "user_xxx"


def test_format_roster_human_requires_active_platform_user_for_platform_tool():
    tenant_id = uuid.uuid4()
    source = _make_agent(tenant_id=tenant_id)
    inactive_user = _make_user(tenant_id=tenant_id, is_active=False)
    member = _make_member(tenant_id=tenant_id, user_id=inactive_user.id)

    payload = agent_tools._format_roster_human(source, member, None, None, inactive_user)

    assert payload["can_contact"] is False
    assert "send_platform_message" not in payload["contact_tools"]
    assert payload["unavailable_reason"] == "missing_contact_target"


def test_format_roster_human_requires_channel_ready_identity():
    tenant_id = uuid.uuid4()
    source = _make_agent(tenant_id=tenant_id)
    feishu_open_id_only = _make_member(tenant_id=tenant_id, open_id="ou_1")
    feishu_provider = SimpleNamespace(id=uuid.uuid4(), provider_type="feishu")
    teams_member = _make_member(tenant_id=tenant_id, external_id="teams_1")
    teams_provider = SimpleNamespace(id=uuid.uuid4(), provider_type="microsoft_teams")

    feishu_payload = agent_tools._format_roster_human(source, feishu_open_id_only, feishu_provider, None)
    teams_payload = agent_tools._format_roster_human(source, teams_member, teams_provider, None)

    assert feishu_payload["can_contact"] is False
    assert "send_channel_message" not in feishu_payload["contact_tools"]
    assert teams_payload["can_contact"] is False
    assert "send_channel_message" not in teams_payload["contact_tools"]


def test_format_roster_human_without_contact_target_is_uncontactable():
    tenant_id = uuid.uuid4()
    source = _make_agent(tenant_id=tenant_id)
    member = _make_member(tenant_id=tenant_id)

    payload = agent_tools._format_roster_human(source, member, None, None)

    assert payload["can_contact"] is False
    assert payload["contact_tools"] == []
    assert payload["unavailable_reason"] == "missing_contact_target"


def test_roster_sort_prefers_contactable_exact_agent_match():
    members = [
        {"member_type": "human", "display_name": "OKR", "can_contact": True, "target_member_id": "h"},
        {"member_type": "agent", "display_name": "OKR", "can_contact": True, "target_agent_id": "a"},
        {"member_type": "agent", "display_name": "OKR Helper", "can_contact": True, "target_agent_id": "b"},
        {"member_type": "agent", "display_name": "OKR", "can_contact": False, "target_agent_id": "c"},
    ]

    sorted_members = sorted(members, key=lambda member: agent_tools._roster_sort_key(member, "OKR"))

    assert sorted_members[0]["target_agent_id"] == "a"
    assert sorted_members[-1]["target_agent_id"] == "c"
