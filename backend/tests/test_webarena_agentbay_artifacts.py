from __future__ import annotations

import json
import uuid
from types import SimpleNamespace

import pytest

from app.services import webarena_agentbay_artifacts as artifacts


@pytest.fixture(autouse=True)
def clear_webarena_contexts():
    artifacts._webarena_contexts.clear()
    yield
    artifacts._webarena_contexts.clear()


def test_register_webarena_context_creates_output_dir(tmp_path):
    agent_id = uuid.uuid4()
    context = artifacts.register_webarena_agentbay_context(
        agent_id=agent_id,
        session_id="session-1",
        task_id="108",
        task_type="NAVIGATE",
        output_root=tmp_path,
    )

    assert context.output_dir == tmp_path / "108"
    assert context.output_dir.exists()
    assert artifacts.get_webarena_agentbay_context(agent_id, "session-1") is context


@pytest.mark.asyncio
async def test_finalize_writes_navigate_response_and_empty_har(tmp_path):
    agent_id = uuid.uuid4()
    artifacts.register_webarena_agentbay_context(
        agent_id=agent_id,
        session_id="task-1",
        task_id="task-1",
        task_type="NAVIGATE",
        output_root=tmp_path,
    )

    await artifacts.finalize_webarena_agentbay_context(
        agent_id=agent_id,
        session_id="task-1",
        final_answer="done",
    )

    output_dir = tmp_path / "task-1"
    response = json.loads((output_dir / "agent_response.json").read_text(encoding="utf-8"))
    har = json.loads((output_dir / "network.har").read_text(encoding="utf-8"))
    meta = json.loads((output_dir / "artifact_meta.json").read_text(encoding="utf-8"))

    assert response == {
        "task_type": "NAVIGATE",
        "status": "SUCCESS",
        "retrieved_data": None,
        "error_details": None,
    }
    assert har["log"]["entries"] == []
    assert meta["network_har_source"] == "empty_fallback"


@pytest.mark.asyncio
async def test_finalize_retrieve_wraps_final_answer(tmp_path):
    agent_id = uuid.uuid4()
    artifacts.register_webarena_agentbay_context(
        agent_id=agent_id,
        session_id="task-2",
        task_id="task-2",
        task_type="RETRIEVE",
        output_root=tmp_path,
    )

    await artifacts.finalize_webarena_agentbay_context(
        agent_id=agent_id,
        session_id="task-2",
        final_answer="42",
    )

    response = json.loads((tmp_path / "task-2" / "agent_response.json").read_text(encoding="utf-8"))
    assert response["retrieved_data"] == ["42"]


@pytest.mark.asyncio
async def test_finalize_error_sets_unknown_error(tmp_path):
    agent_id = uuid.uuid4()
    artifacts.register_webarena_agentbay_context(
        agent_id=agent_id,
        session_id="task-3",
        task_id="task-3",
        task_type="MUTATE",
        output_root=tmp_path,
    )

    await artifacts.finalize_webarena_agentbay_context(
        agent_id=agent_id,
        session_id="task-3",
        final_answer="",
        error="browser failed",
    )

    response = json.loads((tmp_path / "task-3" / "agent_response.json").read_text(encoding="utf-8"))
    assert response["status"] == "UNKNOWN_ERROR"
    assert response["error_details"] == "browser failed"


def test_redact_headers_masks_sensitive_values():
    redacted = artifacts.redact_headers({
        "Authorization": "Bearer secret",
        "cookie": "a=b",
        "x-api-key": "secret",
        "accept": "text/html",
    })

    values = {item["name"].lower(): item["value"] for item in redacted}
    assert values["authorization"] == "[REDACTED]"
    assert values["cookie"] == "[REDACTED]"
    assert values["x-api-key"] == "[REDACTED]"
    assert values["accept"] == "text/html"


@pytest.mark.asyncio
async def test_maybe_start_recorder_writes_and_starts_script(tmp_path):
    agent_id = uuid.uuid4()
    context = artifacts.register_webarena_agentbay_context(
        agent_id=agent_id,
        session_id="task-4",
        task_id="task-4",
        task_type="NAVIGATE",
        output_root=tmp_path,
    )
    commands = []

    class FakeClient:
        _session = SimpleNamespace()

        async def _ensure_browser_initialized(self):
            return None

        async def command_exec(self, command, timeout_ms=50000, cwd=""):
            commands.append(command)
            return {"success": True, "stdout": "", "stderr": "", "error_message": ""}

    await artifacts.maybe_start_webarena_recorder(agent_id, "task-4", FakeClient())

    assert context.recorder_started is True
    assert context.remote_har_path.endswith("/network.har")
    assert any("base64 -d" in command for command in commands)
    assert any("nohup node" in command for command in commands)
