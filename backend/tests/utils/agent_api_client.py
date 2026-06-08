"""JWT-backed convenience client for external Agent API-style tests.

Endpoint mapping:
- chat_sync() -> WS /ws/chat/{agent_id}?token=<jwt>
- create_agent() -> POST /api/agents/
- create_async_run() -> POST /api/agents/{agent_id}/tasks/
- get_run_status() -> GET /api/agents/{agent_id}/tasks/ and client-side filter
- get_run_logs() -> GET /api/agents/{agent_id}/tasks/{task_id}/logs
- export_workspace() -> recursive file list + download + in-memory zip
- upload_artifact() -> POST /api/agents/{agent_id}/files/upload
- get_run_trace() -> GET /api/logs/agent-trace
"""

from __future__ import annotations

import asyncio
import io
import json
import posixpath
import zipfile
from typing import Any
from urllib.parse import quote, urlencode, urlsplit, urlunsplit

import httpx
import websockets


class AgentApiClient:
    """Small SDK that exposes external API semantics over Clawith's existing JWT APIs."""

    def __init__(
        self,
        base_url: str,
        jwt_token: str,
        *,
        timeout: float = 30.0,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.jwt_token = jwt_token
        self.timeout = timeout
        self._client = http_client
        self._owns_client = http_client is None

    async def __aenter__(self) -> "AgentApiClient":
        await self._ensure_client()
        return self

    async def __aexit__(self, _exc_type, _exc, _tb) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()
        self._client = None

    def _auth_headers(self, headers: dict[str, str] | None = None) -> dict[str, str]:
        merged = {"Authorization": f"Bearer {self.jwt_token}"}
        if headers:
            merged.update(headers)
        return merged

    async def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                timeout=self.timeout,
                follow_redirects=True,
                trust_env=False,
            )
        return self._client

    async def _request(self, method: str, path: str, **kwargs) -> httpx.Response:
        """Run an authenticated HTTP request and raise on non-2xx responses."""
        client = await self._ensure_client()
        headers = self._auth_headers(kwargs.pop("headers", None))
        response = await client.request(method, path, headers=headers, timeout=self.timeout, **kwargs)
        response.raise_for_status()
        return response

    def _ws_url(self, agent_id: str, *, session_id: str | None = None, lang: str = "en") -> str:
        parsed = urlsplit(self.base_url)
        scheme = "wss" if parsed.scheme == "https" else "ws"
        query = {
            "token": self.jwt_token,
            "lang": lang,
        }
        if session_id:
            query["session_id"] = session_id
        return urlunsplit((
            scheme,
            parsed.netloc,
            f"/ws/chat/{quote(str(agent_id))}",
            urlencode(query),
            "",
        ))

    async def chat_sync(
        self,
        agent_id: str,
        prompt: str,
        *,
        session_id: str | None = None,
        display_content: str | None = None,
        model_id: str | None = None,
        lang: str = "en",
        include_logs: bool = False,
    ) -> dict[str, Any]:
        """Send one WebSocket chat turn and wait for the final assistant reply."""
        if not prompt:
            raise ValueError("prompt is required")

        payload: dict[str, Any] = {"content": prompt}
        if display_content is not None:
            payload["display_content"] = display_content
        if model_id:
            payload["model_id"] = model_id

        events: list[dict[str, Any]] = []
        ws_url = self._ws_url(agent_id, session_id=session_id, lang=lang)
        async with websockets.connect(ws_url, open_timeout=self.timeout, max_size=32 * 1024 * 1024) as websocket:
            await websocket.send(json.dumps(payload, ensure_ascii=False))
            while True:
                raw = await asyncio.wait_for(websocket.recv(), timeout=self.timeout)
                data = json.loads(raw)
                events.append(data)
                if data.get("type") != "done":
                    continue
                trace_id = data.get("trace_id")
                if not trace_id and len(events) <= 2:
                    # Ignore pre-turn welcome messages emitted before the first real prompt response.
                    continue
                result = {
                    "run_id": trace_id or session_id,
                    "agent_id": str(agent_id),
                    "status": "succeeded",
                    "reply": data.get("content", ""),
                    "trace_id": trace_id,
                }
                if include_logs and trace_id:
                    result["logs"] = await self.get_run_trace(trace_id=trace_id)
                return result

    async def list_agents(self, **params) -> list[dict[str, Any]]:
        return (await self._request("GET", "/api/agents/", params=params or None)).json()

    async def list_llm_models(self, **params) -> list[dict[str, Any]]:
        return (await self._request("GET", "/api/enterprise/llm-models", params=params or None)).json()

    async def list_tools(self, **params) -> list[dict[str, Any]]:
        return (await self._request("GET", "/api/tools", params=params or None)).json()

    async def list_skills(self) -> list[dict[str, Any]]:
        return (await self._request("GET", "/api/skills/")).json()

    async def create_agent(self, data: dict[str, Any] | None = None, **fields) -> dict[str, Any]:
        payload = {**(data or {}), **fields}
        return (await self._request("POST", "/api/agents/", json=payload)).json()

    async def create_agent_from_spec(self, spec: dict[str, Any]) -> dict[str, Any]:
        """Create an agent from an external-style spec using existing Clawith APIs.

        Supported mappings:
        - agent.primary_model_id / fallback_model_id -> POST /api/agents/
        - mind.personality / boundaries -> POST /api/agents/
        - skills.skill_ids -> POST /api/agents/
        - tools.assignments -> PUT /api/tools/agents/{agent_id}
        - mind.soul -> PUT /api/agents/{agent_id}/files/content?path=soul.md
        - mind.memory -> PUT /api/agents/{agent_id}/files/content?path=memory/memory.md
        """
        agent_spec = spec.get("agent") or {}
        mind_spec = spec.get("mind") or {}
        skills_spec = spec.get("skills") or {}

        payload = {
            "name": agent_spec.get("name"),
            "role_description": agent_spec.get("role_description", ""),
            "bio": agent_spec.get("bio"),
            "avatar_url": agent_spec.get("avatar_url"),
            "primary_model_id": agent_spec.get("primary_model_id"),
            "fallback_model_id": agent_spec.get("fallback_model_id"),
            "permission_scope_type": agent_spec.get("permission_scope_type", "company"),
            "permission_access_level": agent_spec.get("permission_access_level", "use"),
            "permission_scope_ids": agent_spec.get("permission_scope_ids", []),
            "tenant_id": (spec.get("company") or {}).get("tenant_id") or agent_spec.get("tenant_id"),
            "template_id": agent_spec.get("template_id"),
            "autonomy_policy": agent_spec.get("autonomy_policy"),
            "personality": mind_spec.get("personality", ""),
            "boundaries": mind_spec.get("boundaries", ""),
            "skill_ids": skills_spec.get("skill_ids", []),
        }
        payload = {key: value for key, value in payload.items() if value is not None}

        created = await self.create_agent(payload)
        agent_id = str(created.get("id") or created.get("agent_id"))
        if not agent_id:
            raise ValueError(f"create_agent response did not include id: {created}")

        tool_assignments = self._tool_assignments_from_spec(spec.get("tools") or {})
        if tool_assignments:
            await self.update_agent_tools(agent_id, tool_assignments)

        if mind_spec.get("soul") is not None:
            await self.write_file(agent_id, "soul.md", mind_spec["soul"])
        if mind_spec.get("memory") is not None:
            await self.write_file(agent_id, "memory/memory.md", mind_spec["memory"])

        created["agent_id"] = agent_id
        return created

    def _tool_assignments_from_spec(self, tools_spec: dict[str, Any]) -> list[dict[str, Any]]:
        assignments: list[dict[str, Any]] = []
        for item in tools_spec.get("assignments") or []:
            tool_id = item.get("tool_id") or item.get("id")
            if not tool_id:
                continue
            assignments.append({"tool_id": str(tool_id), "enabled": bool(item.get("enabled", True))})
        for item in tools_spec.get("builtin") or []:
            tool_id = item.get("tool_id") or item.get("id")
            if not tool_id:
                continue
            assignments.append({"tool_id": str(tool_id), "enabled": bool(item.get("enabled", True))})
        return assignments

    async def get_agent(self, agent_id: str) -> dict[str, Any]:
        return (await self._request("GET", f"/api/agents/{agent_id}")).json()

    async def update_agent(self, agent_id: str, **fields) -> dict[str, Any]:
        return (await self._request("PATCH", f"/api/agents/{agent_id}", json=fields)).json()

    async def delete_agent(self, agent_id: str) -> None:
        await self._request("DELETE", f"/api/agents/{agent_id}")

    async def start_agent(self, agent_id: str) -> dict[str, Any]:
        return (await self._request("POST", f"/api/agents/{agent_id}/start")).json()

    async def stop_agent(self, agent_id: str) -> dict[str, Any]:
        return (await self._request("POST", f"/api/agents/{agent_id}/stop")).json()

    async def create_async_run(
        self,
        agent_id: str,
        *,
        title: str,
        prompt: str | None = None,
        description: str | None = None,
        task_type: str = "todo",
        priority: str = "medium",
        **fields,
    ) -> dict[str, Any]:
        payload = {
            "title": title,
            "description": description if description is not None else (prompt or ""),
            "type": task_type,
            "priority": priority,
            **fields,
        }
        return (await self._request("POST", f"/api/agents/{agent_id}/tasks/", json=payload)).json()

    async def list_tasks(self, agent_id: str, **params) -> list[dict[str, Any]]:
        return (await self._request("GET", f"/api/agents/{agent_id}/tasks/", params=params or None)).json()

    async def get_run_status(self, agent_id: str, run_id: str) -> dict[str, Any]:
        for task in await self.list_tasks(agent_id):
            if str(task.get("id")) == str(run_id):
                return task
        raise LookupError(f"run {run_id} not found for agent {agent_id}")

    async def get_run_logs(self, agent_id: str, run_id: str) -> list[dict[str, Any]]:
        return (await self._request("GET", f"/api/agents/{agent_id}/tasks/{run_id}/logs")).json()

    async def wait_for_run(
        self,
        agent_id: str,
        run_id: str,
        *,
        timeout: float = 180.0,
        poll_interval: float = 2.0,
    ) -> dict[str, Any]:
        """Poll until a task-backed run reaches a terminal state or emits an error log."""
        deadline = asyncio.get_running_loop().time() + timeout
        last_status: dict[str, Any] | None = None
        while True:
            last_status = await self.get_run_status(agent_id, run_id)
            if last_status.get("status") == "done":
                return last_status

            logs = await self.get_run_logs(agent_id, run_id)
            error_log = next(
                (entry for entry in logs if str(entry.get("content", "")).lstrip().startswith("❌")),
                None,
            )
            if error_log:
                last_status = dict(last_status)
                last_status["error_log"] = error_log
                return last_status

            if asyncio.get_running_loop().time() >= deadline:
                return last_status
            await asyncio.sleep(poll_interval)

    async def trigger_task(self, agent_id: str, task_id: str) -> dict[str, Any]:
        return (await self._request("POST", f"/api/agents/{agent_id}/tasks/{task_id}/trigger")).json()

    async def run_and_collect(
        self,
        agent_id: str,
        *,
        title: str,
        prompt: str,
        workspace_root: str = "",
        wait_timeout: float = 180.0,
        poll_interval: float = 2.0,
        eval_artifacts: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Create a task run, then collect status, trace logs, task logs, and workspace zip bytes."""
        fields: dict[str, Any] = {}
        if eval_artifacts:
            fields["eval_artifacts"] = eval_artifacts
        run = await self.create_async_run(agent_id, title=title, prompt=prompt, **fields)
        run_id = str(run.get("id") or run.get("task_id"))
        if not run_id:
            raise ValueError(f"create_async_run response did not include id: {run}")
        status = await self.wait_for_run(
            agent_id,
            run_id,
            timeout=wait_timeout,
            poll_interval=poll_interval,
        )
        return {
            "run": run,
            "status": status,
            "task_logs": await self.get_run_logs(agent_id, run_id),
            "trace_logs": await self.get_run_trace(task_id=run_id),
            "workspace_zip": await self.export_workspace(agent_id, workspace_root),
        }

    async def get_run_trace(
        self,
        trace_id: str | None = None,
        *,
        task_id: str | None = None,
        agent_id: str | None = None,
        act: str = "agent_loop",
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        params = {"act": act, "limit": limit}
        if trace_id:
            params["trace_id"] = trace_id
        if task_id:
            params["task_id"] = task_id
        if agent_id:
            params["agent_id"] = agent_id
        return (await self._request("GET", "/api/logs/agent-trace", params=params)).json()

    async def list_files(self, agent_id: str, path: str = "") -> list[dict[str, Any]]:
        return (await self._request("GET", f"/api/agents/{agent_id}/files/", params={"path": path})).json()

    async def read_file(self, agent_id: str, path: str) -> dict[str, Any]:
        return (await self._request("GET", f"/api/agents/{agent_id}/files/content", params={"path": path})).json()

    async def write_file(
        self,
        agent_id: str,
        path: str,
        content: str,
        *,
        autosave: bool = False,
        session_id: str | None = None,
        expected_version_token: str | None = None,
    ) -> dict[str, Any]:
        payload = {
            "content": content,
            "autosave": autosave,
            "session_id": session_id,
            "expected_version_token": expected_version_token,
        }
        return (
            await self._request("PUT", f"/api/agents/{agent_id}/files/content", params={"path": path}, json=payload)
        ).json()

    async def delete_file(
        self,
        agent_id: str,
        path: str,
        *,
        expected_version_token: str | None = None,
    ) -> dict[str, Any]:
        params = {"path": path}
        if expected_version_token:
            params["expected_version_token"] = expected_version_token
        return (await self._request("DELETE", f"/api/agents/{agent_id}/files/content", params=params)).json()

    async def download_file(self, agent_id: str, path: str) -> bytes:
        return (await self._request("GET", f"/api/agents/{agent_id}/files/download", params={"path": path})).content

    async def upload_artifact(
        self,
        agent_id: str,
        *,
        filename: str,
        content: bytes,
        path: str = "workspace/knowledge_base",
        content_type: str = "application/octet-stream",
    ) -> dict[str, Any]:
        files = {"file": (filename, content, content_type)}
        return (
            await self._request("POST", f"/api/agents/{agent_id}/files/upload", params={"path": path}, files=files)
        ).json()

    async def export_workspace(self, agent_id: str, root_path: str = "") -> bytes:
        """Download the visible workspace tree and return a zip archive as bytes."""
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, mode="w", compression=zipfile.ZIP_DEFLATED) as archive:
            await self._write_workspace_zip_entries(agent_id, root_path, archive)
        return buffer.getvalue()

    async def _write_workspace_zip_entries(self, agent_id: str, path: str, archive: zipfile.ZipFile) -> None:
        for item in await self.list_files(agent_id, path=path):
            item_path = str(item.get("path") or "")
            if not item_path:
                continue
            if item.get("is_dir"):
                await self._write_workspace_zip_entries(agent_id, item_path, archive)
                continue
            data = await self.download_file(agent_id, item_path)
            archive.writestr(posixpath.normpath(item_path), data)

    async def get_permissions(self, agent_id: str) -> dict[str, Any]:
        return (await self._request("GET", f"/api/agents/{agent_id}/permissions")).json()

    async def update_permissions(self, agent_id: str, **fields) -> dict[str, Any]:
        return (await self._request("PUT", f"/api/agents/{agent_id}/permissions", json=fields)).json()

    async def update_agent_tools(self, agent_id: str, updates: list[dict[str, Any]]) -> dict[str, Any]:
        return (await self._request("PUT", f"/api/tools/agents/{agent_id}", json=updates)).json()

    async def get_agent_tools(self, agent_id: str) -> list[dict[str, Any]]:
        return (await self._request("GET", f"/api/tools/agents/{agent_id}")).json()

    async def list_credentials(self, agent_id: str) -> list[dict[str, Any]]:
        return (await self._request("GET", f"/api/agents/{agent_id}/credentials/")).json()
