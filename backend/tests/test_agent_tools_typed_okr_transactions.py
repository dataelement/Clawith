"""D-020 typed outcomes for OKR handlers with one local DB transaction.

This batch deliberately excludes collection and report-generation jobs.  It
locks only the three local reads and seven local writes whose business fact can
be settled by one database transaction.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from types import SimpleNamespace
import uuid

import pytest

from app import database
from app.services import agent_tools, okr_reporting, okr_scheduler
from app.services.agent_runtime.tool_execution import ToolExecutionOutcome


OKR_TRANSACTION_TOOL_NAMES = frozenset(
    {
        "get_okr",
        "get_my_okr",
        "get_okr_settings",
        "update_kr_progress",
        "update_kr_content",
        "create_objective",
        "create_key_result",
        "update_objective",
        "update_any_kr_progress",
        "upsert_member_daily_report",
    }
)

OKR_TRANSACTION_WRITE_TOOL_NAMES = (
    "update_kr_progress",
    "update_kr_content",
    "create_objective",
    "create_key_result",
    "update_objective",
    "update_any_kr_progress",
    "upsert_member_daily_report",
)

OKR_AGENT_ONLY_TRANSACTION_TOOL_NAMES = frozenset(
    {
        "get_okr_settings",
        "create_objective",
        "create_key_result",
        "update_any_kr_progress",
        "upsert_member_daily_report",
    }
)

KR_STATUSES = frozenset({"on_track", "at_risk", "behind", "completed"})


class FakeScalars:
    def __init__(self, items=()) -> None:
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
        rows=(),
    ) -> None:
        self._scalar = scalar
        self._items = tuple(items)
        self._first = first_value
        self._rows = tuple(rows)

    def scalar_one_or_none(self):
        return self._scalar

    def scalars(self):
        return FakeScalars(self._items)

    def first(self):
        return self._first

    def fetchall(self):
        return list(self._rows)


class FakeDB:
    def __init__(
        self,
        *results: FakeResult,
        commit_error: BaseException | None = None,
        assigned_ids: tuple[uuid.UUID, ...] = (),
    ) -> None:
        self.results = list(results)
        self.commit_error = commit_error
        self.assigned_ids = list(assigned_ids)
        self.execute_calls = []
        self.added = []
        self.commit_calls = 0
        self.flush_calls = 0
        self.rollback_calls = 0

    async def execute(self, statement):
        self.execute_calls.append(statement)
        if not self.results:
            raise AssertionError(f"unexpected OKR database query: {statement}")
        return self.results.pop(0)

    def _assign_id(self, value) -> None:
        if self.assigned_ids and getattr(value, "id", None) is None:
            value.id = self.assigned_ids.pop(0)

    def add(self, value) -> None:
        self._assign_id(value)
        self.added.append(value)

    async def flush(self) -> None:
        self.flush_calls += 1
        for value in self.added:
            self._assign_id(value)

    async def commit(self) -> None:
        self.commit_calls += 1
        if self.commit_error is not None:
            raise self.commit_error

    async def rollback(self) -> None:
        self.rollback_calls += 1


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


class UpsertDB(FakeDB):
    """Statement-routed fake that supports old and intended upsert shapes."""

    def __init__(
        self,
        *,
        caller,
        settings,
        member,
        existing=None,
        commit_error: BaseException | None = None,
        assigned_ids: tuple[uuid.UUID, ...] = (),
    ) -> None:
        super().__init__(
            commit_error=commit_error,
            assigned_ids=assigned_ids,
        )
        self.caller = caller
        self.settings = settings
        self.member = member
        self.existing = existing

    async def execute(self, statement):
        self.execute_calls.append(statement)
        sql = str(statement).lower()
        if "okr_settings" in sql:
            return FakeResult(scalar=self.settings)
        if "member_daily_reports" in sql:
            return FakeResult(scalar=self.existing)
        if " users " in f" {sql} " or "from users" in sql:
            return FakeResult(scalar=self.member)
        if " agents " in f" {sql} " or "from agents" in sql:
            return FakeResult(scalar=self.caller)
        raise AssertionError(f"unexpected daily-report database query: {statement}")


class CrossTenantOwnerDB(FakeDB):
    """Return a foreign owner only when the lookup forgot tenant scoping."""

    def __init__(self, foreign_owner_id: uuid.UUID) -> None:
        super().__init__()
        self.foreign_owner_id = foreign_owner_id
        self.owner_query_was_tenant_scoped = False

    async def execute(self, statement):
        self.execute_calls.append(statement)
        sql = str(statement).lower()
        self.owner_query_was_tenant_scoped = "tenant_id" in sql
        return FakeResult(scalar=(None if self.owner_query_was_tenant_scoped else self.foreign_owner_id))


def install_session(monkeypatch, factory: SessionFactory) -> None:
    monkeypatch.setattr(database, "async_session", factory)
    monkeypatch.setattr(agent_tools, "async_session", factory)


async def execute(
    tool_name: str,
    arguments: dict,
    *,
    agent_id: uuid.UUID,
    user_id: uuid.UUID,
):
    return await agent_tools.execute_builtin_tool_outcome(
        tool_name,
        arguments,
        agent_id,
        user_id,
    )


def assert_outcome(
    result,
    status: str,
    *,
    error_code: str | None = None,
) -> ToolExecutionOutcome:
    assert isinstance(result, ToolExecutionOutcome)
    assert result.status == status
    if error_code is not None:
        assert result.error_code == error_code
    return result


def install_common_context(
    monkeypatch,
    *,
    agent_id: uuid.UUID,
    user_id: uuid.UUID,
    tenant_id: uuid.UUID,
    is_system: bool = False,
    is_admin: bool = False,
    designated: bool = True,
):
    agent = SimpleNamespace(
        id=agent_id,
        tenant_id=tenant_id,
        is_system=is_system,
    )

    async def request_context(_db, _agent_id, _user_id):
        return {
            "agent": agent,
            "tenant_id": tenant_id,
            "agent_is_system": is_system,
            "requester_is_admin": is_admin,
            "requester_user_id": user_id,
        }

    async def tenant_for_agent(_agent_id):
        return str(tenant_id)

    async def is_designated(_agent_id):
        return designated

    monkeypatch.setattr(
        agent_tools,
        "_load_okr_request_context",
        request_context,
    )
    monkeypatch.setattr(agent_tools, "_get_agent_tenant_id", tenant_for_agent)
    monkeypatch.setattr(
        agent_tools,
        "_agent_is_designated_okr_agent",
        is_designated,
        raising=False,
    )
    return agent


def objective_for(
    owner_id: uuid.UUID,
    *,
    owner_type: str = "agent",
    objective_id: uuid.UUID | None = None,
):
    return SimpleNamespace(
        id=objective_id or uuid.uuid4(),
        title="Ship the release",
        description="Keep the release safe",
        owner_type=owner_type,
        owner_id=None if owner_type == "company" else owner_id,
        period_start=date(2026, 7, 1),
        period_end=date(2026, 9, 30),
        status="active",
    )


def key_result_for(
    owner_id: uuid.UUID,
    *,
    kr_id: uuid.UUID | None = None,
    target_value: float = 10.0,
    current_value: float = 0.0,
):
    del owner_id
    return SimpleNamespace(
        id=kr_id or uuid.uuid4(),
        objective_id=uuid.uuid4(),
        title="Pass the release gate",
        target_value=target_value,
        current_value=current_value,
        unit="checks",
        focus_ref=None,
        status="behind",
        last_updated_at=None,
    )


@dataclass
class WriteScenario:
    tool_name: str
    arguments: dict
    db: FakeDB
    expected_ref: str
    agent_id: uuid.UUID
    user_id: uuid.UUID
    tenant_id: uuid.UUID
    objects: dict[str, object] = field(default_factory=dict)
    captured: dict[str, object] = field(default_factory=dict)


def build_write_scenario(
    monkeypatch,
    tool_name: str,
    *,
    commit_error: BaseException | None = None,
) -> WriteScenario:
    agent_id = uuid.uuid4()
    user_id = uuid.uuid4()
    tenant_id = uuid.uuid4()
    objective_id = uuid.uuid4()
    kr_id = uuid.uuid4()
    new_id = uuid.uuid4()
    report_id = uuid.uuid4()
    objects: dict[str, object] = {}
    captured: dict[str, object] = {}

    is_system = tool_name in {
        "create_objective",
        "create_key_result",
        "update_any_kr_progress",
        "upsert_member_daily_report",
    }
    install_common_context(
        monkeypatch,
        agent_id=agent_id,
        user_id=user_id,
        tenant_id=tenant_id,
        is_system=is_system,
        is_admin=is_system,
    )

    if tool_name in {
        "update_kr_progress",
        "update_kr_content",
        "update_any_kr_progress",
    }:
        kr = key_result_for(agent_id, kr_id=kr_id)
        objective = objective_for(
            agent_id,
            objective_id=objective_id,
        )
        db = FakeDB(
            FakeResult(first_value=(kr, objective)),
            commit_error=commit_error,
        )
        objects.update(kr=kr, objective=objective)
        if tool_name == "update_kr_progress":
            arguments = {
                "kr_id": str(kr_id),
                "value": 8.0,
                "note": "Eight checks passed",
            }
        elif tool_name == "update_kr_content":
            arguments = {
                "kr_id": str(kr_id),
                "title": "Pass every release gate",
                "target_value": 12.0,
                "status": "on_track",
            }
        else:
            arguments = {
                "kr_id": str(kr_id),
                "value": 8.0,
                "note": "Verified by the OKR Agent",
            }
        expected_ref = str(kr_id)
    elif tool_name == "create_objective":
        db = FakeDB(
            commit_error=commit_error,
            assigned_ids=(new_id,),
        )
        arguments = {
            "title": "Make releases boring",
            "description": "Remove release-day surprises",
            "owner_type": "company",
            "period_start": "2026-07-01",
            "period_end": "2026-09-30",
        }
        expected_ref = str(new_id)
    elif tool_name == "create_key_result":
        objective = objective_for(
            agent_id,
            owner_type="company",
            objective_id=objective_id,
        )
        db = FakeDB(
            FakeResult(scalar=objective),
            commit_error=commit_error,
            assigned_ids=(new_id,),
        )
        objects["objective"] = objective
        arguments = {
            "objective_id": str(objective_id),
            "title": "Complete ten release checks",
            "target_value": 10.0,
            "unit": "checks",
        }
        expected_ref = str(new_id)
    elif tool_name == "update_objective":
        objective = objective_for(
            agent_id,
            objective_id=objective_id,
        )
        db = FakeDB(
            FakeResult(scalar=objective),
            commit_error=commit_error,
        )
        objects["objective"] = objective
        arguments = {
            "objective_id": str(objective_id),
            "title": "Make verified releases boring",
        }
        expected_ref = str(objective_id)
    elif tool_name == "upsert_member_daily_report":
        member_id = uuid.uuid4()
        caller = SimpleNamespace(
            id=agent_id,
            tenant_id=tenant_id,
            is_system=True,
        )
        settings = SimpleNamespace(tenant_id=tenant_id, okr_agent_id=agent_id)
        member = SimpleNamespace(
            id=member_id,
            tenant_id=tenant_id,
            display_name="Alice",
        )
        db = UpsertDB(
            caller=caller,
            settings=settings,
            member=member,
            commit_error=commit_error,
            assigned_ids=(report_id,),
        )
        report = SimpleNamespace(
            id=report_id,
            tenant_id=tenant_id,
            member_type="user",
            member_id=member_id,
            report_date=date(2026, 7, 16),
            content="Completed the release checklist.",
            source="okr_agent_assisted",
            status="submitted",
        )

        async def fake_upsert(**kwargs):
            captured.update(kwargs)
            report.content = kwargs["content"]
            return report

        monkeypatch.setattr(okr_reporting, "upsert_member_daily_report", fake_upsert)
        objects.update(report=report, member=member)
        arguments = {
            "report_date": "2026-07-16",
            "content": report.content,
            "member_type": "user",
            "member_id": str(member_id),
            "source": "okr_agent_assisted",
        }
        expected_ref = str(report_id)
    else:  # pragma: no cover - the caller is parameterized by a fixed constant.
        raise AssertionError(f"unsupported write scenario: {tool_name}")

    install_session(monkeypatch, SessionFactory(db))
    return WriteScenario(
        tool_name=tool_name,
        arguments=arguments,
        db=db,
        expected_ref=expected_ref,
        agent_id=agent_id,
        user_id=user_id,
        tenant_id=tenant_id,
        objects=objects,
        captured=captured,
    )


def test_exact_local_okr_transaction_batch_is_runtime_typed() -> None:
    assert OKR_TRANSACTION_TOOL_NAMES <= agent_tools.RUNTIME_TYPED_APPLICATION_TOOL_NAMES


@pytest.mark.parametrize("tool_name", ("get_okr", "get_my_okr"))
@pytest.mark.parametrize(
    "arguments",
    (
        {"period_start": "2026-07-01"},
        {"period_end": "2026-07-31"},
        {"period_start": "2026-08-01", "period_end": "2026-07-01"},
    ),
)
@pytest.mark.asyncio
async def test_okr_read_period_requires_a_complete_ordered_range_before_database(
    monkeypatch,
    tool_name,
    arguments,
) -> None:
    agent_id = uuid.uuid4()
    user_id = uuid.uuid4()
    tenant_id = uuid.uuid4()
    factory = SessionFactory()
    install_session(monkeypatch, factory)
    install_common_context(
        monkeypatch,
        agent_id=agent_id,
        user_id=user_id,
        tenant_id=tenant_id,
    )

    result = await execute(
        tool_name,
        arguments,
        agent_id=agent_id,
        user_id=user_id,
    )

    assert factory.calls == 0
    assert_outcome(result, "failed", error_code="invalid_tool_arguments")


@pytest.mark.parametrize(
    ("tool_name", "arguments"),
    (
        (
            "create_objective",
            {
                "title": "Invalid period",
                "owner_type": "company",
                "period_start": "2026-08-01",
                "period_end": "2026-07-01",
            },
        ),
        (
            "update_objective",
            {
                "objective_id": str(uuid.uuid4()),
                "period_start": "2026-08-01",
                "period_end": "2026-07-01",
            },
        ),
    ),
)
@pytest.mark.asyncio
async def test_okr_objective_period_rejects_reversed_range_before_database(
    monkeypatch,
    tool_name,
    arguments,
) -> None:
    agent_id = uuid.uuid4()
    user_id = uuid.uuid4()
    tenant_id = uuid.uuid4()
    factory = SessionFactory()
    install_session(monkeypatch, factory)
    install_common_context(
        monkeypatch,
        agent_id=agent_id,
        user_id=user_id,
        tenant_id=tenant_id,
        is_system=tool_name == "create_objective",
        is_admin=tool_name == "create_objective",
    )

    result = await execute(
        tool_name,
        arguments,
        agent_id=agent_id,
        user_id=user_id,
    )

    assert factory.calls == 0
    assert_outcome(result, "failed", error_code="invalid_tool_arguments")


@pytest.mark.parametrize("tool_name", ("get_okr", "get_my_okr"))
@pytest.mark.asyncio
async def test_empty_okr_read_is_a_typed_success_and_honors_explicit_period(
    monkeypatch,
    tool_name,
) -> None:
    agent_id = uuid.uuid4()
    user_id = uuid.uuid4()
    tenant_id = uuid.uuid4()
    agent = SimpleNamespace(id=agent_id, tenant_id=tenant_id, is_system=False)
    settings = SimpleNamespace(
        enabled=True,
        period_frequency="quarterly",
        period_length_days=90,
    )
    db = FakeDB(
        FakeResult(scalar=agent),
        FakeResult(scalar=settings),
        FakeResult(items=()),
    )
    install_session(monkeypatch, SessionFactory(db))
    install_common_context(
        monkeypatch,
        agent_id=agent_id,
        user_id=user_id,
        tenant_id=tenant_id,
    )

    result = await execute(
        tool_name,
        {
            "period_start": "2026-04-01",
            "period_end": "2026-04-30",
        },
        agent_id=agent_id,
        user_id=user_id,
    )

    outcome = assert_outcome(result, "succeeded")
    assert "2026-04-01" in (outcome.summary or "")
    assert "2026-04-30" in (outcome.summary or "")


@pytest.mark.asyncio
async def test_get_okr_settings_returns_typed_local_settings(monkeypatch) -> None:
    agent_id = uuid.uuid4()
    user_id = uuid.uuid4()
    tenant_id = uuid.uuid4()
    agent = SimpleNamespace(id=agent_id, tenant_id=tenant_id, is_system=True)
    db = FakeDB(FakeResult(scalar=agent))
    install_session(monkeypatch, SessionFactory(db))
    install_common_context(
        monkeypatch,
        agent_id=agent_id,
        user_id=user_id,
        tenant_id=tenant_id,
        is_system=True,
        is_admin=True,
    )

    async def settings_for_agent(_tenant_id):
        return {
            "enabled": True,
            "period_frequency": "quarterly",
            "okr_agent_id": str(agent_id),
        }

    monkeypatch.setattr(
        okr_scheduler,
        "get_okr_settings_for_agent",
        settings_for_agent,
    )

    result = await execute(
        "get_okr_settings",
        {},
        agent_id=agent_id,
        user_id=user_id,
    )

    outcome = assert_outcome(result, "succeeded")
    assert "quarterly" in (outcome.summary or "")
    assert "enabled" in (outcome.summary or "")


@pytest.mark.parametrize(
    ("tool_name", "arguments"),
    (
        (
            "update_kr_progress",
            {"kr_id": str(uuid.uuid4()), "value": 1.0, "status": "blocked"},
        ),
        (
            "update_kr_content",
            {"kr_id": str(uuid.uuid4()), "status": "blocked"},
        ),
        (
            "create_objective",
            {
                "title": "Bad owner",
                "owner_type": "team",
                "period_start": "2026-07-01",
                "period_end": "2026-09-30",
            },
        ),
        (
            "update_objective",
            {"objective_id": str(uuid.uuid4()), "status": "blocked"},
        ),
        (
            "update_any_kr_progress",
            {"kr_id": str(uuid.uuid4()), "value": 1.0, "status": "blocked"},
        ),
        (
            "upsert_member_daily_report",
            {
                "report_date": "2026-07-16",
                "content": "Done",
                "member_type": "contractor",
                "member_id": str(uuid.uuid4()),
            },
        ),
    ),
)
@pytest.mark.asyncio
async def test_okr_closed_enums_reject_unknown_values_before_database(
    monkeypatch,
    tool_name,
    arguments,
) -> None:
    agent_id = uuid.uuid4()
    user_id = uuid.uuid4()
    tenant_id = uuid.uuid4()
    factory = SessionFactory()
    install_session(monkeypatch, factory)
    install_common_context(
        monkeypatch,
        agent_id=agent_id,
        user_id=user_id,
        tenant_id=tenant_id,
        is_system=tool_name in OKR_AGENT_ONLY_TRANSACTION_TOOL_NAMES,
        is_admin=tool_name in OKR_AGENT_ONLY_TRANSACTION_TOOL_NAMES,
    )

    result = await execute(
        tool_name,
        arguments,
        agent_id=agent_id,
        user_id=user_id,
    )

    assert factory.calls == 0
    assert_outcome(result, "failed", error_code="invalid_tool_arguments")


@pytest.mark.parametrize("bad_value", (float("nan"), float("inf"), float("-inf")))
@pytest.mark.parametrize(
    ("tool_name", "arguments", "field_name"),
    (
        (
            "update_kr_progress",
            {"kr_id": str(uuid.uuid4()), "value": 1.0},
            "value",
        ),
        (
            "update_kr_content",
            {"kr_id": str(uuid.uuid4()), "target_value": 1.0},
            "target_value",
        ),
        (
            "create_key_result",
            {
                "objective_id": str(uuid.uuid4()),
                "title": "Finite target required",
                "target_value": 1.0,
            },
            "target_value",
        ),
        (
            "update_any_kr_progress",
            {"kr_id": str(uuid.uuid4()), "value": 1.0},
            "value",
        ),
    ),
)
@pytest.mark.asyncio
async def test_okr_numbers_reject_nan_and_infinity_before_database(
    monkeypatch,
    tool_name,
    arguments,
    field_name,
    bad_value,
) -> None:
    agent_id = uuid.uuid4()
    user_id = uuid.uuid4()
    tenant_id = uuid.uuid4()
    factory = SessionFactory()
    install_session(monkeypatch, factory)
    install_common_context(
        monkeypatch,
        agent_id=agent_id,
        user_id=user_id,
        tenant_id=tenant_id,
        is_system=tool_name in OKR_AGENT_ONLY_TRANSACTION_TOOL_NAMES,
        is_admin=tool_name in OKR_AGENT_ONLY_TRANSACTION_TOOL_NAMES,
    )
    call_arguments = dict(arguments)
    call_arguments[field_name] = bad_value

    result = await execute(
        tool_name,
        call_arguments,
        agent_id=agent_id,
        user_id=user_id,
    )

    assert factory.calls == 0
    assert_outcome(result, "failed", error_code="invalid_tool_arguments")


@pytest.mark.parametrize("missing_field", ("objective_id", "title", "target_value"))
@pytest.mark.asyncio
async def test_create_key_result_required_fields_fail_before_database(
    monkeypatch,
    missing_field,
) -> None:
    agent_id = uuid.uuid4()
    user_id = uuid.uuid4()
    tenant_id = uuid.uuid4()
    arguments = {
        "objective_id": str(uuid.uuid4()),
        "title": "Pass release checks",
        "target_value": 10.0,
    }
    arguments.pop(missing_field)
    factory = SessionFactory()
    install_session(monkeypatch, factory)
    install_common_context(
        monkeypatch,
        agent_id=agent_id,
        user_id=user_id,
        tenant_id=tenant_id,
        is_system=True,
        is_admin=True,
    )

    result = await execute(
        "create_key_result",
        arguments,
        agent_id=agent_id,
        user_id=user_id,
    )

    assert factory.calls == 0
    assert_outcome(result, "failed", error_code="invalid_tool_arguments")


@pytest.mark.parametrize(
    ("tool_name", "explicit_status", "value", "target", "expected_status"),
    (
        ("update_kr_progress", "completed", 1.0, 10.0, "completed"),
        ("update_any_kr_progress", "completed", 1.0, 10.0, "completed"),
        ("update_kr_progress", None, 8.0, 10.0, "on_track"),
        ("update_any_kr_progress", None, 8.0, 10.0, "on_track"),
        ("update_kr_progress", None, 0.0, 0.0, "completed"),
        ("update_any_kr_progress", None, 0.0, 0.0, "completed"),
    ),
)
@pytest.mark.asyncio
async def test_kr_progress_status_override_auto_and_zero_target(
    monkeypatch,
    tool_name,
    explicit_status,
    value,
    target,
    expected_status,
) -> None:
    agent_id = uuid.uuid4()
    user_id = uuid.uuid4()
    tenant_id = uuid.uuid4()
    kr = key_result_for(agent_id, target_value=target)
    objective = objective_for(agent_id)
    db = FakeDB(FakeResult(first_value=(kr, objective)))
    install_session(monkeypatch, SessionFactory(db))
    install_common_context(
        monkeypatch,
        agent_id=agent_id,
        user_id=user_id,
        tenant_id=tenant_id,
        is_system=tool_name == "update_any_kr_progress",
        is_admin=tool_name == "update_any_kr_progress",
    )
    arguments = {"kr_id": str(kr.id), "value": value}
    if explicit_status is not None:
        arguments["status"] = explicit_status

    result = await execute(
        tool_name,
        arguments,
        agent_id=agent_id,
        user_id=user_id,
    )

    outcome = assert_outcome(result, "succeeded")
    assert outcome.result_ref == str(kr.id)
    assert kr.status == expected_status
    assert db.commit_calls == 1


@pytest.mark.parametrize("tool_name", OKR_TRANSACTION_WRITE_TOOL_NAMES)
@pytest.mark.asyncio
async def test_okr_transaction_write_success_has_one_commit_and_stable_receipt(
    monkeypatch,
    tool_name,
) -> None:
    scenario = build_write_scenario(monkeypatch, tool_name)

    result = await execute(
        tool_name,
        scenario.arguments,
        agent_id=scenario.agent_id,
        user_id=scenario.user_id,
    )

    outcome = assert_outcome(result, "succeeded")
    assert scenario.db.commit_calls == 1
    assert outcome.result_ref == scenario.expected_ref
    assert outcome.summary
    assert len(outcome.summary.encode("utf-8")) <= 8192


@pytest.mark.parametrize("tool_name", OKR_TRANSACTION_WRITE_TOOL_NAMES)
@pytest.mark.asyncio
async def test_okr_commit_started_exception_is_unknown_with_reconciliation_ref(
    monkeypatch,
    tool_name,
) -> None:
    scenario = build_write_scenario(
        monkeypatch,
        tool_name,
        commit_error=RuntimeError("commit acknowledgement lost"),
    )

    result = await execute(
        tool_name,
        scenario.arguments,
        agent_id=scenario.agent_id,
        user_id=scenario.user_id,
    )

    outcome = assert_outcome(result, "unknown")
    assert scenario.db.commit_calls == 1
    assert outcome.result_ref == scenario.expected_ref
    assert outcome.retryable is False


@pytest.mark.parametrize(
    ("tool_name", "result"),
    (
        ("update_kr_progress", FakeResult(first_value=None)),
        ("update_kr_content", FakeResult(first_value=None)),
        ("create_key_result", FakeResult(scalar=None)),
        ("update_objective", FakeResult(scalar=None)),
        ("update_any_kr_progress", FakeResult(first_value=None)),
    ),
)
@pytest.mark.asyncio
async def test_okr_missing_target_is_failed_without_commit(
    monkeypatch,
    tool_name,
    result,
) -> None:
    agent_id = uuid.uuid4()
    user_id = uuid.uuid4()
    tenant_id = uuid.uuid4()
    target_id = uuid.uuid4()
    db = FakeDB(result)
    install_session(monkeypatch, SessionFactory(db))
    install_common_context(
        monkeypatch,
        agent_id=agent_id,
        user_id=user_id,
        tenant_id=tenant_id,
        is_system=tool_name in OKR_AGENT_ONLY_TRANSACTION_TOOL_NAMES,
        is_admin=tool_name in OKR_AGENT_ONLY_TRANSACTION_TOOL_NAMES,
    )
    if tool_name == "create_key_result":
        arguments = {
            "objective_id": str(target_id),
            "title": "Missing parent",
            "target_value": 1.0,
        }
    elif tool_name == "update_objective":
        arguments = {"objective_id": str(target_id), "title": "Missing"}
    else:
        arguments = {"kr_id": str(target_id), "value": 1.0}
        if tool_name == "update_kr_content":
            arguments = {"kr_id": str(target_id), "title": "Missing"}

    outcome = await execute(
        tool_name,
        arguments,
        agent_id=agent_id,
        user_id=user_id,
    )

    assert_outcome(outcome, "failed")
    assert db.commit_calls == 0


@pytest.mark.parametrize(
    "tool_name",
    (
        "update_kr_progress",
        "update_kr_content",
        "create_key_result",
        "update_objective",
    ),
)
@pytest.mark.asyncio
async def test_okr_foreign_owner_is_permission_failure_without_commit(
    monkeypatch,
    tool_name,
) -> None:
    agent_id = uuid.uuid4()
    user_id = uuid.uuid4()
    tenant_id = uuid.uuid4()
    target_id = uuid.uuid4()
    objective = objective_for(uuid.uuid4(), objective_id=target_id)
    kr = key_result_for(agent_id, kr_id=target_id)
    db_result = (
        FakeResult(scalar=objective)
        if tool_name in {"create_key_result", "update_objective"}
        else FakeResult(first_value=(kr, objective))
    )
    db = FakeDB(db_result)
    install_session(monkeypatch, SessionFactory(db))
    install_common_context(
        monkeypatch,
        agent_id=agent_id,
        user_id=user_id,
        tenant_id=tenant_id,
    )
    if tool_name == "create_key_result":
        arguments = {
            "objective_id": str(target_id),
            "title": "Unauthorized KR",
            "target_value": 1.0,
        }
    elif tool_name == "update_objective":
        arguments = {"objective_id": str(target_id), "title": "Unauthorized"}
    elif tool_name == "update_kr_content":
        arguments = {"kr_id": str(target_id), "title": "Unauthorized"}
    else:
        arguments = {"kr_id": str(target_id), "value": 1.0}

    result = await execute(
        tool_name,
        arguments,
        agent_id=agent_id,
        user_id=user_id,
    )

    assert_outcome(result, "failed")
    assert db.commit_calls == 0


@pytest.mark.asyncio
async def test_create_objective_cannot_resolve_owner_across_tenants(
    monkeypatch,
) -> None:
    agent_id = uuid.uuid4()
    user_id = uuid.uuid4()
    tenant_id = uuid.uuid4()
    foreign_owner_id = uuid.uuid4()
    db = CrossTenantOwnerDB(foreign_owner_id)
    install_session(monkeypatch, SessionFactory(db))
    install_common_context(
        monkeypatch,
        agent_id=agent_id,
        user_id=user_id,
        tenant_id=tenant_id,
        is_system=True,
        is_admin=True,
    )

    result = await execute(
        "create_objective",
        {
            "title": "Foreign owner must not resolve",
            "owner_type": "agent",
            "owner_id": str(foreign_owner_id),
            "period_start": "2026-07-01",
            "period_end": "2026-09-30",
        },
        agent_id=agent_id,
        user_id=user_id,
    )

    assert db.owner_query_was_tenant_scoped is True
    assert_outcome(result, "failed")
    assert db.commit_calls == 0


@pytest.mark.parametrize(
    ("tool_name", "arguments"),
    (
        ("get_okr_settings", {}),
        (
            "create_objective",
            {
                "title": "Unauthorized Objective",
                "owner_type": "company",
                "period_start": "2026-07-01",
                "period_end": "2026-09-30",
            },
        ),
        (
            "create_key_result",
            {
                "objective_id": str(uuid.uuid4()),
                "title": "Unauthorized KR",
                "target_value": 1.0,
            },
        ),
        (
            "update_any_kr_progress",
            {"kr_id": str(uuid.uuid4()), "value": 1.0},
        ),
        (
            "upsert_member_daily_report",
            {
                "report_date": "2026-07-16",
                "content": "Unauthorized report",
                "member_type": "user",
                "member_id": str(uuid.uuid4()),
            },
        ),
    ),
)
@pytest.mark.asyncio
async def test_okr_agent_only_execution_rechecks_designated_agent_before_database(
    monkeypatch,
    tool_name,
    arguments,
) -> None:
    agent_id = uuid.uuid4()
    user_id = uuid.uuid4()
    tenant_id = uuid.uuid4()
    factory = SessionFactory()
    install_session(monkeypatch, factory)
    install_common_context(
        monkeypatch,
        agent_id=agent_id,
        user_id=user_id,
        tenant_id=tenant_id,
        is_system=True,
        is_admin=True,
        designated=False,
    )

    result = await execute(
        tool_name,
        arguments,
        agent_id=agent_id,
        user_id=user_id,
    )

    assert factory.calls == 0
    outcome = assert_outcome(result, "failed")
    assert outcome.error_code in {
        "okr_agent_permission_denied",
        "tool_permission_denied",
    }


@pytest.mark.asyncio
async def test_upsert_member_daily_report_rejects_missing_member_without_commit(
    monkeypatch,
) -> None:
    agent_id = uuid.uuid4()
    user_id = uuid.uuid4()
    tenant_id = uuid.uuid4()
    member_id = uuid.uuid4()
    caller = SimpleNamespace(
        id=agent_id,
        tenant_id=tenant_id,
        is_system=True,
    )
    settings = SimpleNamespace(tenant_id=tenant_id, okr_agent_id=agent_id)
    db = UpsertDB(caller=caller, settings=settings, member=None)
    install_session(monkeypatch, SessionFactory(db))
    install_common_context(
        monkeypatch,
        agent_id=agent_id,
        user_id=user_id,
        tenant_id=tenant_id,
        is_system=True,
        is_admin=True,
    )

    async def must_not_upsert(**_kwargs):
        raise AssertionError("daily report write reached before member validation")

    monkeypatch.setattr(
        okr_reporting,
        "upsert_member_daily_report",
        must_not_upsert,
    )

    result = await execute(
        "upsert_member_daily_report",
        {
            "report_date": "2026-07-16",
            "content": "Member does not exist",
            "member_type": "user",
            "member_id": str(member_id),
        },
        agent_id=agent_id,
        user_id=user_id,
    )

    assert_outcome(result, "failed")
    assert db.commit_calls == 0


@pytest.mark.asyncio
async def test_upsert_member_daily_report_truncates_storage_and_returns_bounded_receipt(
    monkeypatch,
) -> None:
    scenario = build_write_scenario(monkeypatch, "upsert_member_daily_report")
    long_content = "Z" * 2500
    scenario.arguments["content"] = long_content

    result = await execute(
        scenario.tool_name,
        scenario.arguments,
        agent_id=scenario.agent_id,
        user_id=scenario.user_id,
    )

    outcome = assert_outcome(result, "succeeded")
    stored_content = scenario.captured.get("content")
    if stored_content is None:
        report_rows = [
            value for value in scenario.db.added if hasattr(value, "content") and hasattr(value, "report_date")
        ]
        assert len(report_rows) == 1
        stored_content = report_rows[0].content
    assert stored_content == long_content[:2000]
    assert outcome.result_ref == scenario.expected_ref
    assert outcome.summary
    assert "Z" * 128 not in outcome.summary
    assert len(outcome.summary.encode("utf-8")) <= 8192
    assert scenario.db.commit_calls == 1
