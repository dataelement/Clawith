"""Typed contracts for compound OKR collection and report jobs."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict
from datetime import date, timedelta
import json
from types import SimpleNamespace
import uuid

import pytest

from app.services import activity_logger, agent_tools, okr_scheduler
from app.services.agent_runtime.tool_execution import ToolExecutionOutcome
from app.services.builtin_tool_definitions import (
    builtin_model_definition,
    builtin_policy,
)


OKR_JOB_TOOLS = (
    "collect_okr_progress",
    "generate_okr_report",
    "generate_monthly_okr_report",
)
REPORT_BODY = "REPORT-BODY-MUST-NOT-LEAK\n" + ("sensitive details\n" * 5_000)


class FakeScalars:
    def __init__(self, items=()) -> None:
        self.items = list(items)

    def all(self):
        return list(self.items)


class FakeResult:
    def __init__(self, *, scalar=None, items=(), first=None) -> None:
        self.scalar = scalar
        self.items = tuple(items)
        self.first_value = first

    def scalar_one_or_none(self):
        return self.scalar

    def scalars(self):
        return FakeScalars(self.items)

    def first(self):
        return self.first_value


class FakeDB:
    def __init__(
        self,
        *results: FakeResult,
        commit_error: BaseException | None = None,
    ) -> None:
        self.results = list(results)
        self.commit_error = commit_error
        self.added = []
        self.commit_calls = 0

    async def execute(self, _statement):
        if not self.results:
            raise AssertionError("unexpected OKR database query")
        return self.results.pop(0)

    def add(self, value) -> None:
        if getattr(value, "id", None) is None:
            value.id = uuid.uuid4()
        self.added.append(value)

    async def commit(self) -> None:
        self.commit_calls += 1
        if self.commit_error is not None:
            raise self.commit_error


class FakeSession:
    def __init__(self, db: FakeDB) -> None:
        self.db = db

    async def __aenter__(self):
        return self.db

    async def __aexit__(self, *_args):
        return False


class SessionFactory:
    def __init__(self, db: FakeDB) -> None:
        self.db = db
        self.calls = 0

    def __call__(self):
        self.calls += 1
        return FakeSession(self.db)


class FakeStorage:
    def __init__(
        self,
        content_by_key: dict[str, str],
        *,
        read_errors: set[str] = frozenset(),
    ) -> None:
        self.content_by_key = content_by_key
        self.read_errors = set(read_errors)

    async def exists(self, key: str) -> bool:
        return key in self.content_by_key

    async def read_text(self, key: str, **_kwargs) -> str:
        if key in self.read_errors:
            raise OSError("focus projection unreadable")
        return self.content_by_key[key]


class CommitStartedError(ConnectionResetError):
    def __init__(
        self,
        message: str,
        *,
        operation_id: str,
        report_id: str | None = None,
        report_type: str | None = None,
        workspace_path: str | None = None,
    ) -> None:
        super().__init__(message)
        self.commit_started = True
        self.operation_id = operation_id
        self.report_id = report_id
        self.report_type = report_type
        self.workspace_path = workspace_path


class FrozenDate(date):
    @classmethod
    def today(cls):
        return cls(2026, 7, 16)


def _field(receipt, name: str):
    if isinstance(receipt, Mapping):
        return receipt[name]
    return getattr(receipt, name)


def _assert_typed(
    value: ToolExecutionOutcome | str,
    expected_status: str,
) -> ToolExecutionOutcome:
    assert isinstance(value, ToolExecutionOutcome)
    assert value.status == expected_status
    return value


def _focus_content(kr_id: uuid.UUID, value: float) -> str:
    return (
        "## KR: Release quality\n"
        f"- **KR ID**: {kr_id}\n"
        f"- **Current Progress**: {value}\n"
        "- **This Week**: Closed the release blockers\n"
    )


def _install_runtime_context(
    monkeypatch,
    *,
    agent_id: uuid.UUID,
    tenant_id: uuid.UUID,
    designated: bool = True,
) -> None:
    agent = SimpleNamespace(id=agent_id, tenant_id=tenant_id)
    lookup_db = FakeDB(FakeResult(scalar=agent))

    async def no_tenant(_agent_id):
        return None

    async def is_designated(_agent_id):
        return designated

    async def no_activity(*args, **kwargs):
        del args, kwargs

    monkeypatch.setattr(agent_tools, "async_session", SessionFactory(lookup_db))
    monkeypatch.setattr(agent_tools, "_get_agent_tenant_id", no_tenant)
    monkeypatch.setattr(
        agent_tools,
        "_agent_is_designated_okr_agent",
        is_designated,
        raising=False,
    )
    monkeypatch.setattr(activity_logger, "log_activity", no_activity)


async def _execute(
    tool_name: str,
    arguments: dict,
    *,
    agent_id: uuid.UUID,
) -> ToolExecutionOutcome | str:
    return await agent_tools.execute_builtin_tool_outcome(
        tool_name,
        arguments,
        agent_id=agent_id,
        user_id=uuid.uuid4(),
    )


def test_collect_okr_progress_is_a_conditional_serial_write() -> None:
    assert builtin_policy("collect_okr_progress") == {
        "effect": "write",
        "retry_policy": "conditional",
        "parallel_safe": False,
    }


@pytest.mark.asyncio
async def test_designated_okr_agent_gets_only_assigned_compound_jobs(
    monkeypatch,
) -> None:
    tools = [builtin_model_definition(name) for name in OKR_JOB_TOOLS]

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

    assert set(OKR_JOB_TOOLS) <= agent_tools.RUNTIME_TYPED_APPLICATION_TOOL_NAMES
    assert {tool["function"]["name"] for tool in resolved} == set(OKR_JOB_TOOLS)


@pytest.mark.asyncio
async def test_other_agents_cannot_see_compound_okr_jobs(monkeypatch) -> None:
    tools = [builtin_model_definition(name) for name in OKR_JOB_TOOLS]

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
            {*agent_tools.RUNTIME_TYPED_APPLICATION_TOOL_NAMES, *OKR_JOB_TOOLS}
        ),
    )

    resolved = await agent_tools.get_runtime_agent_tools_for_llm(uuid.uuid4())

    assert {
        tool["function"]["name"] for tool in resolved
    }.isdisjoint(OKR_JOB_TOOLS)


@pytest.mark.asyncio
@pytest.mark.parametrize("tool_name", OKR_JOB_TOOLS)
async def test_direct_compound_job_execution_requires_designated_okr_agent(
    monkeypatch,
    tool_name: str,
) -> None:
    agent_id = uuid.uuid4()
    tenant_id = uuid.uuid4()
    _install_runtime_context(
        monkeypatch,
        agent_id=agent_id,
        tenant_id=tenant_id,
        designated=False,
    )

    async def forbidden(*args, **kwargs):
        raise AssertionError("unauthorized OKR job reached the scheduler")

    monkeypatch.setattr(okr_scheduler, "collect_all_focus_updates", forbidden)
    monkeypatch.setattr(okr_scheduler, "generate_daily_report", forbidden)
    monkeypatch.setattr(okr_scheduler, "generate_weekly_report", forbidden)
    monkeypatch.setattr(okr_scheduler, "generate_monthly_report", forbidden)

    arguments = {"report_type": "daily"} if tool_name == "generate_okr_report" else {}
    outcome = _assert_typed(
        await _execute(tool_name, arguments, agent_id=agent_id),
        "failed",
    )

    assert outcome.error_code == "okr_agent_required"
    assert outcome.retryable is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("service_status", "updated", "skipped", "errors", "expected_status"),
    [
        ("succeeded", 0, 0, 0, "succeeded"),
        ("succeeded", 3, 1, 0, "succeeded"),
        ("partial", 2, 0, 1, "failed"),
    ],
    ids=["zero-updates", "all-settled", "partial-errors"],
)
async def test_collect_progress_maps_structured_service_receipt(
    monkeypatch,
    service_status: str,
    updated: int,
    skipped: int,
    errors: int,
    expected_status: str,
) -> None:
    agent_id = uuid.uuid4()
    tenant_id = uuid.uuid4()
    operation_id = str(uuid.uuid4())
    update_refs = [f"okr-progress-log://{uuid.uuid4()}" for _ in range(updated)]
    _install_runtime_context(
        monkeypatch,
        agent_id=agent_id,
        tenant_id=tenant_id,
    )

    async def collect(**kwargs):
        assert kwargs["tenant_id"] == tenant_id
        assert kwargs["okr_agent_id"] == agent_id
        return {
            "status": service_status,
            "operation_id": operation_id,
            "updated_count": updated,
            "skipped_count": skipped,
            "error_count": errors,
            "updated_refs": update_refs,
        }

    monkeypatch.setattr(okr_scheduler, "collect_all_focus_updates", collect)

    outcome = _assert_typed(
        await _execute("collect_okr_progress", {}, agent_id=agent_id),
        expected_status,
    )

    assert outcome.result_ref == f"okr-collection://{operation_id}"
    assert outcome.metadata["updated_count"] == updated
    assert outcome.metadata["skipped_count"] == skipped
    assert outcome.metadata["error_count"] == errors
    assert outcome.metadata["updated_refs"] == update_refs
    assert outcome.retryable is False
    if service_status == "partial":
        assert outcome.error_code == "okr_collection_partial_failure"


@pytest.mark.asyncio
async def test_collect_commit_started_exception_is_unknown(monkeypatch) -> None:
    agent_id = uuid.uuid4()
    tenant_id = uuid.uuid4()
    operation_id = str(uuid.uuid4())
    _install_runtime_context(
        monkeypatch,
        agent_id=agent_id,
        tenant_id=tenant_id,
    )

    async def collect(**_kwargs):
        raise CommitStartedError(
            "database connection reset during commit",
            operation_id=operation_id,
        )

    monkeypatch.setattr(okr_scheduler, "collect_all_focus_updates", collect)

    outcome = _assert_typed(
        await _execute("collect_okr_progress", {}, agent_id=agent_id),
        "unknown",
    )

    assert outcome.result_ref == f"okr-collection://{operation_id}"
    assert outcome.error_code == "okr_collection_commit_outcome_unknown"
    assert outcome.retryable is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("agents", "contents", "expected"),
    [
        ([], {}, (0, 0, 0)),
    ],
    ids=["no-agents"],
)
async def test_collection_service_returns_a_structured_zero_receipt(
    monkeypatch,
    agents: list,
    contents: dict,
    expected: tuple[int, int, int],
) -> None:
    db = FakeDB(FakeResult(items=agents))
    monkeypatch.setattr(okr_scheduler, "async_session", SessionFactory(db))
    monkeypatch.setattr(
        okr_scheduler,
        "get_storage_backend",
        lambda: FakeStorage(contents),
    )

    receipt = await okr_scheduler.collect_all_focus_updates(
        tenant_id=uuid.uuid4(),
        okr_agent_id=uuid.uuid4(),
    )

    assert _field(receipt, "status") == "succeeded"
    assert (
        _field(receipt, "updated_count"),
        _field(receipt, "skipped_count"),
        _field(receipt, "error_count"),
    ) == expected


@pytest.mark.asyncio
@pytest.mark.parametrize("partial", [False, True], ids=["all-settled", "partial-error"])
async def test_collection_service_preserves_stable_update_receipts(
    monkeypatch,
    partial: bool,
) -> None:
    tenant_id = uuid.uuid4()
    okr_agent_id = uuid.uuid4()
    kr_id = uuid.uuid4()
    first_agent = SimpleNamespace(id=uuid.uuid4(), name="Ada")
    agents = [first_agent]
    second_agent = None
    if partial:
        second_agent = SimpleNamespace(id=uuid.uuid4(), name="Grace")
        agents.append(second_agent)
    kr = SimpleNamespace(
        id=kr_id,
        title="Release quality",
        current_value=0.0,
        target_value=10.0,
        status="behind",
        last_updated_at=None,
    )
    objective = SimpleNamespace(tenant_id=tenant_id)
    db = FakeDB(
        FakeResult(items=agents),
        FakeResult(first=(kr, objective)),
    )
    first_key = okr_scheduler.agent_storage_key(first_agent.id, "focus.md")
    contents = {first_key: _focus_content(kr_id, 8.0)}
    read_errors: set[str] = set()
    if second_agent is not None:
        second_key = okr_scheduler.agent_storage_key(second_agent.id, "focus.md")
        contents[second_key] = "unreadable"
        read_errors.add(second_key)
    monkeypatch.setattr(okr_scheduler, "async_session", SessionFactory(db))
    monkeypatch.setattr(
        okr_scheduler,
        "get_storage_backend",
        lambda: FakeStorage(contents, read_errors=read_errors),
    )

    receipt = await okr_scheduler.collect_all_focus_updates(
        tenant_id=tenant_id,
        okr_agent_id=okr_agent_id,
    )

    expected_status = "partial" if partial else "succeeded"
    assert _field(receipt, "status") == expected_status
    assert _field(receipt, "updated_count") == 1
    assert _field(receipt, "error_count") == int(partial)
    update_refs = _field(receipt, "updated_refs")
    assert len(update_refs) == 1
    assert update_refs[0].startswith("okr-progress-log://")
    assert db.commit_calls == 1


@pytest.mark.asyncio
async def test_collection_service_marks_commit_ambiguity_unknown(
    monkeypatch,
) -> None:
    tenant_id = uuid.uuid4()
    okr_agent_id = uuid.uuid4()
    kr_id = uuid.uuid4()
    agent = SimpleNamespace(id=uuid.uuid4(), name="Ada")
    kr = SimpleNamespace(
        id=kr_id,
        title="Release quality",
        current_value=0.0,
        target_value=10.0,
        status="behind",
        last_updated_at=None,
    )
    db = FakeDB(
        FakeResult(items=[agent]),
        FakeResult(first=(kr, SimpleNamespace(tenant_id=tenant_id))),
        commit_error=ConnectionResetError("commit result lost"),
    )
    focus_key = okr_scheduler.agent_storage_key(agent.id, "focus.md")
    monkeypatch.setattr(okr_scheduler, "async_session", SessionFactory(db))
    monkeypatch.setattr(
        okr_scheduler,
        "get_storage_backend",
        lambda: FakeStorage({focus_key: _focus_content(kr_id, 8.0)}),
    )

    receipt = await okr_scheduler.collect_all_focus_updates(
        tenant_id=tenant_id,
        okr_agent_id=okr_agent_id,
    )

    assert _field(receipt, "status") == "unknown"
    assert _field(receipt, "error_code") == "okr_collection_commit_outcome_unknown"
    assert db.commit_calls == 1


@pytest.mark.asyncio
async def test_weekly_report_selects_the_period_containing_current_week(
    monkeypatch,
) -> None:
    captured: dict[str, date | None] = {}
    settings = SimpleNamespace(
        enabled=True,
        period_frequency="quarter",
        period_length_days=90,
    )
    db = FakeDB(FakeResult(scalar=settings))

    async def snapshot(*args, target_date=None, **kwargs):
        del args, kwargs
        captured["target_date"] = target_date
        return [], {}, date(2026, 7, 1), date(2026, 9, 30)

    async def store(*args, **kwargs):
        del args, kwargs
        return {"report_id": str(uuid.uuid4())}

    async def project(*args, **kwargs):
        del args, kwargs
        return {"status": "succeeded"}

    monkeypatch.setattr(okr_scheduler, "date", FrozenDate)
    monkeypatch.setattr(okr_scheduler, "async_session", SessionFactory(db))
    monkeypatch.setattr(okr_scheduler, "_build_okr_snapshot", snapshot)
    monkeypatch.setattr(okr_scheduler, "_store_report", store)
    monkeypatch.setattr(okr_scheduler, "_safe_write_report", project)

    await okr_scheduler.generate_weekly_report(uuid.uuid4(), uuid.uuid4())

    assert captured["target_date"] == FrozenDate.today()


@pytest.mark.asyncio
async def test_monthly_report_selects_previous_month_reference(monkeypatch) -> None:
    captured: dict[str, date | None] = {}
    settings = SimpleNamespace(
        enabled=True,
        period_frequency="monthly",
        period_length_days=None,
    )
    db = FakeDB(FakeResult(scalar=settings))

    async def snapshot(*args, target_date=None, **kwargs):
        del args, kwargs
        captured["target_date"] = target_date
        return [], {}, date(2026, 6, 1), date(2026, 6, 30)

    async def store(*args, **kwargs):
        del args, kwargs
        return {"report_id": str(uuid.uuid4())}

    async def project(*args, **kwargs):
        del args, kwargs
        return {"status": "succeeded"}

    monkeypatch.setattr(okr_scheduler, "date", FrozenDate)
    monkeypatch.setattr(okr_scheduler, "async_session", SessionFactory(db))
    monkeypatch.setattr(okr_scheduler, "_build_okr_snapshot", snapshot)
    monkeypatch.setattr(okr_scheduler, "_store_report", store)
    monkeypatch.setattr(okr_scheduler, "_safe_write_report", project)

    await okr_scheduler.generate_monthly_report(uuid.uuid4(), uuid.uuid4())

    previous_month_end = FrozenDate.today().replace(day=1) - timedelta(days=1)
    assert captured["target_date"] == previous_month_end


def _report_case(report_type: str) -> tuple[str, dict, str]:
    if report_type == "monthly":
        return "generate_monthly_okr_report", {}, "generate_monthly_report"
    return (
        "generate_okr_report",
        {"report_type": report_type},
        f"generate_{report_type}_report",
    )


def _report_receipt(
    report_type: str,
    *,
    projection_status: str,
) -> dict:
    report_id = str(uuid.uuid4())
    period_start = {
        "daily": "2026-07-16",
        "weekly": "2026-07-13",
        "monthly": "2026-06-01",
    }[report_type]
    period_end = {
        "daily": "2026-07-16",
        "weekly": "2026-07-19",
        "monthly": "2026-06-30",
    }[report_type]
    return {
        "status": "succeeded" if projection_status == "succeeded" else "partial",
        "db_status": "succeeded",
        "report_id": report_id,
        "report_type": report_type,
        "period_start": period_start,
        "period_end": period_end,
        "workspace_path": f"workspace/reports/{report_type}_{period_start}.md",
        "projection_status": projection_status,
        "content": REPORT_BODY,
    }


def _install_report_helpers(
    monkeypatch,
    selected_helper: str,
    result: dict | BaseException,
    calls: list[str],
) -> None:
    async def selected(*args, **kwargs):
        del args, kwargs
        calls.append(selected_helper)
        if isinstance(result, BaseException):
            raise result
        return result

    async def forbidden(*args, **kwargs):
        del args, kwargs
        raise AssertionError("wrong canonical OKR report helper selected")

    for helper_name in (
        "generate_daily_report",
        "generate_weekly_report",
        "generate_monthly_report",
    ):
        monkeypatch.setattr(
            okr_scheduler,
            helper_name,
            selected if helper_name == selected_helper else forbidden,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("report_type", ["daily", "weekly", "monthly"])
async def test_report_job_returns_stable_db_and_workspace_receipt(
    monkeypatch,
    report_type: str,
) -> None:
    agent_id = uuid.uuid4()
    tenant_id = uuid.uuid4()
    tool_name, arguments, helper_name = _report_case(report_type)
    receipt = _report_receipt(report_type, projection_status="succeeded")
    calls: list[str] = []
    _install_runtime_context(
        monkeypatch,
        agent_id=agent_id,
        tenant_id=tenant_id,
    )
    _install_report_helpers(monkeypatch, helper_name, receipt, calls)

    outcome = _assert_typed(
        await _execute(tool_name, arguments, agent_id=agent_id),
        "succeeded",
    )

    expected_ref = f"okr-report://{receipt['report_id']}"
    workspace_ref = f"workspace://{agent_id}/{receipt['workspace_path']}"
    assert outcome.result_ref == expected_ref
    assert outcome.artifact_refs == (workspace_ref,)
    assert outcome.metadata["report_id"] == receipt["report_id"]
    assert outcome.metadata["workspace_path"] == receipt["workspace_path"]
    assert outcome.metadata["projection_status"] == "succeeded"
    assert calls == [helper_name]


@pytest.mark.asyncio
@pytest.mark.parametrize("report_type", ["daily", "weekly", "monthly"])
async def test_projection_failure_preserves_db_receipt_without_whole_job_retry(
    monkeypatch,
    report_type: str,
) -> None:
    agent_id = uuid.uuid4()
    tenant_id = uuid.uuid4()
    tool_name, arguments, helper_name = _report_case(report_type)
    receipt = _report_receipt(report_type, projection_status="failed")
    calls: list[str] = []
    _install_runtime_context(
        monkeypatch,
        agent_id=agent_id,
        tenant_id=tenant_id,
    )
    _install_report_helpers(monkeypatch, helper_name, receipt, calls)

    outcome = _assert_typed(
        await _execute(tool_name, arguments, agent_id=agent_id),
        "failed",
    )

    assert outcome.result_ref == f"okr-report://{receipt['report_id']}"
    assert outcome.error_code == "okr_report_projection_failed"
    assert outcome.retryable is False
    assert outcome.metadata["db_status"] == "succeeded"
    assert outcome.metadata["projection_status"] == "failed"
    assert calls == [helper_name]


@pytest.mark.asyncio
async def test_report_commit_started_exception_is_unknown(monkeypatch) -> None:
    agent_id = uuid.uuid4()
    tenant_id = uuid.uuid4()
    report_id = str(uuid.uuid4())
    workspace_path = "workspace/reports/daily_2026-07-16.md"
    calls: list[str] = []
    _install_runtime_context(
        monkeypatch,
        agent_id=agent_id,
        tenant_id=tenant_id,
    )
    _install_report_helpers(
        monkeypatch,
        "generate_daily_report",
        CommitStartedError(
            "database connection reset during commit",
            operation_id=str(uuid.uuid4()),
            report_id=report_id,
            report_type="daily",
            workspace_path=workspace_path,
        ),
        calls,
    )

    outcome = _assert_typed(
        await _execute(
            "generate_okr_report",
            {"report_type": "daily"},
            agent_id=agent_id,
        ),
        "unknown",
    )

    assert outcome.result_ref == f"okr-report://{report_id}"
    assert outcome.error_code == "okr_report_commit_outcome_unknown"
    assert outcome.retryable is False
    assert outcome.metadata["workspace_path"] == workspace_path
    assert calls == ["generate_daily_report"]


@pytest.mark.asyncio
async def test_report_tool_receipt_is_bounded_and_excludes_report_body(
    monkeypatch,
) -> None:
    agent_id = uuid.uuid4()
    tenant_id = uuid.uuid4()
    receipt = _report_receipt("daily", projection_status="succeeded")
    calls: list[str] = []
    _install_runtime_context(
        monkeypatch,
        agent_id=agent_id,
        tenant_id=tenant_id,
    )
    _install_report_helpers(
        monkeypatch,
        "generate_daily_report",
        receipt,
        calls,
    )

    outcome = _assert_typed(
        await _execute(
            "generate_okr_report",
            {"report_type": "daily"},
            agent_id=agent_id,
        ),
        "succeeded",
    )
    serialized = json.dumps(asdict(outcome), ensure_ascii=False, default=str)

    assert "REPORT-BODY-MUST-NOT-LEAK" not in serialized
    assert len(outcome.summary or "") <= 1_000
    assert len(json.dumps(outcome.metadata, default=str)) <= 4_096


@pytest.mark.asyncio
async def test_scheduler_projection_exception_returns_explicit_partial_fact_once(
    monkeypatch,
) -> None:
    report_id = str(uuid.uuid4())
    store_calls = 0
    settings = SimpleNamespace(
        enabled=True,
        period_frequency="quarter",
        period_length_days=90,
    )
    db = FakeDB(FakeResult(scalar=settings))

    async def snapshot(*args, **kwargs):
        del args, kwargs
        return [], {}, date(2026, 7, 1), date(2026, 9, 30)

    async def store(*args, **kwargs):
        nonlocal store_calls
        del args, kwargs
        store_calls += 1
        return {
            "status": "succeeded",
            "report_id": report_id,
            "report_type": "daily",
        }

    async def project(*args, **kwargs):
        del args, kwargs
        raise OSError("workspace storage unavailable")

    monkeypatch.setattr(okr_scheduler, "date", FrozenDate)
    monkeypatch.setattr(okr_scheduler, "async_session", SessionFactory(db))
    monkeypatch.setattr(okr_scheduler, "_build_okr_snapshot", snapshot)
    monkeypatch.setattr(okr_scheduler, "_store_report", store)
    monkeypatch.setattr(okr_scheduler, "_safe_write_report", project)

    try:
        receipt = await okr_scheduler.generate_daily_report(
            uuid.uuid4(),
            uuid.uuid4(),
        )
    except Exception as exc:  # RED: legacy code still loses the DB receipt.
        pytest.fail(f"projection failure escaped after DB success: {type(exc).__name__}")

    assert store_calls == 1
    assert _field(receipt, "status") == "partial"
    assert _field(receipt, "report_id") == report_id
    assert _field(receipt, "projection_status") == "failed"
