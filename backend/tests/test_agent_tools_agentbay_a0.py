from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
import threading
import time
import uuid
from unittest.mock import AsyncMock

import pytest

from app.api import agentbay_control
from app.services import agent_tools, agentbay_client, agentbay_live
from app.services.builtin_tool_definitions import (
    BUILTIN_TOOL_DEFINITIONS,
    builtin_model_definition,
    builtin_readiness,
)


AGENTBAY_TOOL_NAMES = frozenset(
    {
        "agentbay_browser_navigate",
        "agentbay_browser_screenshot",
        "agentbay_browser_save_screenshot",
        "agentbay_browser_click",
        "agentbay_browser_type",
        "agentbay_code_execute",
        "agentbay_code_write_file",
        "agentbay_code_read_file",
        "agentbay_code_edit_file",
        "agentbay_browser_extract",
        "agentbay_browser_observe",
        "agentbay_browser_login",
        "agentbay_command_exec",
        "agentbay_computer_screenshot",
        "agentbay_computer_save_screenshot",
        "agentbay_computer_click",
        "agentbay_computer_precision_screenshot",
        "agentbay_computer_input_text",
        "agentbay_computer_press_keys",
        "agentbay_computer_scroll",
        "agentbay_computer_move_mouse",
        "agentbay_computer_drag_mouse",
        "agentbay_computer_get_screen_size",
        "agentbay_computer_start_app",
        "agentbay_computer_get_installed_apps",
        "agentbay_computer_get_cursor_position",
        "agentbay_computer_get_active_window",
        "agentbay_computer_activate_window",
        "agentbay_computer_list_windows",
        "agentbay_computer_close_window",
        "agentbay_computer_dismiss_dialog",
        "agentbay_computer_list_visible_apps",
        "agentbay_file_transfer",
    }
)


@pytest.fixture(autouse=True)
def _reset_agentbay_process_state():
    agentbay_client._agentbay_sessions.clear()
    agentbay_control._browser_initialized.clear()
    agentbay_control._take_control_locks.clear()
    for name in ("_agentbay_session_locks", "_agentbay_cold_start_locks"):
        locks = getattr(agentbay_client, name, None)
        if hasattr(locks, "clear"):
            locks.clear()
    yield
    agentbay_client._agentbay_sessions.clear()
    agentbay_control._browser_initialized.clear()
    agentbay_control._take_control_locks.clear()
    for name in ("_agentbay_session_locks", "_agentbay_cold_start_locks"):
        locks = getattr(agentbay_client, name, None)
        if hasattr(locks, "clear"):
            locks.clear()


def _agentbay_model_definitions() -> list[dict]:
    definitions = []
    for name in sorted(AGENTBAY_TOOL_NAMES):
        definition = builtin_model_definition(name)
        assert definition is not None
        definitions.append(definition)
    return definitions


def _install_runtime_catalog(
    monkeypatch: pytest.MonkeyPatch,
    *,
    config: dict | None,
    expose_as_typed: bool,
) -> list[tuple[uuid.UUID, str]]:
    config_calls: list[tuple[uuid.UUID, str]] = []

    async def assigned_tools(_agent_id: uuid.UUID) -> list[dict]:
        return _agentbay_model_definitions()

    async def no_dynamic_mcp(_agent_id: uuid.UUID) -> set[str]:
        return set()

    async def local_tool_config(agent_id: uuid.UUID, tool_name: str):
        config_calls.append((agent_id, tool_name))
        return deepcopy(config)

    async def local_api_key(_agent_id: uuid.UUID, db=None):
        del db
        value = (config or {}).get("api_key")
        return value if isinstance(value, str) and value.strip() else None

    class ProviderCallForbidden:
        def __init__(self, *args, **kwargs):
            del args, kwargs
            raise AssertionError("Runtime readiness must not ping AgentBay")

    monkeypatch.setattr(agent_tools, "get_agent_tools_for_llm", assigned_tools)
    monkeypatch.setattr(
        agent_tools,
        "_get_runtime_dynamic_mcp_tool_names",
        no_dynamic_mcp,
    )
    monkeypatch.setattr(agent_tools, "_get_tool_config", local_tool_config)
    monkeypatch.setattr(
        agentbay_client,
        "get_agentbay_api_key_for_agent",
        local_api_key,
    )
    monkeypatch.setattr(agentbay_client, "AgentBay", ProviderCallForbidden)
    if expose_as_typed:
        monkeypatch.setattr(
            agent_tools,
            "RUNTIME_TYPED_APPLICATION_TOOL_NAMES",
            AGENTBAY_TOOL_NAMES,
        )
    return config_calls


def _runtime_names(tools: list[dict]) -> set[str]:
    return {
        str(tool.get("function", {}).get("name") or "")
        for tool in tools
    }


def test_agentbay_registry_has_the_33_unique_canonical_names():
    definitions = [
        definition
        for definition in BUILTIN_TOOL_DEFINITIONS
        if definition.get("category") == "agentbay"
    ]
    names = [str(definition["name"]) for definition in definitions]

    assert len(names) == 33
    assert len(names) == len(set(names))
    assert set(names) == AGENTBAY_TOOL_NAMES
    assert {builtin_readiness(name) for name in names} == {
        "agentbay_configuration"
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("os_type", ["linux", "windows"])
async def test_agentbay_readiness_uses_only_local_key_and_os_configuration(
    monkeypatch: pytest.MonkeyPatch,
    os_type: str,
):
    agent_id = uuid.uuid4()
    config_calls = _install_runtime_catalog(
        monkeypatch,
        config={"api_key": "akm-local-test", "os_type": os_type},
        expose_as_typed=True,
    )

    resolved = await agent_tools.get_runtime_agent_tools_for_llm(agent_id)

    assert _runtime_names(resolved) == AGENTBAY_TOOL_NAMES
    assert config_calls
    assert {tool_name for _, tool_name in config_calls} == {
        "agentbay_browser_navigate"
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "config",
    [
        None,
        {},
        {"api_key": "", "os_type": "windows"},
        {"api_key": "not-an-agentbay-key", "os_type": "windows"},
        {"api_key": "akm-local-test"},
        {"api_key": "akm-local-test", "os_type": "macos"},
    ],
)
async def test_agentbay_readiness_hides_locally_incomplete_configuration(
    monkeypatch: pytest.MonkeyPatch,
    config: dict | None,
):
    _install_runtime_catalog(
        monkeypatch,
        config=config,
        expose_as_typed=True,
    )

    resolved = await agent_tools.get_runtime_agent_tools_for_llm(uuid.uuid4())

    assert _runtime_names(resolved).isdisjoint(AGENTBAY_TOOL_NAMES)


@pytest.mark.asyncio
async def test_untyped_agentbay_tools_stay_hidden_even_when_locally_ready(
    monkeypatch: pytest.MonkeyPatch,
):
    _install_runtime_catalog(
        monkeypatch,
        config={"api_key": "akm-local-test", "os_type": "windows"},
        expose_as_typed=False,
    )
    untyped_names = (
        AGENTBAY_TOOL_NAMES - agent_tools.RUNTIME_TYPED_APPLICATION_TOOL_NAMES
    )

    resolved = await agent_tools.get_runtime_agent_tools_for_llm(uuid.uuid4())

    assert _runtime_names(resolved).isdisjoint(untyped_names)


@pytest.mark.asyncio
async def test_dispatch_keeps_durable_arguments_deeply_unchanged_and_does_not_inject_session_id(
    monkeypatch: pytest.MonkeyPatch,
):
    seen_arguments: list[dict] = []

    def unlocked(*_args, **_kwargs) -> bool:
        return False

    async def command_handler(_agent_id, _workspace: Path, arguments: dict):
        seen_arguments.append(deepcopy(arguments))
        return "ok"

    monkeypatch.setattr(agentbay_control, "is_session_locked", unlocked)
    monkeypatch.setattr(agent_tools, "_agentbay_command_exec", command_handler)
    arguments = {
        "command": "printf ok",
        "timeout_ms": 1234,
        "metadata": {"nested": [1, {"keep": True}]},
    }
    original = deepcopy(arguments)

    await agent_tools.execute_tool(
        "agentbay_command_exec",
        arguments,
        uuid.uuid4(),
        uuid.uuid4(),
        session_id=str(uuid.uuid4()),
    )

    assert arguments == original
    assert seen_arguments == [original]
    assert "_session_id" not in arguments
    assert "_session_id" not in seen_arguments[0]


class _FakeRemoteSession:
    def __init__(self, session_id: str):
        self.session_id = session_id
        self.deleted = False

    def delete(self):
        self.deleted = True


class _FakeAgentBaySDK:
    instances: list["_FakeAgentBaySDK"] = []
    sessions: dict[str, tuple[dict[str, str], _FakeRemoteSession]] = {}
    create_params: list[object] = []
    list_labels: list[dict[str, str]] = []
    get_ids: list[str] = []
    create_delay = 0.0
    _lock = threading.Lock()

    @classmethod
    def reset(cls):
        cls.instances = []
        cls.sessions = {}
        cls.create_params = []
        cls.list_labels = []
        cls.get_ids = []
        cls.create_delay = 0.0

    def __init__(self, api_key: str):
        self.api_key = api_key
        type(self).instances.append(self)

    def list(self, labels=None, **_kwargs):
        normalized = dict(labels or {})
        type(self).list_labels.append(normalized)
        ids = [
            session_id
            for session_id, (stored_labels, session) in type(self).sessions.items()
            if stored_labels == normalized and not session.deleted
        ]
        return SimpleNamespace(
            success=True,
            session_ids=ids,
            request_id="req-list",
            error_message="",
        )

    def get(self, session_id: str):
        type(self).get_ids.append(session_id)
        entry = type(self).sessions.get(session_id)
        if not entry or entry[1].deleted:
            return SimpleNamespace(
                success=False,
                session=None,
                request_id="req-get",
                error_message="not found",
            )
        return SimpleNamespace(
            success=True,
            session=entry[1],
            request_id="req-get",
            error_message="",
        )

    def create(self, params):
        if type(self).create_delay:
            time.sleep(type(self).create_delay)
        with type(self)._lock:
            type(self).create_params.append(params)
            session_id = f"sdk-session-{len(type(self).create_params)}"
            session = _FakeRemoteSession(session_id)
            type(self).sessions[session_id] = (
                dict(getattr(params, "labels", None) or {}),
                session,
            )
        return SimpleNamespace(
            success=True,
            session=session,
            request_id=f"req-create-{session_id}",
            error_message="",
        )


def _install_fake_agentbay_sdk(monkeypatch: pytest.MonkeyPatch):
    _FakeAgentBaySDK.reset()

    async def local_tool_config(_agent_id: uuid.UUID, tool_name: str):
        assert tool_name == "agentbay_browser_navigate"
        return {"api_key": "akm-local-test", "os_type": "windows"}

    async def no_fallback_key(_agent_id: uuid.UUID, db=None):
        del db
        raise AssertionError("configured canonical AgentBay key must be used")

    monkeypatch.setattr(agentbay_client, "AgentBay", _FakeAgentBaySDK)
    monkeypatch.setattr(agent_tools, "_get_tool_config", local_tool_config)
    monkeypatch.setattr(
        agentbay_client,
        "get_agentbay_api_key_for_agent",
        no_fallback_key,
    )


@pytest.mark.asyncio
async def test_same_chat_session_reuses_agentbay_across_runs(
    monkeypatch: pytest.MonkeyPatch,
):
    _install_fake_agentbay_sdk(monkeypatch)
    agent_id = uuid.uuid4()
    chat_session_id = str(uuid.uuid4())

    first = await agentbay_client.get_agentbay_client_for_agent(
        agent_id,
        "code",
        session_id=chat_session_id,
        run_id=str(uuid.uuid4()),
    )
    second = await agentbay_client.get_agentbay_client_for_agent(
        agent_id,
        "code",
        session_id=chat_session_id,
        run_id=str(uuid.uuid4()),
    )

    assert second is first
    assert len(_FakeAgentBaySDK.create_params) == 1


@pytest.mark.asyncio
async def test_different_chat_sessions_get_isolated_agentbay_sessions(
    monkeypatch: pytest.MonkeyPatch,
):
    _install_fake_agentbay_sdk(monkeypatch)
    agent_id = uuid.uuid4()

    first = await agentbay_client.get_agentbay_client_for_agent(
        agent_id,
        "code",
        session_id=str(uuid.uuid4()),
    )
    second = await agentbay_client.get_agentbay_client_for_agent(
        agent_id,
        "code",
        session_id=str(uuid.uuid4()),
    )

    assert second is not first
    assert len(_FakeAgentBaySDK.create_params) == 2


@pytest.mark.asyncio
async def test_sessionless_calls_are_isolated_per_run_and_reused_within_one_run(
    monkeypatch: pytest.MonkeyPatch,
):
    _install_fake_agentbay_sdk(monkeypatch)
    agent_id = uuid.uuid4()
    run_one = str(uuid.uuid4())
    run_two = str(uuid.uuid4())

    first = await agentbay_client.get_agentbay_client_for_agent(
        agent_id,
        "code",
        session_id="",
        run_id=run_one,
    )
    first_again = await agentbay_client.get_agentbay_client_for_agent(
        agent_id,
        "code",
        session_id="",
        run_id=run_one,
    )
    second = await agentbay_client.get_agentbay_client_for_agent(
        agent_id,
        "code",
        session_id="",
        run_id=run_two,
    )

    assert first_again is first
    assert second is not first
    assert len(_FakeAgentBaySDK.create_params) == 2


@pytest.mark.asyncio
async def test_concurrent_cold_start_creates_only_one_remote_session(
    monkeypatch: pytest.MonkeyPatch,
):
    _install_fake_agentbay_sdk(monkeypatch)
    _FakeAgentBaySDK.create_delay = 0.02
    agent_id = uuid.uuid4()
    chat_session_id = str(uuid.uuid4())

    clients = await asyncio.gather(
        *(
            agentbay_client.get_agentbay_client_for_agent(
                agent_id,
                "code",
                session_id=chat_session_id,
            )
            for _ in range(8)
        )
    )

    assert all(client is clients[0] for client in clients)
    assert len(_FakeAgentBaySDK.create_params) == 1


@pytest.mark.asyncio
async def test_created_labels_restore_the_same_scoped_remote_session_after_cache_loss(
    monkeypatch: pytest.MonkeyPatch,
):
    _install_fake_agentbay_sdk(monkeypatch)
    agent_id = uuid.uuid4()
    chat_session_id = str(uuid.uuid4())

    first = await agentbay_client.get_agentbay_client_for_agent(
        agent_id,
        "code",
        session_id=chat_session_id,
    )
    assert len(_FakeAgentBaySDK.create_params) == 1
    labels = dict(_FakeAgentBaySDK.create_params[0].labels or {})
    assert labels

    agentbay_client._agentbay_sessions.clear()
    different_scope = await agentbay_client.get_agentbay_client_for_agent(
        agent_id,
        "code",
        session_id=str(uuid.uuid4()),
    )
    assert different_scope._session.session_id != first._session.session_id
    assert len(_FakeAgentBaySDK.create_params) == 2
    different_scope_labels = dict(
        _FakeAgentBaySDK.create_params[1].labels or {}
    )
    assert different_scope_labels
    assert different_scope_labels != labels

    agentbay_client._agentbay_sessions.clear()
    different_environment = await agentbay_client.get_agentbay_client_for_agent(
        agent_id,
        "computer",
        session_id=chat_session_id,
    )
    assert different_environment._session.session_id != first._session.session_id
    assert len(_FakeAgentBaySDK.create_params) == 3
    different_environment_labels = dict(
        _FakeAgentBaySDK.create_params[2].labels or {}
    )
    assert different_environment_labels
    assert different_environment_labels != labels
    assert different_environment_labels != different_scope_labels

    agentbay_client._agentbay_sessions.clear()
    restored = await agentbay_client.get_agentbay_client_for_agent(
        agent_id,
        "code",
        session_id=chat_session_id,
    )

    assert restored is not first
    assert restored._session.session_id == first._session.session_id
    assert len(_FakeAgentBaySDK.create_params) == 3
    assert _FakeAgentBaySDK.list_labels[-1] == labels
    assert _FakeAgentBaySDK.get_ids == [first._session.session_id]


@pytest.mark.asyncio
async def test_browser_login_reuses_an_existing_browser_latest_session(
    monkeypatch: pytest.MonkeyPatch,
):
    class BrowserOperator:
        def navigate(self, _url: str):
            return None

        def login(self, _login_config: str, *, use_vision: bool):
            assert use_vision is True
            return SimpleNamespace(success=True, message="logged in")

    client = object.__new__(agentbay_client.AgentBayClient)
    client._session = SimpleNamespace(
        browser=SimpleNamespace(operator=BrowserOperator())
    )
    client._image_type = "browser_latest"
    client._browser_initialized = True
    client.create_session = AsyncMock()
    client._ensure_browser_initialized = AsyncMock()

    async def inline_to_thread(function, *args, **kwargs):
        return function(*args, **kwargs)

    monkeypatch.setattr(agentbay_client.asyncio, "to_thread", inline_to_thread)

    result = await client.browser_login(
        "https://example.test/login",
        '{"api_key":"local","skill_id":"login"}',
    )

    assert result == {"success": True, "message": "logged in"}
    client.create_session.assert_not_awaited()


class _PreviewClient:
    def __init__(self, payload: str):
        self.get_desktop_snapshot_base64 = AsyncMock(return_value=payload)
        self.get_browser_snapshot_base64 = AsyncMock(return_value=payload)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("image_type", "reader_name", "client_method"),
    [
        (
            "computer",
            "get_desktop_screenshot",
            "get_desktop_snapshot_base64",
        ),
        (
            "browser",
            "get_browser_snapshot",
            "get_browser_snapshot_base64",
        ),
    ],
)
async def test_preview_never_fuzzy_reuses_another_session(
    image_type: str,
    reader_name: str,
    client_method: str,
):
    agent_id = uuid.uuid4()
    cached = _PreviewClient("wrong-session-image")
    agentbay_client._agentbay_sessions[(agent_id, "other-session", image_type)] = (
        cached,
        datetime.now(),
    )

    result = await getattr(agentbay_live, reader_name)(agent_id, "requested-session")

    assert result is None
    getattr(cached, client_method).assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("image_type", "reader_name", "client_method"),
    [
        (
            "computer",
            "get_desktop_screenshot",
            "get_desktop_snapshot_base64",
        ),
        (
            "browser",
            "get_browser_snapshot",
            "get_browser_snapshot_base64",
        ),
    ],
)
async def test_preview_reuses_only_the_exact_session_and_environment(
    image_type: str,
    reader_name: str,
    client_method: str,
):
    agent_id = uuid.uuid4()
    cached = _PreviewClient("exact-image")
    agentbay_client._agentbay_sessions[(agent_id, "exact-session", image_type)] = (
        cached,
        datetime.now(),
    )

    result = await getattr(agentbay_live, reader_name)(agent_id, "exact-session")

    assert result == "exact-image"
    getattr(cached, client_method).assert_awaited_once_with()


class _ControlClient:
    def __init__(self, name: str):
        self.name = name
        self._ensure_browser_initialized = AsyncMock()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("cached_session", "cached_environment"),
    [
        ("other-session", "computer"),
        ("requested-session", "browser"),
    ],
)
async def test_take_control_never_fuzzy_reuses_another_scope_or_environment(
    monkeypatch: pytest.MonkeyPatch,
    cached_session: str,
    cached_environment: str,
):
    agent_id = uuid.uuid4()
    cached = _ControlClient("cached")
    fresh = _ControlClient("fresh")
    agentbay_client._agentbay_sessions[
        (agent_id, cached_session, cached_environment)
    ] = (cached, datetime.now())
    factory_calls: list[tuple[uuid.UUID, str, str]] = []

    async def exact_factory(
        requested_agent_id: uuid.UUID,
        image_type: str,
        session_id: str = "",
        **_kwargs,
    ):
        factory_calls.append((requested_agent_id, image_type, session_id))
        return fresh

    monkeypatch.setattr(
        agentbay_client,
        "get_agentbay_client_for_agent",
        exact_factory,
    )

    result = await agentbay_control._get_client(
        agent_id,
        "requested-session",
        "computer",
    )

    assert result is fresh
    assert factory_calls == [(agent_id, "computer", "requested-session")]


@pytest.mark.asyncio
async def test_take_control_reuses_an_exact_scoped_environment(
    monkeypatch: pytest.MonkeyPatch,
):
    agent_id = uuid.uuid4()
    cached = _ControlClient("cached")
    agentbay_client._agentbay_sessions[
        (agent_id, "requested-session", "computer")
    ] = (cached, datetime.now())

    async def no_create(*_args, **_kwargs):
        raise AssertionError("exact Take Control session must be reused")

    monkeypatch.setattr(
        agentbay_client,
        "get_agentbay_client_for_agent",
        no_create,
    )

    result = await agentbay_control._get_client(
        agent_id,
        "requested-session",
        "computer",
    )

    assert result is cached


@pytest.mark.asyncio
async def test_start_app_unknown_result_never_dispatches_a_second_start(
    monkeypatch: pytest.MonkeyPatch,
):
    class UnknownStartClient:
        def __init__(self):
            self.start_calls: list[tuple[str, str]] = []

        async def computer_start_app(self, cmd: str, work_dir: str = ""):
            self.start_calls.append((cmd, work_dir))
            return {
                "success": False,
                "request_id": f"request-{len(self.start_calls)}",
                "error_message": "operation timed out after dispatch",
            }

        async def computer_get_installed_apps(self):
            return {
                "success": True,
                "apps": [
                    {
                        "name": "Notepad",
                        "start_cmd": "notepad.exe",
                        "work_directory": "",
                    }
                ],
            }

        async def computer_list_visible_apps(self):
            return {"success": True, "apps": []}

    client = UnknownStartClient()

    async def get_client(*_args, **_kwargs):
        return client

    monkeypatch.setattr(
        agentbay_client,
        "get_agentbay_client_for_agent",
        get_client,
    )

    await agent_tools._agentbay_computer_start_app(
        uuid.uuid4(),
        Path("/tmp"),
        {"cmd": "Notepad", "_session_id": "chat-session"},
    )

    assert client.start_calls == [("Notepad", "")]


@pytest.mark.asyncio
async def test_computer_click_unknown_result_dispatches_the_click_at_most_once(
    monkeypatch: pytest.MonkeyPatch,
):
    class UnknownClickClient:
        def __init__(self):
            self.click_calls: list[tuple[int, int, str]] = []

        async def computer_get_screen_size(self):
            return {
                "success": True,
                "data": {"width": 1920, "height": 1080},
            }

        async def computer_click(self, x: int, y: int, button: str = "left"):
            self.click_calls.append((x, y, button))
            raise TimeoutError("operation timed out after click dispatch")

    client = UnknownClickClient()

    async def get_client(*_args, **_kwargs):
        return client

    monkeypatch.setattr(
        agentbay_client,
        "get_agentbay_client_for_agent",
        get_client,
    )

    await agent_tools._agentbay_computer_click(
        uuid.uuid4(),
        Path("/tmp"),
        {
            "x": 320,
            "y": 240,
            "button": "left",
            "_session_id": "chat-session",
        },
    )

    assert client.click_calls == [(320, 240, "left")]


@pytest.mark.asyncio
async def test_sdk_result_mapping_preserves_provider_facts_across_operations(
    monkeypatch: pytest.MonkeyPatch,
):
    provider_session = {"session_id": "sdk-session"}
    provider_data = {"provider": "payload"}
    sdk_result = SimpleNamespace(
        success=False,
        request_id="request-123",
        error="provider error",
        error_message="provider error",
        data=provider_data,
        exit=17,
        exit_code=17,
        session=provider_session,
        stdout="",
        stderr="provider error",
    )
    remote_session = SimpleNamespace(
        session_id="sdk-session",
        command=SimpleNamespace(exec=lambda *_args, **_kwargs: sdk_result),
        computer=SimpleNamespace(start_app=lambda *_args, **_kwargs: sdk_result),
    )
    client = object.__new__(agentbay_client.AgentBayClient)
    client._session = remote_session
    client._image_type = "windows_latest"
    client._browser_initialized = False

    async def inline_to_thread(function, *args, **kwargs):
        return function(*args, **kwargs)

    monkeypatch.setattr(agentbay_client.asyncio, "to_thread", inline_to_thread)

    command = await client.command_exec("false")
    start = await client.computer_start_app("unknown-app")

    for mapped in (command, start):
        assert mapped["success"] is False
        assert mapped["request_id"] == "request-123"
        assert mapped.get("error", mapped.get("error_message")) == "provider error"
        assert mapped["data"] == provider_data
        assert mapped.get("exit", mapped.get("exit_code")) == 17
        assert mapped["session"] == provider_session
