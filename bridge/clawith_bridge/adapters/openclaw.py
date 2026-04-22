"""OpenClaw adapter — wraps a local OpenClaw daemon.

OpenClaw in its "local" mode is the flavor this bridge is meant to eventually
replace. During the transition, two shapes are supported via config:

  1. `mode = "http"`  (default): OpenClaw exposes
       POST /v1/chat          { "messages": [...] }   -> { "job_id": "..." }
       GET  /v1/jobs/{id}/events  (SSE)
     Configure with `base_url` + optional `auth_header`.

  2. `mode = "subprocess"`: Spawn `openclaw run --prompt=...` (for dev setups
     that don't run a long-lived daemon).

V1 implements the HTTP path; the subprocess path is left as a small shim you
can wire up by overriding `build_command` (SubprocessAdapter).
"""
from __future__ import annotations

import json
from typing import Any, AsyncIterator

from .base import DaemonAdapter, SessionEvent


class OpenClawAdapter(DaemonAdapter):
    name = "openclaw"
    capabilities = {"interactive_input": False, "cancellation": True}

    async def start_session_request(
        self,
        prompt: str,
        params: dict[str, Any],
        cwd: str | None,
    ) -> str:
        client = await self._ensure_client()
        body = {"messages": [{"role": "user", "content": prompt}], "params": params or {}}
        r = await client.post("/v1/chat", json=body)
        r.raise_for_status()
        data = r.json()
        job_id = data.get("job_id") or data.get("id")
        if not job_id:
            # OpenClaw may return the response inline (no job_id) for fast paths
            content = (
                data.get("content")
                or data.get("message", {}).get("content")
                or ""
            )
            if content:
                # Stash so iter_events can flush it immediately without hitting the network.
                return f"inline:{json.dumps({'content': content})}"
            raise RuntimeError(f"OpenClaw start response missing job_id: {data}")
        return str(job_id)

    async def iter_events(self, task_id: str) -> AsyncIterator[SessionEvent]:
        if task_id.startswith("inline:"):
            # Synchronous response path — emit once and finish.
            try:
                payload = json.loads(task_id[len("inline:"):])
            except json.JSONDecodeError:
                payload = {}
            content = payload.get("content") or ""
            if content:
                yield SessionEvent(kind="assistant_text", payload={"text": content})
            return

        client = await self._ensure_client()
        async with client.stream("GET", f"/v1/jobs/{task_id}/events") as resp:
            resp.raise_for_status()
            async for raw in resp.aiter_lines():
                if not raw:
                    continue
                line = raw.strip()
                if line.startswith("data:"):
                    line = line[5:].strip()
                if not line or line == "[DONE]":
                    if line == "[DONE]":
                        return
                    continue
                try:
                    evt = json.loads(line)
                except json.JSONDecodeError:
                    yield SessionEvent(kind="stdout_chunk", payload={"text": line})
                    continue
                # Map OpenClaw-native fields to our taxonomy.
                if "delta" in evt:
                    yield SessionEvent(kind="assistant_text", payload={"text": str(evt["delta"])})
                    continue
                if "content" in evt and "role" in evt:
                    yield SessionEvent(kind="assistant_text", payload={"text": str(evt["content"])})
                    continue
                kind = evt.get("kind")
                if isinstance(kind, str):
                    yield SessionEvent(kind=kind, payload=evt.get("payload") or {})
                    if kind in ("done", "finished"):
                        return

    async def cancel_request(self, task_id: str) -> None:
        if task_id.startswith("inline:"):
            return
        client = await self._ensure_client()
        try:
            await client.post(f"/v1/jobs/{task_id}/cancel")
        except Exception:
            pass
