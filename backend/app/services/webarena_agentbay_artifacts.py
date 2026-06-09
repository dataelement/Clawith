"""WebArena-Verified artifacts for Clawith's built-in AgentBay browser tools.

This module intentionally keeps the first implementation in-process and
run-scoped: the evaluation platform can pass ``eval_artifacts`` on task
creation, the AgentBay browser session starts a CDP recorder when first used,
and task finalization writes the artifacts expected by WebArena-Verified:

    <output_root>/<task_id>/agent_response.json
    <output_root>/<task_id>/network.har
"""

from __future__ import annotations

import asyncio
import base64
import json
import re
import shlex
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional

from loguru import logger

if TYPE_CHECKING:
    from app.services.agentbay_client import AgentBayClient


SENSITIVE_HEADERS = {
    "authorization",
    "cookie",
    "set-cookie",
    "proxy-authorization",
    "x-api-key",
}


@dataclass
class WebArenaAgentBayContext:
    agent_id: uuid.UUID
    session_id: str
    task_id: str
    task_type: str
    output_dir: Path
    status: str = "SUCCESS"
    recorder_started: bool = False
    remote_dir: str = ""
    remote_har_path: str = ""
    remote_pid_path: str = ""
    remote_script_path: str = ""
    remote_log_path: str = ""
    har_recording_error: str = ""
    screenshot_count: int = 0


_webarena_contexts: dict[tuple[uuid.UUID, str], WebArenaAgentBayContext] = {}


def redact_headers(headers: dict[str, Any] | list[dict[str, Any]] | None) -> list[dict[str, str]]:
    """Return HAR-style headers with sensitive values redacted."""
    if not headers:
        return []

    if isinstance(headers, list):
        pairs = [
            (str(item.get("name", "")), item.get("value", ""))
            for item in headers
            if isinstance(item, dict)
        ]
    else:
        pairs = [(str(name), value) for name, value in headers.items()]

    redacted: list[dict[str, str]] = []
    for name, value in pairs:
        if not name:
            continue
        value_str = "[REDACTED]" if name.lower() in SENSITIVE_HEADERS else str(value)
        redacted.append({"name": name, "value": value_str})
    return redacted


def empty_har() -> dict[str, Any]:
    return {
        "log": {
            "version": "1.2",
            "creator": {
                "name": "clawith-agentbay-cdp-recorder",
                "version": "0.1.0",
            },
            "entries": [],
        }
    }


def register_webarena_agentbay_context(
    *,
    agent_id: uuid.UUID,
    session_id: str,
    task_id: str,
    task_type: str,
    output_root: str | Path,
) -> WebArenaAgentBayContext:
    """Register a WebArena artifact context for one AgentBay-backed run."""
    normalized_task_type = (task_type or "").upper()
    if normalized_task_type not in {"NAVIGATE", "MUTATE", "RETRIEVE"}:
        raise ValueError("eval_artifacts.task_type must be NAVIGATE, MUTATE, or RETRIEVE")

    root = Path(output_root).expanduser()
    if not root.is_absolute():
        raise ValueError("eval_artifacts.output_root must be an absolute path")

    safe_task_id = _safe_name(task_id)
    output_dir = root / safe_task_id
    output_dir.mkdir(parents=True, exist_ok=True)

    context = WebArenaAgentBayContext(
        agent_id=agent_id,
        session_id=str(session_id),
        task_id=str(task_id),
        task_type=normalized_task_type,
        output_dir=output_dir,
    )
    _webarena_contexts[(agent_id, str(session_id))] = context
    logger.info(
        "[WebArenaAgentBay] Registered context agent={} session={} task={} output={}",
        agent_id,
        str(session_id)[:8],
        task_id,
        output_dir,
    )
    return context


def register_webarena_agentbay_context_from_payload(
    *,
    agent_id: uuid.UUID,
    session_id: str,
    payload: dict[str, Any] | None,
) -> Optional[WebArenaAgentBayContext]:
    """Register from the optional TaskCreate.eval_artifacts payload."""
    if not payload:
        return None
    artifact_type = payload.get("type")
    if artifact_type != "webarena_verified":
        raise ValueError("eval_artifacts.type must be webarena_verified")

    task_id = str(payload.get("task_id") or session_id)
    task_type = str(payload.get("task_type") or "NAVIGATE")
    output_root = payload.get("output_root")
    if not output_root:
        raise ValueError("eval_artifacts.output_root is required")

    return register_webarena_agentbay_context(
        agent_id=agent_id,
        session_id=session_id,
        task_id=task_id,
        task_type=task_type,
        output_root=output_root,
    )


def get_webarena_agentbay_context(
    agent_id: uuid.UUID,
    session_id: str,
) -> Optional[WebArenaAgentBayContext]:
    return _webarena_contexts.get((agent_id, str(session_id)))


def record_webarena_agentbay_screenshot(
    *,
    agent_id: uuid.UUID,
    session_id: str,
    tool_name: str,
    image_id: str,
    raw_bytes: bytes,
    metadata: dict[str, Any] | None = None,
) -> Optional[Path]:
    """Persist an AgentBay screenshot under the registered eval artifact dir."""
    context = get_webarena_agentbay_context(agent_id, session_id)
    if not context:
        return None

    try:
        context.screenshot_count += 1
        screenshot_dir = context.output_dir / "screenshots"
        screenshot_dir.mkdir(parents=True, exist_ok=True)

        safe_tool = _safe_name(tool_name)[:80]
        safe_image_id = _safe_name(image_id)[:80]
        extension = _image_extension(raw_bytes)
        filename = f"{context.screenshot_count:04d}-{safe_tool}-{safe_image_id}.{extension}"
        screenshot_path = screenshot_dir / filename
        screenshot_path.write_bytes(raw_bytes)

        entry = {
            "index": context.screenshot_count,
            "image_id": str(image_id),
            "tool_name": str(tool_name),
            "path": screenshot_path.relative_to(context.output_dir).as_posix(),
            "size_bytes": len(raw_bytes),
            "created_at": datetime.now(timezone.utc).isoformat(),
            "metadata": metadata or {},
        }
        manifest_path = screenshot_dir / "manifest.jsonl"
        with manifest_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")

        logger.info(
            "[WebArenaAgentBay] Saved screenshot agent={} session={} path={}",
            agent_id,
            str(session_id)[:8],
            screenshot_path,
        )
        return screenshot_path
    except Exception as exc:
        logger.warning("[WebArenaAgentBay] Failed to save screenshot artifact: {}", exc)
        return None


async def maybe_start_webarena_recorder(
    agent_id: uuid.UUID,
    session_id: str,
    client: "AgentBayClient",
) -> Optional[WebArenaAgentBayContext]:
    """Start the CDP HAR recorder for a registered AgentBay browser run."""
    context = get_webarena_agentbay_context(agent_id, session_id)
    if not context or context.recorder_started:
        return context
    if not getattr(client, "_session", None):
        context.har_recording_error = "AgentBay browser session was not created"
        return context

    await client._ensure_browser_initialized()

    remote_dir = f"clawith_webarena_{_safe_name(context.task_id)}_{uuid.uuid4().hex[:8]}"
    context.remote_dir = remote_dir
    context.remote_har_path = f"{remote_dir}/network.har"
    context.remote_pid_path = f"{remote_dir}/recorder.pid"
    context.remote_script_path = f"{remote_dir}/clawith_webarena_recorder.js"
    context.remote_log_path = f"{remote_dir}/recorder.log"

    recorder_script = _build_recorder_script()
    script_b64 = base64.b64encode(recorder_script.encode("utf-8")).decode("ascii")
    mkdir_cmd = f"mkdir -p {shlex.quote(remote_dir)}"
    write_cmd = (
        f"echo {shlex.quote(script_b64)} | /usr/bin/base64 -d > "
        f"{shlex.quote(context.remote_script_path)}"
    )
    start_cmd = (
        f"nohup node {shlex.quote(context.remote_script_path)} "
        f"{shlex.quote(context.remote_har_path)} "
        f"> {shlex.quote(context.remote_log_path)} 2>&1 & "
        f"echo $! > {shlex.quote(context.remote_pid_path)}"
    )

    try:
        await client.command_exec(mkdir_cmd, timeout_ms=10000)
        write_result = await client.command_exec(write_cmd, timeout_ms=15000)
        if not write_result.get("success"):
            raise RuntimeError(write_result.get("stderr") or write_result.get("error_message") or "script write failed")
        start_result = await client.command_exec(start_cmd, timeout_ms=15000)
        if not start_result.get("success"):
            raise RuntimeError(start_result.get("stderr") or start_result.get("error_message") or "recorder start failed")
        context.recorder_started = True
        logger.info(
            "[WebArenaAgentBay] Started recorder agent={} session={} remote_har={}",
            agent_id,
            str(session_id)[:8],
            context.remote_har_path,
        )
    except Exception as exc:
        context.har_recording_error = str(exc)[:500]
        logger.warning("[WebArenaAgentBay] Failed to start recorder: {}", exc)
    return context


async def finalize_webarena_agentbay_context(
    *,
    agent_id: uuid.UUID,
    session_id: str,
    final_answer: str = "",
    error: str | None = None,
) -> Optional[WebArenaAgentBayContext]:
    """Finalize a WebArena run, writing response/HAR/meta artifacts."""
    key = (agent_id, str(session_id))
    context = _webarena_contexts.pop(key, None)
    if not context:
        return None

    client = _get_cached_browser_client(agent_id, session_id)
    network_har_source = "empty_fallback"
    har_error = context.har_recording_error

    if client and context.recorder_started:
        await _stop_remote_recorder(client, context)
        try:
            network_har_source = await _download_remote_har(client, context)
        except Exception as exc:
            har_error = (har_error + "; " if har_error else "") + str(exc)[:500]
            logger.warning("[WebArenaAgentBay] HAR download failed: {}", exc)

    har_path = context.output_dir / "network.har"
    if network_har_source == "empty_fallback" or not har_path.exists():
        _write_json(har_path, empty_har())

    response_payload = _agent_response_payload(context.task_type, final_answer, error)
    _write_json(context.output_dir / "agent_response.json", response_payload)
    _write_json(
        context.output_dir / "artifact_meta.json",
        {
            "type": "webarena_verified",
            "task_id": context.task_id,
            "task_type": context.task_type,
            "agent_id": str(agent_id),
            "session_id": str(session_id),
            "network_har_source": network_har_source,
            "har_recording_error": har_error or None,
            "recorder_started": context.recorder_started,
            "screenshot_count": context.screenshot_count,
            "screenshots_manifest": (
                "screenshots/manifest.jsonl"
                if (context.output_dir / "screenshots" / "manifest.jsonl").exists()
                else None
            ),
        },
    )
    logger.info(
        "[WebArenaAgentBay] Finalized artifacts agent={} session={} output={} source={}",
        agent_id,
        str(session_id)[:8],
        context.output_dir,
        network_har_source,
    )
    return context


async def _stop_remote_recorder(client: "AgentBayClient", context: WebArenaAgentBayContext) -> None:
    if not context.remote_pid_path:
        return
    cmd = (
        f"if [ -f {shlex.quote(context.remote_pid_path)} ]; then "
        f"kill $(cat {shlex.quote(context.remote_pid_path)}) 2>/dev/null || true; "
        "fi"
    )
    try:
        await client.command_exec(cmd, timeout_ms=10000)
        await asyncio.sleep(0.5)
    except Exception as exc:
        context.har_recording_error = (context.har_recording_error + "; " if context.har_recording_error else "") + str(exc)[:300]


async def _download_remote_har(client: "AgentBayClient", context: WebArenaAgentBayContext) -> str:
    local_har_path = context.output_dir / "network.har"
    if not context.remote_har_path:
        raise RuntimeError("remote HAR path was not set")

    if getattr(client, "_session", None) and getattr(client._session, "file_system", None):
        result = await asyncio.to_thread(
            client._session.file_system.download_file,
            context.remote_har_path,
            str(local_har_path),
        )
        if getattr(result, "success", False) and local_har_path.exists() and local_har_path.stat().st_size > 0:
            return "agentbay_file_system"
        message = getattr(result, "error_message", "") or "file_system.download_file failed"
        logger.warning("[WebArenaAgentBay] file_system HAR download failed: {}", message)

    cat_result = await client.command_exec(
        f"cat {shlex.quote(context.remote_har_path)}",
        timeout_ms=20000,
    )
    stdout = cat_result.get("stdout") or ""
    if cat_result.get("success") and stdout.strip():
        parsed = json.loads(stdout)
        _write_json(local_har_path, parsed)
        return "remote_cat"

    raise RuntimeError(cat_result.get("stderr") or cat_result.get("error_message") or "remote HAR was empty")


def _agent_response_payload(task_type: str, final_answer: str, error: str | None) -> dict[str, Any]:
    status = "UNKNOWN_ERROR" if error else "SUCCESS"
    retrieved_data = [final_answer] if task_type == "RETRIEVE" and not error else None
    return {
        "task_type": task_type,
        "status": status,
        "retrieved_data": retrieved_data,
        "error_details": error,
    }


def _get_cached_browser_client(agent_id: uuid.UUID, session_id: str) -> Optional["AgentBayClient"]:
    try:
        from app.services.agentbay_client import get_cached_agentbay_client_for_agent

        return get_cached_agentbay_client_for_agent(agent_id, "browser", session_id=session_id)
    except Exception as exc:
        logger.warning("[WebArenaAgentBay] Could not get cached AgentBay client: {}", exc)
        return None


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _image_extension(raw_bytes: bytes) -> str:
    if raw_bytes.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if raw_bytes.startswith(b"RIFF") and raw_bytes[8:12] == b"WEBP":
        return "webp"
    return "png"


def _safe_name(value: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value or "").strip())
    return safe or "task"


def _build_recorder_script() -> str:
    """Return the Node.js CDP recorder script run inside the AgentBay browser session."""
    return r"""
const fs = require('fs');
const { chromium } = require('/usr/local/lib/node_modules/playwright');

const harPath = process.argv[2] || 'network.har';
const sensitiveHeaders = new Set(['authorization', 'cookie', 'set-cookie', 'proxy-authorization', 'x-api-key']);
const requests = new Map();
let flushTimer = null;

function redactHeaders(headers) {
  return Object.entries(headers || {}).map(([name, value]) => ({
    name,
    value: sensitiveHeaders.has(String(name).toLowerCase()) ? '[REDACTED]' : String(value)
  }));
}

function emptyHar() {
  return {
    log: {
      version: '1.2',
      creator: { name: 'clawith-agentbay-cdp-recorder', version: '0.1.0' },
      entries: []
    }
  };
}

function toHarEntry(item) {
  const elapsed = Math.max(0, item.endMs ? item.endMs - item.startMs : Date.now() - item.startMs);
  return {
    startedDateTime: new Date(item.startMs).toISOString(),
    time: elapsed,
    request: {
      method: item.method || 'GET',
      url: item.url || '',
      httpVersion: 'HTTP/1.1',
      headers: redactHeaders(item.requestHeaders),
      queryString: [],
      cookies: [],
      headersSize: -1,
      bodySize: -1
    },
    response: {
      status: item.status || 0,
      statusText: item.statusText || '',
      httpVersion: 'HTTP/1.1',
      headers: redactHeaders(item.responseHeaders),
      cookies: [],
      content: {
        size: item.encodedDataLength || 0,
        mimeType: item.mimeType || ''
      },
      redirectURL: '',
      headersSize: -1,
      bodySize: item.encodedDataLength || -1
    },
    cache: {},
    timings: {
      blocked: -1,
      dns: -1,
      connect: -1,
      send: 0,
      wait: elapsed,
      receive: 0,
      ssl: -1
    }
  };
}

function writeHar() {
  const har = emptyHar();
  har.log.entries = Array.from(requests.values()).map(toHarEntry);
  fs.writeFileSync(harPath, JSON.stringify(har, null, 2));
}

function scheduleWrite() {
  if (flushTimer) clearTimeout(flushTimer);
  flushTimer = setTimeout(() => {
    try { writeHar(); } catch (e) { console.error('HAR_WRITE_FAIL:' + e.message); }
  }, 300);
}

async function attachPage(context, page, attached) {
  if (attached.has(page)) return;
  attached.add(page);
  try {
    const cdp = await context.newCDPSession(page);
    await cdp.send('Network.enable');
    cdp.on('Network.requestWillBeSent', params => {
      requests.set(params.requestId, {
        requestId: params.requestId,
        startMs: Date.now(),
        method: params.request && params.request.method,
        url: params.request && params.request.url,
        requestHeaders: params.request && params.request.headers
      });
      scheduleWrite();
    });
    cdp.on('Network.responseReceived', params => {
      const item = requests.get(params.requestId) || { requestId: params.requestId, startMs: Date.now() };
      item.status = params.response && params.response.status;
      item.statusText = params.response && params.response.statusText;
      item.responseHeaders = params.response && params.response.headers;
      item.mimeType = params.response && params.response.mimeType;
      requests.set(params.requestId, item);
      scheduleWrite();
    });
    cdp.on('Network.loadingFinished', params => {
      const item = requests.get(params.requestId) || { requestId: params.requestId, startMs: Date.now() };
      item.endMs = Date.now();
      item.encodedDataLength = params.encodedDataLength || 0;
      requests.set(params.requestId, item);
      scheduleWrite();
    });
    cdp.on('Network.loadingFailed', params => {
      const item = requests.get(params.requestId) || { requestId: params.requestId, startMs: Date.now() };
      item.endMs = Date.now();
      item.statusText = params.errorText || item.statusText || 'loadingFailed';
      requests.set(params.requestId, item);
      scheduleWrite();
    });
  } catch (e) {
    console.error('ATTACH_FAIL:' + e.message);
  }
}

(async () => {
  try {
    fs.mkdirSync(require('path').dirname(harPath), { recursive: true });
    writeHar();
    const browser = await chromium.connectOverCDP('http://localhost:9222');
    const context = browser.contexts()[0];
    if (!context) throw new Error('No browser context found');
    const attached = new WeakSet();
    for (const page of context.pages()) {
      await attachPage(context, page, attached);
    }
    context.on('page', page => attachPage(context, page, attached));
    setInterval(() => {
      try { writeHar(); } catch (e) { console.error('HAR_INTERVAL_FAIL:' + e.message); }
    }, 1000);
    process.on('SIGTERM', () => { try { writeHar(); } finally { process.exit(0); } });
    process.on('SIGINT', () => { try { writeHar(); } finally { process.exit(0); } });
    console.log('RECORDER_READY:' + harPath);
  } catch (e) {
    console.error('RECORDER_FAIL:' + e.message);
    try { writeHar(); } catch (_) {}
    setInterval(() => {}, 1000);
  }
})();
"""
