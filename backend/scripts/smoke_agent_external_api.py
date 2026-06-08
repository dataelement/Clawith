"""Smoke-test the JWT-backed external Agent API mapping against a live Clawith server.

Examples:
    python backend/scripts/smoke_agent_external_api.py \
      --base-url http://192.168.106.163:3008

    python backend/scripts/smoke_agent_external_api.py \
      --base-url http://192.168.106.163:3008 \
      --login-identifier "$CLAWITH_LOGIN_IDENTIFIER" \
      --password "$CLAWITH_PASSWORD"

    python backend/scripts/smoke_agent_external_api.py \
      --base-url http://192.168.106.163:3008 \
      --jwt "$CLAWITH_JWT" \
      --agent-id "$CLAWITH_AGENT_ID"

    python backend/scripts/smoke_agent_external_api.py \
      --base-url http://192.168.106.163:3008 \
      --jwt "$CLAWITH_JWT" \
      --agent-spec-json smoke-agent-spec.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
import zipfile
from io import BytesIO
from pathlib import Path
from typing import Any

import httpx

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from tests.utils.agent_api_client import AgentApiClient


class SmokeFailure(RuntimeError):
    pass


def _ok(name: str, detail: str = "") -> None:
    suffix = f" - {detail}" if detail else ""
    print(f"PASS {name}{suffix}")


def _fail(name: str, detail: str) -> None:
    print(f"FAIL {name} - {detail}")


async def _expect_status(
    client: httpx.AsyncClient,
    method: str,
    path: str,
    *,
    expected: set[int],
    headers: dict[str, str] | None = None,
    params: dict[str, Any] | None = None,
) -> httpx.Response:
    response = await client.request(method, path, headers=headers, params=params)
    if response.status_code not in expected:
        raise SmokeFailure(f"{method} {path} expected {sorted(expected)}, got {response.status_code}: {response.text[:500]}")
    return response


async def smoke_public_routes(base_url: str) -> None:
    async with httpx.AsyncClient(base_url=base_url, timeout=30, follow_redirects=True, trust_env=False) as client:
        health = await _expect_status(client, "GET", "/api/health", expected={200})
        _ok("health", health.text)

        logs_no_auth = await client.get("/api/logs/agent-trace", params={"trace_id": "smoke"})
        if logs_no_auth.status_code == 404:
            raise SmokeFailure(
                "GET /api/logs/agent-trace returned 404. The deployed backend image does not include the new logs router."
            )
        if logs_no_auth.status_code not in {401, 403}:
            raise SmokeFailure(
                f"GET /api/logs/agent-trace without JWT expected 401/403, got {logs_no_auth.status_code}: "
                f"{logs_no_auth.text[:500]}"
            )
        _ok("logs route requires jwt", f"status={logs_no_auth.status_code}")


async def login_for_jwt(
    base_url: str,
    login_identifier: str,
    password: str,
    *,
    tenant_id: str = "",
) -> tuple[str, dict[str, Any]]:
    payload: dict[str, Any] = {
        "login_identifier": login_identifier,
        "password": password,
    }
    if tenant_id:
        payload["tenant_id"] = tenant_id

    async with httpx.AsyncClient(base_url=base_url, timeout=30, follow_redirects=True, trust_env=False) as client:
        response = await client.post("/api/auth/login", json=payload)
    if response.status_code != 200:
        raise SmokeFailure(f"login failed: HTTP {response.status_code}: {response.text[:500]}")

    data = response.json()
    if data.get("requires_tenant_selection"):
        choices = [
            f"{item.get('tenant_name')} ({item.get('tenant_id')})"
            for item in data.get("tenants", [])
            if item.get("tenant_id")
        ]
        raise SmokeFailure(
            "login requires tenant selection. Re-run with --tenant-id. "
            f"Available tenants: {', '.join(choices) or data.get('tenants')}"
        )

    token = data.get("access_token")
    if not token:
        raise SmokeFailure(f"login response did not include access_token: {data}")
    user = data.get("user") or {}
    _ok("login", f"user_id={user.get('id')}, role={user.get('role')}")
    return token, data


async def smoke_jwt_routes(base_url: str, jwt: str) -> None:
    async with AgentApiClient(base_url, jwt, timeout=60) as api:
        trace_logs = await api.get_run_trace(trace_id="smoke-nonexistent-trace")
    if trace_logs != []:
        raise SmokeFailure(f"expected empty trace list for nonexistent trace, got {trace_logs!r}")
    _ok("logs route accepts jwt", "empty list for nonexistent trace")


async def smoke_existing_agent(base_url: str, jwt: str, agent_id: str, prompt: str) -> None:
    async with AgentApiClient(base_url, jwt, timeout=180) as api:
        result = await api.chat_sync(agent_id, prompt, include_logs=True)
        if not result.get("reply"):
            raise SmokeFailure(f"chat_sync returned empty reply: {result}")
        if not result.get("trace_id"):
            raise SmokeFailure(f"chat_sync did not return trace_id: {result}")
        events = [entry.get("event") for entry in result.get("logs", [])]
        if "prompt" not in events or "response" not in events:
            raise SmokeFailure(f"trace logs missing prompt/response events: {events}")
    _ok("existing agent chat + trace", f"trace_id={result['trace_id']}")


def _pick_enabled_models(models: list[dict[str, Any]]) -> tuple[dict[str, Any], dict[str, Any]]:
    enabled = [model for model in models if model.get("enabled") and model.get("id")]
    if len(enabled) < 2:
        labels = [str(model.get("label") or model.get("model") or model.get("id")) for model in enabled]
        raise SmokeFailure(
            "complete flow needs at least two enabled LLM models to verify primary/fallback selection. "
            f"Found {len(enabled)} enabled model(s): {labels}"
        )
    return enabled[0], enabled[1]


def _pick_enabled_tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    candidates = [
        tool
        for tool in tools
        if tool.get("enabled")
        and tool.get("id")
        and tool.get("category") != "system"
        and not (tool.get("config") or {}).get("okr_agent_only")
    ]
    if len(candidates) < 2:
        names = [str(tool.get("name") or tool.get("id")) for tool in candidates]
        raise SmokeFailure(
            "complete flow needs at least two enabled non-system tools. "
            f"Found {len(candidates)} candidate(s): {names}"
        )
    return candidates[:2]


def _pick_skills(skills: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not skills:
        return []
    defaults = [skill for skill in skills if skill.get("is_default")]
    return (defaults or skills)[:2]


async def build_auto_agent_spec(api: AgentApiClient) -> tuple[dict[str, Any], dict[str, Any]]:
    models = await api.list_llm_models()
    tools = await api.list_tools()
    skills = await api.list_skills()

    primary_model, fallback_model = _pick_enabled_models(models)
    picked_tools = _pick_enabled_tools(tools)
    picked_skills = _pick_skills(skills)
    stamp = int(time.time())

    spec = {
        "agent": {
            "name": f"Codex External API Smoke {stamp}",
            "role_description": "Runs a short live API smoke test and writes a concise result.",
            "bio": "Temporary live smoke-test agent created through the external API client.",
            "primary_model_id": str(primary_model["id"]),
            "fallback_model_id": str(fallback_model["id"]),
            "permission_scope_type": "company",
            "permission_access_level": "manage",
            "autonomy_policy": {
                "mode": "test",
                "max_rounds": 3,
            },
        },
        "mind": {
            "personality": "precise, brief, verification-oriented",
            "boundaries": "never expose secrets; never mutate unrelated files",
            "soul": "# Soul\nPrefer direct verification and keep results short.",
            "memory": "This is a Codex live smoke test for JWT external API flow.",
        },
        "skills": {
            "skill_ids": [str(skill["id"]) for skill in picked_skills],
        },
        "tools": {
            "assignments": [
                {"tool_id": str(tool["id"]), "enabled": True}
                for tool in picked_tools
            ],
        },
    }
    summary = {
        "primary_model": primary_model.get("label") or primary_model.get("model") or primary_model["id"],
        "fallback_model": fallback_model.get("label") or fallback_model.get("model") or fallback_model["id"],
        "tools": [tool.get("display_name") or tool.get("name") or tool["id"] for tool in picked_tools],
        "skills": [skill.get("name") or skill["id"] for skill in picked_skills],
    }
    return spec, summary


def _events(entries: list[dict[str, Any]]) -> list[str]:
    return [str(entry.get("event")) for entry in entries]


async def smoke_create_run_collect(
    base_url: str,
    jwt: str,
    spec: dict[str, Any],
    prompt: str,
    *,
    eval_artifacts: dict[str, Any] | None = None,
) -> None:
    async with AgentApiClient(base_url, jwt, timeout=180) as api:
        created = await api.create_agent_from_spec(spec)
        agent_id = created["agent_id"]
        _ok("create agent", f"agent_id={agent_id}")

        agent = await api.get_agent(agent_id)
        primary_model_id = str(agent.get("primary_model_id") or "")
        fallback_model_id = str(agent.get("fallback_model_id") or "")
        expected_primary = str((spec.get("agent") or {}).get("primary_model_id") or "")
        expected_fallback = str((spec.get("agent") or {}).get("fallback_model_id") or "")
        if primary_model_id != expected_primary or fallback_model_id != expected_fallback:
            raise SmokeFailure(
                f"agent model mismatch: primary={primary_model_id}, fallback={fallback_model_id}"
            )
        if primary_model_id == fallback_model_id:
            raise SmokeFailure("agent primary_model_id and fallback_model_id are not different")

        soul = await api.read_file(agent_id, "soul.md")
        memory = await api.read_file(agent_id, "memory/memory.md")
        if soul.get("content") != (spec.get("mind") or {}).get("soul"):
            raise SmokeFailure("soul.md content mismatch")
        if memory.get("content") != (spec.get("mind") or {}).get("memory"):
            raise SmokeFailure("memory/memory.md content mismatch")

        assigned_tools = await api.get_agent_tools(agent_id)
        enabled_tool_ids = {
            str(tool.get("id"))
            for tool in assigned_tools
            if tool.get("enabled")
        }
        expected_tool_ids = {
            str(item.get("tool_id"))
            for item in (spec.get("tools") or {}).get("assignments", [])
            if item.get("enabled", True)
        }
        if not expected_tool_ids.issubset(enabled_tool_ids):
            raise SmokeFailure(
                f"tool assignment mismatch: expected={sorted(expected_tool_ids)}, enabled={sorted(enabled_tool_ids)}"
            )
        _ok("agent models + tools + mind", f"tools={len(expected_tool_ids)}")

        chat = await api.chat_sync(agent_id, prompt, include_logs=True)
        chat_events = _events(chat.get("logs", []))
        if "prompt" not in chat_events or "response" not in chat_events:
            raise SmokeFailure(f"chat trace logs missing prompt/response events: {chat_events}")
        _ok("chat run + trace", f"trace_id={chat['trace_id']}")

        collected = await api.run_and_collect(
            agent_id,
            title="Smoke external API run",
            prompt=prompt,
            wait_timeout=180,
            poll_interval=2,
            eval_artifacts=eval_artifacts,
        )
        run_id = str(collected["run"].get("id") or collected["run"].get("task_id"))
        status = collected["status"]
        if status.get("status") != "done":
            raise SmokeFailure(f"task run did not complete: run_id={run_id}, status={status}")
        trace_events = _events(collected["trace_logs"])
        if "prompt" not in trace_events or "response" not in trace_events:
            raise SmokeFailure(f"run trace logs missing prompt/response for run_id={run_id}: {trace_events}")

        workspace_zip = collected["workspace_zip"]
        with zipfile.ZipFile(BytesIO(workspace_zip)) as archive:
            names = archive.namelist()
        _ok("run collect logs + workspace", f"run_id={run_id}, files={len(names)}")

        if eval_artifacts:
            output_dir = Path(eval_artifacts["output_root"]) / eval_artifacts["task_id"]
            response_path = output_dir / "agent_response.json"
            har_path = output_dir / "network.har"
            if not response_path.exists() or not har_path.exists():
                raise SmokeFailure(f"missing WebArena artifacts under {output_dir}")
            _ok("webarena artifacts", f"{response_path}, {har_path}")


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://192.168.106.163:3008")
    parser.add_argument("--jwt", default=os.getenv("CLAWITH_JWT", ""))
    parser.add_argument("--login-identifier", default=os.getenv("CLAWITH_LOGIN_IDENTIFIER", ""))
    parser.add_argument("--password", default=os.getenv("CLAWITH_PASSWORD", ""))
    parser.add_argument("--tenant-id", default=os.getenv("CLAWITH_TENANT_ID", ""))
    parser.add_argument("--agent-id", default="")
    parser.add_argument("--prompt", default="请用一句话回复 smoke test")
    parser.add_argument("--agent-spec-json", type=Path)
    parser.add_argument("--skip-complete-flow", action="store_true")
    parser.add_argument("--webarena-output-root", default="")
    parser.add_argument("--webarena-task-id", default="agentbay-smoke-001")
    parser.add_argument("--webarena-task-type", default="NAVIGATE")
    args = parser.parse_args()

    start = time.perf_counter()
    try:
        await smoke_public_routes(args.base_url)
        jwt = args.jwt
        if not jwt and args.login_identifier and args.password:
            jwt, _login_data = await login_for_jwt(
                args.base_url,
                args.login_identifier,
                args.password,
                tenant_id=args.tenant_id,
            )

        if jwt:
            await smoke_jwt_routes(args.base_url, jwt)
        else:
            print("SKIP jwt routes - pass --jwt or --login-identifier/--password to test authenticated APIs")
        if jwt and args.agent_id:
            await smoke_existing_agent(args.base_url, jwt, args.agent_id, args.prompt)
        else:
            print("SKIP existing agent chat - pass authenticated args and --agent-id")
        if jwt and not args.skip_complete_flow:
            if args.agent_spec_json:
                spec = json.loads(args.agent_spec_json.read_text(encoding="utf-8"))
                summary = {"source": str(args.agent_spec_json)}
            else:
                async with AgentApiClient(args.base_url, jwt, timeout=60) as api:
                    spec, summary = await build_auto_agent_spec(api)
                _ok("discover models/tools/skills", json.dumps(summary, ensure_ascii=False))
            eval_artifacts = None
            if args.webarena_output_root:
                eval_artifacts = {
                    "type": "webarena_verified",
                    "task_id": args.webarena_task_id,
                    "task_type": args.webarena_task_type,
                    "output_root": args.webarena_output_root,
                }
            await smoke_create_run_collect(args.base_url, jwt, spec, args.prompt, eval_artifacts=eval_artifacts)
        else:
            print("SKIP create/run/workspace flow - pass authenticated args or remove --skip-complete-flow")
    except Exception as exc:
        _fail("smoke", str(exc))
        return 1

    _ok("smoke complete", f"{time.perf_counter() - start:.2f}s")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
