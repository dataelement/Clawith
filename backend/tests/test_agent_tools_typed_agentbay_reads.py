"""D-020 AgentBay A1 typed read contracts with a local fake provider."""

from __future__ import annotations

import base64
from collections import deque
from contextlib import asynccontextmanager
from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any
import uuid

import pytest

from app.models.agent import Agent
from app.models.agent_tool_execution import AgentToolExecution
from app.models.llm import LLMModel
from app.services import activity_logger, agent_tools, agentbay_client, vision_inject
from app.services.agent_runtime.context_builder import RuntimeContextBuild
from app.services.agent_runtime.model_step_service import RuntimeModelStepService
from app.services.agent_runtime import tool_step_service
from app.services.agent_runtime.state import (
    RunInputSnapshots,
    RunRegistrySnapshot,
    RuntimeContext,
    RuntimeGraphState,
)
from app.services.agent_runtime.tool_execution import (
    ToolExecutionOutcome,
    ToolExecutionReservation,
)
from app.services.builtin_tool_definitions import (
    builtin_model_definition,
    builtin_policy,
    builtin_readiness,
)
from app.services.llm.single_step import LLMCompletionStep
from app.services.token_tracker import TokenUsage


AGENTBAY_A1_READ_TOOL_NAMES = frozenset(
    {
        "agentbay_browser_screenshot",
        "agentbay_browser_extract",
        "agentbay_browser_observe",
        "agentbay_code_read_file",
        "agentbay_computer_screenshot",
        "agentbay_computer_precision_screenshot",
        "agentbay_computer_get_screen_size",
        "agentbay_computer_get_installed_apps",
        "agentbay_computer_get_cursor_position",
        "agentbay_computer_get_active_window",
        "agentbay_computer_list_windows",
        "agentbay_computer_list_visible_apps",
    }
)

SCREENSHOT_TOOL_NAMES = frozenset(
    {
        "agentbay_browser_screenshot",
        "agentbay_computer_screenshot",
        "agentbay_computer_precision_screenshot",
    }
)

SESSION_ID = "chat-session-agentbay-a1"
INPUT_MARKER = "USER_INPUT_MUST_NOT_BE_ECHOED_7f3f"
PROVIDER_SECRET = "api_key=akm-provider-secret-must-not-leak"
PRIVATE_IMAGE_REF = "runtime-private-image://opaque-ref"

# A real 1x1 RGBA PNG. Strict screenshot validation must decode an actual image;
# accepting arbitrary base64 or arbitrary bytes is not enough.
PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGP4z8DwHwAFgAI/ScLzWQAAAABJRU5ErkJggg=="
)
PNG_BASE64 = base64.b64encode(PNG_BYTES).decode("ascii")
PNG_SHA256 = hashlib.sha256(PNG_BYTES).hexdigest()


@dataclass(frozen=True, slots=True)
class ReadCase:
    tool_name: str
    image_type: str
    provider_method: str
    arguments: dict[str, Any]
    success_payload: object
    malformed_payload: object
    expected_fragment: str
    empty_payload: object | None = None


READ_CASES = (
    ReadCase(
        "agentbay_browser_extract",
        "browser",
        "browser_extract",
        {"instruction": f"Extract names. {INPUT_MARKER}", "selector": "main"},
        {"success": True, "data": {"names": ["Ada"]}},
        {"success": True, "data": object()},
        "Ada",
        {"success": True, "data": {}},
    ),
    ReadCase(
        "agentbay_browser_observe",
        "browser",
        "browser_observe",
        {"instruction": f"Find controls. {INPUT_MARKER}", "selector": "body"},
        {"success": True, "elements": [{"role": "button", "name": "Save"}]},
        {"success": True, "elements": "not-a-list"},
        "button",
        {"success": True, "elements": []},
    ),
    ReadCase(
        "agentbay_code_read_file",
        "code",
        "code_read_file",
        {"remote_path": "/home/wuying/readme.txt"},
        SimpleNamespace(success=True, content="hello from sandbox", error_message=""),
        SimpleNamespace(success=True, content=object(), error_message=""),
        "hello from sandbox",
        SimpleNamespace(success=True, content="", error_message=""),
    ),
    ReadCase(
        "agentbay_computer_get_screen_size",
        "computer",
        "computer_get_screen_size",
        {},
        {"success": True, "data": {"width": 1920, "height": 1080}},
        {"success": True, "data": {"width": "wide", "height": 1080}},
        "1920",
    ),
    ReadCase(
        "agentbay_computer_get_installed_apps",
        "computer",
        "computer_get_installed_apps",
        {"start_menu": True, "desktop": False, "ignore_system_apps": True},
        {
            "success": True,
            "apps": [{"name": "Calculator", "start_cmd": "calc.exe"}],
        },
        {"success": True, "apps": "not-a-list"},
        "Calculator",
        {"success": True, "apps": []},
    ),
    ReadCase(
        "agentbay_computer_get_cursor_position",
        "computer",
        "computer_get_cursor_position",
        {},
        {"success": True, "data": {"x": 11, "y": 22}},
        {"success": True, "data": {"x": "left", "y": 22}},
        "11",
    ),
    ReadCase(
        "agentbay_computer_get_active_window",
        "computer",
        "computer_get_active_window",
        {},
        {
            "success": True,
            "window": {"window_id": 7, "title": "Editor", "x": 0, "y": 0},
        },
        {"success": True, "window": "not-an-object"},
        "Editor",
    ),
    ReadCase(
        "agentbay_computer_list_windows",
        "computer",
        "computer_list_windows",
        {"timeout_ms": 1750},
        {
            "success": True,
            "windows": [{"window_id": 7, "title": "Editor"}],
        },
        {"success": True, "windows": "not-a-list"},
        "window_id",
        {"success": True, "windows": []},
    ),
    ReadCase(
        "agentbay_computer_list_visible_apps",
        "computer",
        "computer_list_visible_apps",
        {},
        {"success": True, "apps": [{"name": "Browser", "pid": 42}]},
        {"success": True, "apps": {"name": "Browser"}},
        "Browser",
        {"success": True, "apps": []},
    ),
)

SCREENSHOT_CASES = (
    ReadCase(
        "agentbay_browser_screenshot",
        "browser",
        "browser_screenshot",
        {},
        {"success": True, "screenshot": PNG_BASE64},
        {"success": True, "screenshot": base64.b64encode(b"not an image").decode()},
        "screenshot",
    ),
    ReadCase(
        "agentbay_computer_screenshot",
        "computer",
        "computer_screenshot",
        {},
        {"success": True, "data": PNG_BASE64},
        {"success": True, "data": b"not an image"},
        "screenshot",
    ),
    ReadCase(
        "agentbay_computer_precision_screenshot",
        "computer",
        "computer_screenshot",
        {"x": 0, "y": 0, "width": 1, "height": 1},
        {"success": True, "data": PNG_BASE64},
        {"success": True, "data": "not-base64"},
        "screenshot",
    ),
)

ALL_CASES = READ_CASES + SCREENSHOT_CASES
EMPTY_CASES = tuple(case for case in READ_CASES if case.empty_payload is not None)


def case_id(case: ReadCase) -> str:
    return case.tool_name.removeprefix("agentbay_")


class FakeFileSystem:
    def __init__(self, client: "FakeAgentBayClient") -> None:
        self._client = client

    def read_file(self, remote_path: str):
        return self._client.dispatch("code_read_file", remote_path)


class FakeAgentBayClient:
    """One scripted AgentBay client matching both current and typed adapters."""

    def __init__(
        self,
        *,
        expected_method: str,
        response: object,
    ) -> None:
        self.expected_method = expected_method
        self.response = response
        self.calls: list[tuple[str, tuple, dict]] = []
        self.process_cache_calls: list[bytes] = []
        self._session = SimpleNamespace(file_system=FakeFileSystem(self))

    def dispatch(self, method: str, *args, **kwargs):
        self.calls.append((method, args, kwargs))
        if method == "computer_get_screen_size" and self.expected_method in {
            "computer_screenshot",
        }:
            return {"success": True, "data": {"width": 1, "height": 1}}
        assert method == self.expected_method
        if isinstance(self.response, BaseException):
            raise self.response
        return deepcopy(self.response)

    def __getattr__(self, name: str):
        if name == "code_read_file" or name.startswith(("browser_", "computer_")):

            async def call(*args, **kwargs):
                return self.dispatch(name, *args, **kwargs)

            return call
        raise AttributeError(name)


def assert_outcome(
    value: ToolExecutionOutcome | str,
    status: str,
) -> ToolExecutionOutcome:
    assert isinstance(value, ToolExecutionOutcome)
    assert value.status == status
    return value


def outcome_text(outcome: ToolExecutionOutcome) -> str:
    return json.dumps(
        {
            "summary": outcome.summary,
            "result_ref": outcome.result_ref,
            "artifact_refs": outcome.artifact_refs,
            "evidence_refs": outcome.evidence_refs,
            "metadata": outcome.metadata,
            "error_code": outcome.error_code,
        },
        ensure_ascii=False,
        default=str,
        sort_keys=True,
    )


def install_provider(
    monkeypatch: pytest.MonkeyPatch,
    case: ReadCase,
    response: object,
    workspace_root: Path,
    *,
    session_error: BaseException | None = None,
) -> tuple[FakeAgentBayClient, list[tuple[uuid.UUID, str, str]]]:
    client = FakeAgentBayClient(
        expected_method=case.provider_method,
        response=response,
    )
    factory_calls: list[tuple[uuid.UUID, str, str]] = []

    async def factory(
        agent_id: uuid.UUID,
        image_type: str,
        session_id: str = "",
        **_kwargs,
    ):
        factory_calls.append((agent_id, image_type, session_id))
        assert image_type == case.image_type
        if session_error is not None:
            raise session_error
        return client

    def record_process_memory(raw_bytes: bytes, **_kwargs):
        client.process_cache_calls.append(raw_bytes)
        return "00000000-0000-0000-0000-000000000001"

    monkeypatch.setattr(
        agentbay_client,
        "get_agentbay_client_for_agent",
        factory,
    )
    monkeypatch.setattr(
        agent_tools,
        "_agent_workspace_root",
        lambda _agent_id: workspace_root,
    )

    async def no_activity(*args, **kwargs):
        del args, kwargs

    monkeypatch.setattr(activity_logger, "log_activity", no_activity)
    if case.tool_name in SCREENSHOT_TOOL_NAMES:
        monkeypatch.setattr(
            vision_inject,
            "store_temp_screenshot",
            record_process_memory,
        )
    return client, factory_calls


async def execute_case(
    case: ReadCase,
    arguments: dict[str, Any] | None = None,
) -> ToolExecutionOutcome | str:
    supplied = deepcopy(case.arguments if arguments is None else arguments)
    before = deepcopy(supplied)
    result = await agent_tools.execute_builtin_tool_outcome(
        case.tool_name,
        supplied,
        agent_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        session_id=SESSION_ID,
    )
    assert supplied == before
    return result


def test_agentbay_a1_read_contract_is_typed_local_read_safe() -> None:
    assert AGENTBAY_A1_READ_TOOL_NAMES <= (agent_tools.RUNTIME_TYPED_APPLICATION_TOOL_NAMES)
    assert {name: builtin_readiness(name) for name in AGENTBAY_A1_READ_TOOL_NAMES} == {
        name: "agentbay_configuration" for name in AGENTBAY_A1_READ_TOOL_NAMES
    }
    assert {name: builtin_policy(name) for name in AGENTBAY_A1_READ_TOOL_NAMES} == {
        name: {
            "effect": "read",
            "retry_policy": "safe",
            "parallel_safe": True,
        }
        for name in AGENTBAY_A1_READ_TOOL_NAMES
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("config", "expected_names"),
    [
        (
            {"api_key": "akm-local-ready", "os_type": "windows"},
            {
                "agentbay_browser_extract",
                "agentbay_computer_get_screen_size",
            },
        ),
        (None, set()),
        ({"api_key": "", "os_type": "windows"}, set()),
        ({"api_key": "encrypted-but-not-decrypted", "os_type": "windows"}, set()),
        ({"api_key": "akm-local-ready", "os_type": "macos"}, set()),
    ],
    ids=["ready", "missing", "blank-key", "invalid-key", "invalid-os"],
)
async def test_runtime_exposes_only_assigned_and_locally_ready_agentbay_reads(
    monkeypatch: pytest.MonkeyPatch,
    config: dict[str, Any] | None,
    expected_names: set[str],
) -> None:
    assigned_names = {
        "agentbay_browser_extract",
        "agentbay_computer_get_screen_size",
    }

    async def assigned_tools(_agent_id: uuid.UUID) -> list[dict]:
        return [builtin_model_definition(name) for name in sorted(assigned_names)]

    async def no_dynamic_mcp(_agent_id: uuid.UUID) -> set[str]:
        return set()

    async def local_config(_agent_id: uuid.UUID, tool_name: str):
        assert tool_name == "agentbay_browser_navigate"
        return deepcopy(config)

    async def local_key(_agent_id: uuid.UUID, db=None):
        del db
        value = (config or {}).get("api_key")
        return value if isinstance(value, str) else None

    class ProviderMustNotBeConstructed:
        def __init__(self, *_args, **_kwargs) -> None:
            raise AssertionError("Runtime readiness must not ping AgentBay")

    monkeypatch.setattr(agent_tools, "get_agent_tools_for_llm", assigned_tools)
    monkeypatch.setattr(
        agent_tools,
        "_get_runtime_dynamic_mcp_tool_names",
        no_dynamic_mcp,
    )
    monkeypatch.setattr(agent_tools, "_get_tool_config", local_config)
    monkeypatch.setattr(
        agentbay_client,
        "get_agentbay_api_key_for_agent",
        local_key,
    )
    monkeypatch.setattr(agentbay_client, "AgentBay", ProviderMustNotBeConstructed)
    monkeypatch.setattr(
        agent_tools,
        "RUNTIME_TYPED_APPLICATION_TOOL_NAMES",
        agent_tools.RUNTIME_TYPED_APPLICATION_TOOL_NAMES | AGENTBAY_A1_READ_TOOL_NAMES,
    )

    resolved = await agent_tools.get_runtime_agent_tools_for_llm(uuid.uuid4())
    resolved_names = {str(tool.get("function", {}).get("name") or "") for tool in resolved}

    assert resolved_names == expected_names
    assert resolved_names <= assigned_names


@pytest.mark.asyncio
@pytest.mark.parametrize("case", READ_CASES, ids=case_id)
async def test_legal_provider_data_is_a_typed_success_without_input_echo(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    case: ReadCase,
) -> None:
    client, factory_calls = install_provider(
        monkeypatch,
        case,
        case.success_payload,
        tmp_path,
    )

    outcome = assert_outcome(await execute_case(case), "succeeded")

    assert case.expected_fragment in (outcome.summary or "")
    assert INPUT_MARKER not in outcome_text(outcome)
    assert factory_calls and factory_calls[0][2] == SESSION_ID
    assert any(call[0] == case.provider_method for call in client.calls)


@pytest.mark.asyncio
@pytest.mark.parametrize("case", EMPTY_CASES, ids=case_id)
async def test_explicit_empty_read_data_is_a_valid_success(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    case: ReadCase,
) -> None:
    install_provider(monkeypatch, case, case.empty_payload, tmp_path)

    outcome = assert_outcome(await execute_case(case), "succeeded")

    assert outcome.error_code is None
    assert outcome.retryable is False


def rejection_payload(case: ReadCase) -> object:
    if case.tool_name == "agentbay_code_read_file":
        return SimpleNamespace(
            success=False,
            content="",
            error_message=PROVIDER_SECRET,
        )
    return {"success": False, "error_message": PROVIDER_SECRET}


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ALL_CASES, ids=case_id)
async def test_explicit_provider_rejection_is_known_nonretryable_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    case: ReadCase,
) -> None:
    install_provider(monkeypatch, case, rejection_payload(case), tmp_path)

    outcome = assert_outcome(await execute_case(case), "failed")

    assert outcome.error_code
    assert outcome.retryable is False
    assert "akm-provider-secret" not in outcome_text(outcome)


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ALL_CASES, ids=case_id)
async def test_provider_read_timeout_is_retryable_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    case: ReadCase,
) -> None:
    install_provider(
        monkeypatch,
        case,
        TimeoutError("provider read timed out"),
        tmp_path,
    )

    outcome = assert_outcome(await execute_case(case), "failed")

    assert outcome.error_code
    assert outcome.retryable is True


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ALL_CASES, ids=case_id)
async def test_malformed_provider_read_is_retryable_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    case: ReadCase,
) -> None:
    install_provider(monkeypatch, case, case.malformed_payload, tmp_path)

    outcome = assert_outcome(await execute_case(case), "failed")

    assert outcome.error_code
    assert outcome.retryable is True


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ALL_CASES, ids=case_id)
async def test_unknown_session_create_response_is_unknown_not_a_safe_read_retry(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    case: ReadCase,
) -> None:
    client, factory_calls = install_provider(
        monkeypatch,
        case,
        case.success_payload,
        tmp_path,
        session_error=TimeoutError("session create response was lost"),
    )

    outcome = assert_outcome(await execute_case(case), "unknown")

    assert outcome.error_code
    assert outcome.retryable is False
    assert factory_calls
    assert client.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("case", SCREENSHOT_CASES, ids=case_id)
async def test_screenshot_success_never_uses_workspace_imageid_or_base64_result(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    case: ReadCase,
) -> None:
    client, _ = install_provider(monkeypatch, case, case.success_payload, tmp_path)

    outcome = assert_outcome(await execute_case(case), "succeeded")
    serialized = outcome_text(outcome)

    assert "ImageID" not in serialized
    assert "base64" not in serialized.lower()
    assert PNG_BASE64 not in serialized
    assert "workspace/" not in serialized
    assert client.process_cache_calls == []
    assert not [path for path in tmp_path.rglob("*") if path.is_file()]


class _ScalarResult:
    def __init__(self, value=None) -> None:
        self.value = value

    def scalar_one_or_none(self):
        return self.value


class _Begin:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        del exc_type, exc, traceback
        return False


class _DB:
    def __init__(self, agent: Agent) -> None:
        self.agent = agent

    async def execute(self, statement):
        del statement
        return _ScalarResult(self.agent)

    def begin(self):
        return _Begin()


def session_factory(agent: Agent):
    @asynccontextmanager
    async def factory():
        yield _DB(agent)

    return factory


class _CancelSource:
    def __init__(self) -> None:
        self.signals = deque()

    async def get_cancel(self, state, context):
        del state, context
        return self.signals.popleft() if self.signals else None


def runtime_agent(tenant_id: uuid.UUID) -> Agent:
    return Agent(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        creator_id=uuid.uuid4(),
        name="AgentBay Read Agent",
        status="idle",
        is_expired=False,
        access_mode="company",
    )


def runtime_call(call_id: str, case: ReadCase) -> dict[str, Any]:
    return {
        "id": call_id,
        "type": "function",
        "function": {
            "name": case.tool_name,
            "arguments": json.dumps(case.arguments),
        },
    }


def runtime_state(
    tenant_id: uuid.UUID,
    agent: Agent,
    call: dict[str, Any],
) -> RuntimeGraphState:
    return {
        "registry": RunRegistrySnapshot(
            tenant_id=str(tenant_id),
            run_id=str(uuid.uuid4()),
            goal="Read AgentBay state",
            run_kind="foreground",
            source_type="chat",
            model_id=str(uuid.uuid4()),
            graph_name="runtime",
            graph_version="v1",
            agent_id=str(agent.id),
            session_id=SESSION_ID,
        ),
        "snapshots": RunInputSnapshots(
            session_context={"version": 0},
            session_context_version=0,
            recent_session_messages=(),
            related_run_summaries=(),
            initial_input={},
        ),
        "lifecycle": {
            "status": "running",
            "next_route": "tool",
            "run_messages": [
                {
                    "id": "assistant-agentbay-a1",
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [call],
                }
            ],
            "pending_tool_calls": [call],
        },
    }


def runtime_context(state: RuntimeGraphState) -> RuntimeContext:
    registry = state["registry"]
    return RuntimeContext(
        tenant_id=registry.tenant_id,
        run_id=registry.run_id,
        command_id="command-agentbay-a1",
        executor=object(),  # type: ignore[arg-type]
        goal=registry.goal,
        run_kind=registry.run_kind,
        source_type=registry.source_type,
        model_id=registry.model_id,
        graph_name=registry.graph_name,
        graph_version=registry.graph_version,
        agent_id=registry.agent_id,
        session_id=registry.session_id,
        system_role=registry.system_role,
        parent_run_id=registry.parent_run_id,
        root_run_id=registry.root_run_id,
        actor_user_id=str(uuid.uuid4()),
    )


def started_execution(
    tenant_id: uuid.UUID,
    run_id: uuid.UUID,
    call_id: str,
    tool_name: str,
) -> AgentToolExecution:
    return AgentToolExecution(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        run_id=run_id,
        tool_call_id=call_id,
        tool_name=tool_name,
        assistant_message_id="assistant-agentbay-a1",
        arguments_hash="hash",
        sanitized_arguments={},
        effect="read",
        retry_policy="safe",
        result_metadata={},
        status="started",
        lease_owner=f"runtime:command-agentbay-a1:{call_id}",
    )


def reservation(
    execution: AgentToolExecution,
    *,
    reusable_result: ToolExecutionOutcome | None = None,
) -> ToolExecutionReservation:
    return ToolExecutionReservation(
        execution=execution,
        created=reusable_result is None,
        retrying=False,
        reusable_result=reusable_result,
        prior_failure=None,
        blocked=False,
        reconciliation_required=False,
        requires_confirmation=False,
        error_code=None,
    )


class OpaquePrivateReceipt(str):
    """String-compatible fake receipt without prescribing production API shape."""

    @property
    def ref(self) -> str:
        return str(self)

    @property
    def content_hash(self) -> str:
        return PNG_SHA256

    @property
    def mime_type(self) -> str:
        return "image/png"

    @property
    def size(self) -> int:
        return len(PNG_BYTES)


class SemanticPrivateStore:
    """Records any ToolResultStore extension by payload semantics, not method name."""

    def __init__(self, shared: dict[str, bytes] | None = None) -> None:
        self.shared = shared if shared is not None else {}
        self.operations: list[tuple[str, tuple, dict]] = []
        self.text_archives: list[str] = []
        self.binary_archives: list[bytes] = []

    async def resolve_binary(self, ref: str, **_scope) -> bytes:
        return self.shared[ref]

    def __getattr__(self, operation_name: str):
        async def operation(*args, **kwargs):
            self.operations.append((operation_name, args, kwargs))
            values = list(args) + list(kwargs.values())
            binary = next((value for value in values if isinstance(value, bytes)), None)
            if binary is not None:
                self.binary_archives.append(binary)
                self.shared[PRIVATE_IMAGE_REF] = binary
                return OpaquePrivateReceipt(PRIVATE_IMAGE_REF)
            text = next(
                (value for value in reversed(values) if isinstance(value, str) and len(value.encode("utf-8")) > 512),
                None,
            )
            if text is not None:
                self.text_archives.append(text)
                execution = next(
                    (value for value in values if isinstance(value, AgentToolExecution)),
                    None,
                )
                assert execution is not None
                return f"tool-result://{execution.id}"
            raise AssertionError(f"Unexpected private store operation {operation_name}: {values!r}")

        return operation


async def run_runtime_case(
    monkeypatch: pytest.MonkeyPatch,
    *,
    case: ReadCase,
    response: object,
    workspace_root: Path,
    result_store: SemanticPrivateStore,
) -> tuple[
    tool_step_service.ToolStepResult,
    AgentToolExecution,
    dict[str, Any],
]:
    install_provider(monkeypatch, case, response, workspace_root)
    tenant_id = uuid.uuid4()
    agent = runtime_agent(tenant_id)
    call = runtime_call("call-agentbay-a1", case)
    state = runtime_state(tenant_id, agent, call)
    context = runtime_context(state)
    execution = started_execution(
        tenant_id,
        uuid.UUID(context.run_id),
        "call-agentbay-a1",
        case.tool_name,
    )
    settled: dict[str, Any] = {}

    async def reserve_tool(db, **kwargs):
        del db, kwargs
        return reservation(execution)

    async def mark_succeeded(db, **kwargs):
        del db
        settled.update(kwargs)
        execution.status = "succeeded"
        execution.result_summary = kwargs["result_summary"]
        execution.result_ref = kwargs["result_ref"]
        execution.result_metadata = kwargs["metadata"]
        return execution

    async def mark_failed(db, **kwargs):
        del db
        settled.update(kwargs)
        execution.status = "failed"
        execution.result_summary = kwargs["result_summary"]
        execution.result_ref = kwargs["result_ref"]
        execution.result_metadata = kwargs["metadata"]
        return execution

    async def mark_unknown(db, **kwargs):
        del db
        settled.update(kwargs)
        execution.status = "unknown"
        execution.result_summary = kwargs["result_summary"]
        execution.result_ref = kwargs["result_ref"]
        execution.result_metadata = kwargs["metadata"]
        return execution

    async def only_tool(_agent_id: uuid.UUID) -> list[dict]:
        return [builtin_model_definition(case.tool_name)]

    monkeypatch.setattr(
        tool_step_service,
        "reserve_tool_execution",
        reserve_tool,
    )
    monkeypatch.setattr(
        tool_step_service,
        "mark_tool_execution_succeeded",
        mark_succeeded,
    )
    monkeypatch.setattr(
        tool_step_service,
        "mark_tool_execution_failed",
        mark_failed,
    )
    monkeypatch.setattr(
        tool_step_service,
        "mark_tool_execution_unknown",
        mark_unknown,
    )
    service = tool_step_service.RuntimeToolStepService(
        session_factory=session_factory(agent),
        cancel_source=_CancelSource(),
        tool_provider=only_tool,
        tool_executor=agent_tools.execute_builtin_tool_outcome,
        tool_result_store=result_store,  # type: ignore[arg-type]
    )
    service._inline_result_max_bytes = 512

    result = await service.execute_pending(state, context, (call,))
    return result, execution, settled


@pytest.mark.asyncio
@pytest.mark.parametrize("case", SCREENSHOT_CASES, ids=case_id)
async def test_runtime_archives_screenshot_binary_before_ledger_and_survives_cache_loss(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    case: ReadCase,
) -> None:
    shared_private_storage: dict[str, bytes] = {}
    store = SemanticPrivateStore(shared_private_storage)

    result, execution, settled = await run_runtime_case(
        monkeypatch,
        case=case,
        response=case.success_payload,
        workspace_root=tmp_path,
        result_store=store,
    )

    assert result.error is None
    assert result.waiting_request is None
    assert execution.status == "succeeded"
    assert store.binary_archives == [PNG_BYTES]
    assert store.operations
    assert shared_private_storage[PRIVATE_IMAGE_REF] == PNG_BYTES
    assert not [path for path in tmp_path.rglob("*") if path.is_file()]

    persisted = json.dumps(settled, ensure_ascii=False, default=str, sort_keys=True)
    assert PRIVATE_IMAGE_REF in persisted
    assert PNG_SHA256 in persisted
    assert "image/png" in persisted
    assert str(len(PNG_BYTES)) in persisted
    assert PNG_BASE64 not in persisted
    assert "ImageID" not in persisted

    # Simulate another Runtime process: the process cache is empty, but the
    # execution-scoped binary is still available and decodes for vision.
    vision_inject._memory_image_cache.clear()
    restarted_store = SemanticPrivateStore(shared_private_storage)
    restored = restarted_store.shared[PRIVATE_IMAGE_REF]
    assert restored == PNG_BYTES
    assert vision_inject.compress_bytes_to_base64(restored)


@pytest.mark.asyncio
async def test_large_agentbay_read_uses_existing_private_result_store_path(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    case = next(case for case in READ_CASES if case.tool_name == "agentbay_browser_extract")
    large_marker = "large-provider-result-"
    large_data = large_marker + ("界" * 6000)
    store = SemanticPrivateStore()

    result, execution, settled = await run_runtime_case(
        monkeypatch,
        case=case,
        response={"success": True, "data": {"content": large_data}},
        workspace_root=tmp_path,
        result_store=store,
    )

    assert result.error is None
    assert execution.status == "succeeded"
    assert store.text_archives
    assert large_data in store.text_archives[0]
    assert execution.result_ref == f"tool-result://{execution.id}"
    assert settled["metadata"]["archive_status"] == "stored"
    assert len(result.messages[0]["content"].encode("utf-8")) <= 512


@pytest.mark.asyncio
async def test_replayed_screenshot_receipt_never_reinvokes_provider_or_rearchives(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = SCREENSHOT_CASES[0]
    tenant_id = uuid.uuid4()
    agent = runtime_agent(tenant_id)
    call = runtime_call("call-replay", case)
    state = runtime_state(tenant_id, agent, call)
    context = runtime_context(state)
    execution = started_execution(
        tenant_id,
        uuid.UUID(context.run_id),
        "call-replay",
        case.tool_name,
    )
    execution.status = "succeeded"
    execution.result_metadata = {
        "evidence_refs": [PRIVATE_IMAGE_REF],
        "content_hash": PNG_SHA256,
        "mime_type": "image/png",
        "size": len(PNG_BYTES),
    }
    reusable = ToolExecutionOutcome(
        status="succeeded",
        result_summary="Internal screenshot available for vision.",
        result_ref=None,
        evidence_refs=(PRIVATE_IMAGE_REF,),
        metadata=execution.result_metadata,
    )

    async def reuse(db, **kwargs):
        del db, kwargs
        return reservation(execution, reusable_result=reusable)

    async def provider_must_not_run(*_args, **_kwargs):
        raise AssertionError("a settled screenshot receipt must be replayed")

    async def only_tool(_agent_id: uuid.UUID) -> list[dict]:
        return [builtin_model_definition(case.tool_name)]

    store = SemanticPrivateStore({PRIVATE_IMAGE_REF: PNG_BYTES})
    monkeypatch.setattr(tool_step_service, "reserve_tool_execution", reuse)
    service = tool_step_service.RuntimeToolStepService(
        session_factory=session_factory(agent),
        cancel_source=_CancelSource(),
        tool_provider=only_tool,
        tool_executor=provider_must_not_run,
        tool_result_store=store,  # type: ignore[arg-type]
    )

    result = await service.execute_pending(state, context, (call,))

    assert result.error is None
    assert result.messages[0]["evidence_refs"] == [PRIVATE_IMAGE_REF]
    assert store.operations == []


@pytest.mark.asyncio
async def test_settled_screenshot_is_resolved_after_restart_only_for_next_model_request(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    case = SCREENSHOT_CASES[0]
    shared_private_storage: dict[str, bytes] = {}
    writer = SemanticPrivateStore(shared_private_storage)
    tool_result, execution, _settled = await run_runtime_case(
        monkeypatch,
        case=case,
        response=case.success_payload,
        workspace_root=tmp_path,
        result_store=writer,
    )
    persisted_tool_message = deepcopy(tool_result.messages[0])
    persisted_execution_metadata = deepcopy(execution.result_metadata)

    tenant_id = execution.tenant_id
    model = LLMModel(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        provider="openai",
        model="vision-runtime-model",
        api_key_encrypted="encrypted",
        label="Vision Runtime Model",
        enabled=True,
        supports_vision=True,
        max_output_tokens=2048,
        max_input_tokens=100_000,
    )
    agent = runtime_agent(tenant_id)
    context = RuntimeContext(
        tenant_id=str(tenant_id),
        run_id=str(execution.run_id),
        command_id="command-vision-consumer",
        executor=object(),  # type: ignore[arg-type]
        goal="Inspect screenshot",
        run_kind="foreground",
        source_type="chat",
        model_id=str(model.id),
        graph_name="runtime",
        graph_version="v1",
        agent_id=str(agent.id),
        session_id=SESSION_ID,
    )
    assistant_message = {
        "id": "assistant-before-screenshot",
        "role": "assistant",
        "content": "",
        "tool_calls": [runtime_call("call-agentbay-a1", case)],
    }
    build = RuntimeContextBuild(
        session_context_snapshot={"version": 0},
        current_run={"goal": "Inspect screenshot"},
        related_run_summaries=(),
        pending_session_messages_snapshot=(),
        recent_session_messages_snapshot=(
            {
                "id": "user-vision-request",
                "role": "user",
                "content": "Inspect the current screen",
            },
        ),
        thread_running_summary=None,
        recent_thread_messages=(
            assistant_message,
            persisted_tool_message,
        ),
        initial_input={"message_id": "user-vision-request"},
        resume_input=None,
        omitted_tool_exchanges=(),
        retry_model=False,
        blocked=False,
        requires_confirmation=False,
    )

    class FixedContextBuilder:
        async def build(self, *_args, **_kwargs):
            return build

    captured: dict[str, Any] = {}

    async def completion(model_arg, messages, **kwargs):
        captured.update(
            model=model_arg,
            messages=messages,
            kwargs=kwargs,
        )
        return LLMCompletionStep(
            content="screen inspected",
            tool_calls=(),
            reasoning_content=None,
            retry_instruction=None,
            usage=TokenUsage(input_tokens=1, output_tokens=1),
        )

    restarted_store = SemanticPrivateStore(shared_private_storage)
    service = RuntimeModelStepService(
        session_factory=session_factory(agent),
        context_builder=FixedContextBuilder(),  # type: ignore[arg-type]
        completion=completion,
        tool_provider=lambda _agent_id: None,  # type: ignore[arg-type]
        prompt_builder=lambda *_args, **_kwargs: None,  # type: ignore[arg-type]
        tool_result_store=restarted_store,  # type: ignore[arg-type]
    )
    prepared = await service._prepare_messages(
        state={  # type: ignore[arg-type]
            "snapshots": RunInputSnapshots(
                session_context={"version": 0},
                session_context_version=0,
                recent_session_messages=(),
                related_run_summaries=(),
                initial_input={},
            ),
            "lifecycle": {
                "status": "running",
                "next_route": "model",
                "pending_tool_calls": [],
            },
        },
        context=context,
        model=model,
        agent=agent,
        ledger={},
        tools=[builtin_model_definition(case.tool_name)],
        static_prompt="Static",
        dynamic_prompt="Dynamic",
    )
    assert isinstance(prepared, list)
    await service._call_prepared(
        model=model,
        agent=agent,
        messages=prepared,
        tools=[builtin_model_definition(case.tool_name)],
    )

    tool_message = next(
        message
        for message in captured["messages"]
        if message.role == "tool"
    )
    assert isinstance(tool_message.content, list)
    image_parts = [
        part
        for part in tool_message.content
        if part.get("type") == "image_url"
    ]
    assert len(image_parts) == 1
    assert image_parts[0]["image_url"]["url"].startswith(
        "data:image/jpeg;base64,"
    )
    assert captured["kwargs"]["supports_vision"] is True

    # The data URL exists only in the ephemeral provider request.
    assert tool_result.messages[0] == persisted_tool_message
    assert execution.result_metadata == persisted_execution_metadata
    durable = json.dumps(
        {
            "message": tool_result.messages[0],
            "metadata": execution.result_metadata,
        },
        sort_keys=True,
    )
    assert "data:image/" not in durable
    assert PNG_BASE64 not in durable

    unavailable_service = RuntimeModelStepService(
        session_factory=session_factory(agent),
        context_builder=FixedContextBuilder(),  # type: ignore[arg-type]
        completion=completion,
        tool_provider=lambda _agent_id: None,  # type: ignore[arg-type]
        prompt_builder=lambda *_args, **_kwargs: None,  # type: ignore[arg-type]
        tool_result_store=SemanticPrivateStore({}),  # type: ignore[arg-type]
    )
    unavailable = await unavailable_service._prepare_messages(
        state={  # type: ignore[arg-type]
            "snapshots": RunInputSnapshots(
                session_context={"version": 0},
                session_context_version=0,
                recent_session_messages=(),
                related_run_summaries=(),
                initial_input={},
            ),
            "lifecycle": {
                "status": "running",
                "next_route": "model",
                "pending_tool_calls": [],
            },
        },
        context=context,
        model=model,
        agent=agent,
        ledger={},
        tools=[builtin_model_definition(case.tool_name)],
        static_prompt="Static",
        dynamic_prompt="Dynamic",
    )
    assert not isinstance(unavailable, list)
    assert unavailable.intent == "error"
    assert unavailable.error == {
        "code": "agentbay_screenshot_evidence_unavailable",
        "message": (
            "AgentBay screenshot evidence could not be verified for this model "
            "step: ToolResultStoreError"
        ),
    }
