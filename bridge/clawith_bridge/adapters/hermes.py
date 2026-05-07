"""Hermes daemon adapter.

Hermes is assumed to expose a local HTTP API along the lines of:

    POST   /tasks                 { "prompt": "...", "params": {...} }  -> { "task_id": "..." }
    GET    /tasks/{id}/events     (SSE)  stream of {"kind": "...", "payload": {...}} then {"kind": "done"}
    DELETE /tasks/{id}            204

Configure via `~/.clawith-bridge.toml`:

    [hermes]
    enabled = true
    base_url = "http://127.0.0.1:7890"
    auth_header = "Bearer xxx"

If your actual Hermes API differs, subclass this and override the three methods.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any, AsyncIterator

from .base import DaemonAdapter, SessionEvent


class HermesAdapter(DaemonAdapter):
    name = "hermes"
    capabilities = {"interactive_input": False, "cancellation": True}

    async def start_session_request(
        self,
        prompt: str,
        params: dict[str, Any],
        cwd: str | None,
    ) -> str:
        client = await self._ensure_client()
        body = {"prompt": prompt, "params": params or {}}
        if cwd:
            body["cwd"] = cwd
        r = await client.post("/tasks", json=body)
        r.raise_for_status()
        data = r.json()
        task_id = data.get("task_id") or data.get("id")
        if not task_id:
            raise RuntimeError(f"Hermes start response missing task_id: {data}")
        return str(task_id)

    async def iter_events(self, task_id: str) -> AsyncIterator[SessionEvent]:
        client = await self._ensure_client()
        # SSE-style streaming: each event line begins with `data: `
        async with client.stream("GET", f"/tasks/{task_id}/events") as resp:
            resp.raise_for_status()
            async for raw in resp.aiter_lines():
                if not raw:
                    continue
                line = raw.strip()
                if line.startswith("data:"):
                    line = line[5:].strip()
                if not line:
                    continue
                try:
                    evt = json.loads(line)
                except json.JSONDecodeError:
                    yield SessionEvent(kind="stdout_chunk", payload={"text": line})
                    continue
                kind = evt.get("kind")
                payload = evt.get("payload") or {}
                if kind == "done":
                    return
                if not isinstance(kind, str):
                    continue
                yield SessionEvent(kind=kind, payload=payload)

    async def cancel_request(self, task_id: str) -> None:
        client = await self._ensure_client()
        try:
            await client.delete(f"/tasks/{task_id}")
        except Exception:
            pass
