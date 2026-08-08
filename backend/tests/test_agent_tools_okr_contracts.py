"""D-020 canonical and Runtime authorization contracts for OKR tools."""

from __future__ import annotations

from types import SimpleNamespace
import uuid

import pytest

from app import database
from app.services import agent_tools
from app.services.builtin_tool_definitions import (
    BUILTIN_TOOL_DEFINITIONS,
    builtin_model_definition,
    builtin_policy,
)


OKR_DEFINITIONS = tuple(
    definition
    for definition in BUILTIN_TOOL_DEFINITIONS
    if definition.get("category") == "okr"
)
OKR_TOOL_NAMES = frozenset(str(definition["name"]) for definition in OKR_DEFINITIONS)
OKR_AGENT_ONLY_TOOL_NAMES = frozenset(
    str(definition["name"])
    for definition in OKR_DEFINITIONS
    if (definition.get("config") or {}).get("okr_agent_only") is True
)
OKR_READ_TOOL_NAMES = frozenset({"get_okr", "get_my_okr", "get_okr_settings"})
OKR_WRITE_TOOL_NAMES = OKR_TOOL_NAMES - OKR_READ_TOOL_NAMES
UNMIGRATED_OKR_TOOL_NAMES = frozenset()

EXPECTED_ENUMS = {
    ("generate_okr_report", "report_type"): {"daily", "weekly"},
    ("create_objective", "owner_type"): {"company", "user", "agent"},
    ("update_kr_content", "status"): {
        "on_track",
        "at_risk",
        "behind",
        "completed",
    },
    ("update_objective", "status"): {
        "draft",
        "active",
        "completed",
        "archived",
    },
    ("update_any_kr_progress", "status"): {
        "on_track",
        "at_risk",
        "behind",
        "completed",
    },
    ("upsert_member_daily_report", "member_type"): {"user", "agent"},
}


class FakeScalars:
    def __init__(self, items) -> None:
        self._items = list(items)

    def all(self):
        return list(self._items)


class FakeResult:
    def __init__(
        self,
        *,
        scalar=None,
        items=(),
        first_value=None,
    ) -> None:
        self._scalar = scalar
        self._items = tuple(items)
        self._first = first_value

    def scalar_one_or_none(self):
        return self._scalar

    def scalars(self):
        return FakeScalars(self._items)

    def first(self):
        return self._first


class FakeDB:
    def __init__(self, *results: FakeResult) -> None:
        self.results = list(results)
        self.execute_calls = []
        self.added = []
        self.commit_calls = 0

    async def execute(self, statement):
        self.execute_calls.append(statement)
        if not self.results:
            raise AssertionError("unexpected OKR database query")
        return self.results.pop(0)

    def add(self, value) -> None:
        self.added.append(value)

    async def commit(self) -> None:
        self.commit_calls += 1


class FakeSession:
    def __init__(self, db: FakeDB) -> None:
        self.db = db

    async def __aenter__(self):
        return self.db

    async def __aexit__(self, *_args):
        return False


class SessionFactory:
    def __init__(self, db: FakeDB | None = None) -> None:
        self.db = db
        self.calls = 0

    def __call__(self):
        self.calls += 1
        if self.db is None:
            raise AssertionError("database accessed before OKR argument validation")
        return FakeSession(self.db)


def install_session(monkeypatch, factory: SessionFactory) -> None:
    monkeypatch.setattr(database, "async_session", factory)
    monkeypatch.setattr(agent_tools, "async_session", factory)


def schema_for(name: str) -> dict:
    return builtin_model_definition(name)["function"]["parameters"]


def test_collect_okr_progress_is_conditional_serial_write() -> None:
    # The code contract calls the discussed conditional approval/retry boundary
    # `retry_policy`; it is not a second independent approval field.
    assert builtin_policy("collect_okr_progress") == {
        "effect": "write",
        "retry_policy": "conditional",
        "parallel_safe": False,
    }


@pytest.mark.parametrize("tool_name", ("get_okr", "get_my_okr"))
def test_okr_period_schema_supports_both_dates_or_neither(tool_name) -> None:
    schema = schema_for(tool_name)
    properties = schema["properties"]

    assert {"period_start", "period_end"} <= properties.keys()
    assert properties["period_start"].get("type") == "string"
    assert properties["period_end"].get("type") == "string"
    assert schema.get("dependentRequired") == {
        "period_start": ["period_end"],
        "period_end": ["period_start"],
    }


@pytest.mark.parametrize(
    "handler_name",
    ("_get_okr", "_get_my_okr"),
)
@pytest.mark.asyncio
async def test_okr_period_handler_rejects_one_sided_range_before_database(
    monkeypatch,
    handler_name,
) -> None:
    factory = SessionFactory()
    install_session(monkeypatch, factory)
    handler = getattr(agent_tools, handler_name)

    result = await handler(
        uuid.uuid4(),
        {"period_start": "2026-01-01"},
    )

    assert factory.calls == 0
    assert "period_start" in result
    assert "period_end" in result


@pytest.mark.asyncio
async def test_get_my_okr_honors_explicit_supported_period(monkeypatch) -> None:
    agent_id = uuid.uuid4()
    tenant_id = uuid.uuid4()
    agent = SimpleNamespace(id=agent_id, tenant_id=tenant_id)
    settings = SimpleNamespace(
        enabled=True,
        period_frequency="quarter",
        period_length_days=90,
    )
    db = FakeDB(
        FakeResult(scalar=agent),
        FakeResult(scalar=settings),
        FakeResult(items=()),
    )
    install_session(monkeypatch, SessionFactory(db))

    result = await agent_tools._get_my_okr(
        agent_id,
        {
            "period_start": "2025-01-01",
            "period_end": "2025-01-31",
        },
    )

    assert "2025-01-01" in result
    assert "2025-01-31" in result


@pytest.mark.asyncio
async def test_update_kr_progress_status_schema_matches_handler(monkeypatch) -> None:
    schema = schema_for("update_kr_progress")
    status_schema = schema["properties"].get("status")
    if status_schema is None:
        # Removing the unsupported override is an allowed consistent contract.
        return

    assert set(status_schema.get("enum", ())) == {
        "on_track",
        "at_risk",
        "behind",
        "completed",
    }

    agent_id = uuid.uuid4()
    user_id = uuid.uuid4()
    tenant_id = uuid.uuid4()
    kr_id = uuid.uuid4()
    kr = SimpleNamespace(
        id=kr_id,
        title="Ship the release",
        current_value=0.0,
        target_value=10.0,
        unit="items",
        status="behind",
        last_updated_at=None,
    )
    objective = SimpleNamespace(owner_type="agent", owner_id=agent_id)
    db = FakeDB(FakeResult(first_value=(kr, objective)))
    install_session(monkeypatch, SessionFactory(db))

    async def request_context(_db, _agent_id, _user_id):
        return {
            "agent": SimpleNamespace(id=agent_id),
            "tenant_id": tenant_id,
            "agent_is_system": False,
            "requester_is_admin": False,
            "requester_user_id": user_id,
        }

    monkeypatch.setattr(
        agent_tools,
        "_load_okr_request_context",
        request_context,
    )

    result = await agent_tools._update_kr_progress(
        agent_id,
        user_id,
        {
            "kr_id": str(kr_id),
            "value": 1.0,
            "status": "completed",
        },
    )

    assert "Failed" not in result
    assert kr.status == "completed"
    assert db.commit_calls == 1


def test_create_key_result_requires_parent_title_and_target() -> None:
    schema = schema_for("create_key_result")

    assert set(schema.get("required", ())) == {
        "objective_id",
        "title",
        "target_value",
    }
    assert schema["properties"]["target_value"]["type"] == "number"


@pytest.mark.parametrize("target", (float("nan"), float("inf"), float("-inf")))
@pytest.mark.asyncio
async def test_create_key_result_rejects_non_finite_target_before_commit(
    monkeypatch,
    target,
) -> None:
    agent_id = uuid.uuid4()
    user_id = uuid.uuid4()
    tenant_id = uuid.uuid4()
    objective_id = uuid.uuid4()
    objective = SimpleNamespace(owner_type="agent", owner_id=agent_id)
    db = FakeDB(FakeResult(scalar=objective))
    install_session(monkeypatch, SessionFactory(db))

    async def request_context(_db, _agent_id, _user_id):
        return {
            "agent": SimpleNamespace(id=agent_id),
            "tenant_id": tenant_id,
            "agent_is_system": False,
            "requester_is_admin": False,
            "requester_user_id": user_id,
        }

    monkeypatch.setattr(
        agent_tools,
        "_load_okr_request_context",
        request_context,
    )

    result = await agent_tools._create_key_result(
        agent_id,
        user_id,
        {
            "objective_id": str(objective_id),
            "title": "Ship the release",
            "target_value": target,
        },
    )

    assert "finite" in result.lower() or "invalid" in result.lower()
    assert db.commit_calls == 0


def test_required_okr_write_strings_cannot_be_empty() -> None:
    for tool_name in sorted(OKR_WRITE_TOOL_NAMES):
        schema = schema_for(tool_name)
        properties = schema.get("properties", {})
        for property_name in schema.get("required", ()):
            property_schema = properties[property_name]
            if property_schema.get("type") != "string":
                continue
            enum_values = property_schema.get("enum")
            enum_is_nonempty = bool(enum_values) and all(
                isinstance(value, str) and bool(value.strip())
                for value in enum_values
            )
            assert property_schema.get("minLength", 0) >= 1 or enum_is_nonempty, (
                f"{tool_name}.{property_name} must reject an empty string"
            )


@pytest.mark.parametrize(
    ("tool_name", "property_name", "expected_values"),
    (
        (tool_name, property_name, expected_values)
        for (tool_name, property_name), expected_values in EXPECTED_ENUMS.items()
    ),
)
def test_okr_write_categorical_fields_have_closed_nonempty_enums(
    tool_name,
    property_name,
    expected_values,
) -> None:
    property_schema = schema_for(tool_name)["properties"][property_name]

    assert set(property_schema.get("enum", ())) == expected_values
    assert "" not in property_schema["enum"]


@pytest.mark.asyncio
async def test_runtime_rejects_okr_agent_only_tools_for_other_system_agents(
    monkeypatch,
) -> None:
    tools = [
        builtin_model_definition(name)
        for name in sorted(OKR_AGENT_ONLY_TOOL_NAMES)
    ]

    async def assigned(_agent_id):
        return tools

    async def no_dynamic(_agent_id):
        return set()

    async def not_designated(_agent_id):
        return False

    monkeypatch.setattr(agent_tools, "get_agent_tools_for_llm", assigned)
    monkeypatch.setattr(
        agent_tools,
        "_get_runtime_dynamic_mcp_tool_names",
        no_dynamic,
    )
    monkeypatch.setattr(
        agent_tools,
        "_agent_is_designated_okr_agent",
        not_designated,
        raising=False,
    )
    monkeypatch.setattr(
        agent_tools,
        "RUNTIME_TYPED_APPLICATION_TOOL_NAMES",
        frozenset(
            {
                *agent_tools.RUNTIME_TYPED_APPLICATION_TOOL_NAMES,
                *OKR_AGENT_ONLY_TOOL_NAMES,
            }
        ),
    )

    resolved = await agent_tools.get_runtime_agent_tools_for_llm(uuid.uuid4())

    assert {
        item["function"]["name"] for item in resolved
    }.isdisjoint(OKR_AGENT_ONLY_TOOL_NAMES)


@pytest.mark.asyncio
async def test_runtime_allows_ready_assigned_okr_agent_only_tools_only_for_designated_agent(
    monkeypatch,
) -> None:
    assigned_names = {"collect_okr_progress", "create_key_result"}
    tools = [builtin_model_definition(name) for name in sorted(assigned_names)]

    async def assigned(_agent_id):
        return tools

    async def no_dynamic(_agent_id):
        return set()

    async def designated(_agent_id):
        return True

    monkeypatch.setattr(agent_tools, "get_agent_tools_for_llm", assigned)
    monkeypatch.setattr(
        agent_tools,
        "_get_runtime_dynamic_mcp_tool_names",
        no_dynamic,
    )
    monkeypatch.setattr(
        agent_tools,
        "_agent_is_designated_okr_agent",
        designated,
        raising=False,
    )
    monkeypatch.setattr(
        agent_tools,
        "RUNTIME_TYPED_APPLICATION_TOOL_NAMES",
        frozenset(
            {*agent_tools.RUNTIME_TYPED_APPLICATION_TOOL_NAMES, *assigned_names}
        ),
    )

    resolved = await agent_tools.get_runtime_agent_tools_for_llm(uuid.uuid4())

    assert {item["function"]["name"] for item in resolved} == assigned_names


@pytest.mark.asyncio
async def test_unmigrated_okr_tools_remain_hidden_from_durable_runtime(
    monkeypatch,
) -> None:
    tools = [
        builtin_model_definition(name)
        for name in sorted(UNMIGRATED_OKR_TOOL_NAMES)
    ]

    async def assigned(_agent_id):
        return tools

    async def no_dynamic(_agent_id):
        return set()

    async def designated(_agent_id):
        return True

    monkeypatch.setattr(agent_tools, "get_agent_tools_for_llm", assigned)
    monkeypatch.setattr(
        agent_tools,
        "_get_runtime_dynamic_mcp_tool_names",
        no_dynamic,
    )
    monkeypatch.setattr(
        agent_tools,
        "_agent_is_designated_okr_agent",
        designated,
        raising=False,
    )

    resolved = await agent_tools.get_runtime_agent_tools_for_llm(uuid.uuid4())

    assert resolved == []
