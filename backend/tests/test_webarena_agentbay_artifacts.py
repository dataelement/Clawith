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
    assert meta["screenshot_count"] == 0
    assert meta["screenshots_manifest"] is None


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


def test_record_screenshot_writes_under_eval_artifacts(tmp_path):
    agent_id = uuid.uuid4()
    context = artifacts.register_webarena_agentbay_context(
        agent_id=agent_id,
        session_id="task-ss",
        task_id="task-ss",
        task_type="RETRIEVE",
        output_root=tmp_path,
    )
    raw_bytes = b"\x89PNG\r\n\x1a\nfake-image"

    path = artifacts.record_webarena_agentbay_screenshot(
        agent_id=agent_id,
        session_id="task-ss",
        tool_name="agentbay_browser_screenshot",
        image_id="image/one",
        raw_bytes=raw_bytes,
        metadata={"url": "https://example.test"},
    )

    expected = tmp_path / "task-ss" / "screenshots" / "0001-agentbay_browser_screenshot-image_one.png"
    assert path == expected
    assert path.read_bytes() == raw_bytes
    assert context.screenshot_count == 1

    manifest_lines = (tmp_path / "task-ss" / "screenshots" / "manifest.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(manifest_lines) == 1
    entry = json.loads(manifest_lines[0])
    assert entry["image_id"] == "image/one"
    assert entry["tool_name"] == "agentbay_browser_screenshot"
    assert entry["path"] == "screenshots/0001-agentbay_browser_screenshot-image_one.png"
    assert entry["metadata"]["url"] == "https://example.test"


def test_record_screenshot_without_context_is_noop(tmp_path):
    path = artifacts.record_webarena_agentbay_screenshot(
        agent_id=uuid.uuid4(),
        session_id="missing-session",
        tool_name="agentbay_browser_screenshot",
        image_id="image-1",
        raw_bytes=b"\x89PNG\r\n\x1a\nfake-image",
    )

    assert path is None
    assert not (tmp_path / "missing-session").exists()


@pytest.mark.asyncio
async def test_finalize_meta_includes_screenshot_manifest(tmp_path):
    agent_id = uuid.uuid4()
    artifacts.register_webarena_agentbay_context(
        agent_id=agent_id,
        session_id="task-meta",
        task_id="task-meta",
        task_type="NAVIGATE",
        output_root=tmp_path,
    )
    artifacts.record_webarena_agentbay_screenshot(
        agent_id=agent_id,
        session_id="task-meta",
        tool_name="agentbay_browser_navigate",
        image_id="image-1",
        raw_bytes=b"\x89PNG\r\n\x1a\nfake-image",
    )

    await artifacts.finalize_webarena_agentbay_context(
        agent_id=agent_id,
        session_id="task-meta",
        final_answer="done",
    )

    meta = json.loads((tmp_path / "task-meta" / "artifact_meta.json").read_text(encoding="utf-8"))
    assert meta["screenshot_count"] == 1
    assert meta["screenshots_manifest"] == "screenshots/manifest.jsonl"


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
