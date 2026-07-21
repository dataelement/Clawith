"""Resource discovery — search Smithery & ModelScope registries and import MCP servers."""

import uuid
from urllib.parse import quote, urlparse

import httpx
from loguru import logger
from sqlalchemy import select
from app.database import async_session
from app.models.tool import Tool, AgentTool
from app.services.tool_config import (
    decrypt_sensitive_fields,
    get_tenant_tool_config,
    set_tenant_tool_config,
)
from app.services.agent_runtime.tool_execution import ToolExecutionOutcome


# ── Smithery Registry Search ────────────────────────────────────

SMITHERY_API_BASE = "https://registry.smithery.ai"
SMITHERY_CONNECT_API_BASE = "https://api.smithery.ai"
MODELSCOPE_API_BASE = "https://modelscope.cn"


async def _get_smithery_api_key(agent_id: uuid.UUID | None = None) -> str:
    """Read Smithery API key.

    Priority: 1) legacy per-agent AgentTool config, 2) tenant tool config.

    Sensitive fields in tool/AgentTool config are stored encrypted (see
    api.tools._encrypt_sensitive_fields). We must decrypt here before
    handing the value to httpx — otherwise Smithery rejects with 401.
    Falls back to raw value when decrypt fails (e.g. legacy plaintext keys).
    """
    def _maybe_decrypt(raw: str) -> str:
        if not raw:
            return ""
        return decrypt_sensitive_fields({"value": raw}, {"fields": [{"key": "value", "type": "password"}]}).get("value", raw)

    try:
        async with async_session() as db:
            agent_tenant_id = None
            if agent_id:
                from app.models.agent import Agent as AgentModel
                tenant_r = await db.execute(select(AgentModel.tenant_id).where(AgentModel.id == agent_id))
                agent_tenant_id = tenant_r.scalar_one_or_none()

            # 1) Legacy compatibility: read old per-agent key storage.
            if agent_id:
                at_r = await db.execute(
                    select(AgentTool).where(AgentTool.agent_id == agent_id)
                )
                for at in at_r.scalars().all():
                    if at.config and at.config.get("smithery_api_key"):
                        return _maybe_decrypt(at.config["smithery_api_key"])
            # 2) Tenant/company fallback for builtin discovery tools
            for tool_name in ("discover_resources", "import_mcp_server"):
                r = await db.execute(select(Tool).where(Tool.name == tool_name))
                tool = r.scalar_one_or_none()
                if not tool:
                    continue
                tenant_config = await get_tenant_tool_config(db, agent_tenant_id, tool.name, tool.config_schema)
                if tenant_config.get("smithery_api_key"):
                    return tenant_config["smithery_api_key"]
                if tool.config and tool.config.get("smithery_api_key") and not agent_tenant_id:
                    return _maybe_decrypt(tool.config["smithery_api_key"])
    except Exception:
        pass
    return ""


async def _search_smithery_api(query: str, max_results: int, api_key: str) -> list[dict]:
    """Search Smithery registry, returns normalized results."""
    headers = {"Accept": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
        resp = await client.get(
            f"{SMITHERY_API_BASE}/servers",
            params={"q": query, "pageSize": max_results},
            headers=headers,
        )
        resp.raise_for_status()
        data = resp.json()
    results = []
    for srv in data.get("servers", [])[:max_results]:
        results.append({
            "name": srv.get("qualifiedName", ""),
            "display_name": srv.get("displayName", ""),
            "description": srv.get("description", "")[:200],
            "remote": srv.get("remote", False),
            "verified": srv.get("verified", False),
            "use_count": srv.get("useCount", 0),
            "homepage": srv.get("homepage", ""),
            "source": "Smithery",
        })
    return results


async def _get_modelscope_api_token(agent_id: uuid.UUID | None = None) -> str:
    """Read ModelScope API token from discover_resources tool config."""
    try:
        async with async_session() as db:
            agent_tenant_id = None
            if agent_id:
                from app.models.agent import Agent as AgentModel
                tenant_r = await db.execute(select(AgentModel.tenant_id).where(AgentModel.id == agent_id))
                agent_tenant_id = tenant_r.scalar_one_or_none()
            for tool_name in ("discover_resources", "import_mcp_server"):
                r = await db.execute(select(Tool).where(Tool.name == tool_name))
                tool = r.scalar_one_or_none()
                if not tool:
                    continue
                tenant_config = await get_tenant_tool_config(db, agent_tenant_id, tool.name, tool.config_schema)
                if tenant_config.get("modelscope_api_token"):
                    return tenant_config["modelscope_api_token"]
                if tool.config and tool.config.get("modelscope_api_token") and not agent_tenant_id:
                    return tool.config["modelscope_api_token"]
    except Exception:
        pass
    return ""


async def _search_modelscope_api(
    query: str,
    max_results: int,
    api_token: str,
) -> list[dict]:
    """Search ModelScope MCP Hub via official OpenAPI (no WAF issues)."""
    if not api_token:
        return []

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_token}",
        "Cookie": f"m_session_id={api_token}",
        "User-Agent": "modelscope-mcp-server/1.0",
    }
    async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
        resp = await client.put(
            f"{MODELSCOPE_API_BASE}/openapi/v1/mcp/servers",
            json={"page_size": max_results, "page_number": 1, "search": query, "filter": {}},
            headers=headers,
        )
        resp.raise_for_status()
        data = resp.json()
        if not data.get("success"):
            raise RuntimeError("ModelScope rejected the registry search")

    servers_data = data.get("data", {}).get("mcp_server_list", [])
    if not servers_data:
        return []

    results = []
    for srv in servers_data[:max_results]:
        server_id = srv.get("id", "")
        results.append({
            "name": server_id,
            "display_name": srv.get("name", server_id),
            "description": srv.get("description", "")[:200],
            "remote": srv.get("is_hosted", False),
            "verified": True,
            "use_count": 0,
            "homepage": f"https://modelscope.cn/mcp/servers/{server_id}",
            "source": "ModelScope",
        })
    return results


def _registry_failure_retryable(error: BaseException) -> bool:
    if isinstance(error, (httpx.TimeoutException, httpx.TransportError)):
        return True
    if isinstance(error, httpx.HTTPStatusError):
        status = error.response.status_code
        return status == 429 or status >= 500
    return False


async def search_registries_outcome(
    query: str,
    max_results: int = 5,
    agent_id: uuid.UUID | None = None,
) -> ToolExecutionOutcome:
    """Search configured registries and preserve per-provider transport facts."""
    if not isinstance(query, str) or not query.strip():
        return ToolExecutionOutcome(
            status="failed",
            result_summary="discover_resources requires query.",
            result_ref=None,
            error_code="invalid_tool_arguments",
        )
    try:
        max_results = min(max(1, int(max_results)), 10)
    except (TypeError, ValueError):
        return ToolExecutionOutcome(
            status="failed",
            result_summary="discover_resources max_results must be an integer.",
            result_ref=None,
            error_code="invalid_tool_arguments",
        )

    import asyncio

    smithery_key, modelscope_token = await asyncio.gather(
        _get_smithery_api_key(agent_id),
        _get_modelscope_api_token(agent_id),
    )
    searches = []
    if smithery_key:
        searches.append(
            _search_smithery_api(query.strip(), max_results, smithery_key)
        )
    if modelscope_token:
        searches.append(
            _search_modelscope_api(query.strip(), max_results, modelscope_token)
        )
    if not searches:
        return ToolExecutionOutcome(
            status="failed",
            result_summary="No MCP registry credentials are configured.",
            result_ref=None,
            error_code="resource_credentials_missing",
        )

    provider_results = await asyncio.gather(*searches, return_exceptions=True)
    successes = [
        result for result in provider_results if isinstance(result, list)
    ]
    failures = [
        result for result in provider_results if isinstance(result, BaseException)
    ]
    if not successes:
        return ToolExecutionOutcome(
            status="failed",
            result_summary="Configured MCP registries could not be searched.",
            result_ref=None,
            error_code="resource_discovery_failed",
            retryable=any(_registry_failure_retryable(error) for error in failures),
        )

    seen_names = set()
    all_results = []
    for provider_items in successes:
        for item in provider_items:
            name = item.get("name")
            if name and name not in seen_names:
                seen_names.add(name)
                all_results.append(item)

    if not all_results:
        return ToolExecutionOutcome(
            status="succeeded",
            result_summary=(
                f'No MCP servers found for "{query.strip()}" on the '
                "configured registries."
            ),
            result_ref=None,
        )

    lines = []
    for index, server in enumerate(all_results[:max_results], 1):
        verified = " ✅" if server["verified"] else ""
        remote = (
            "🌐 Remote (no local install needed)"
            if server["remote"]
            else "💻 Local install required"
        )
        use_info = (
            f" · 👥 {server['use_count']:,} users"
            if server["use_count"]
            else ""
        )
        homepage = server["homepage"]
        lines.append(
            f"**{index}. {server['display_name']}**{verified} "
            f"[{server['source']}]\n"
            f"   ID: `{server['name']}`\n"
            f"   {server['description']}\n"
            f"   {remote}{use_info}\n"
            f"   {'🔗 ' + homepage if homepage else ''}"
        )
    summary = (
        f'Found {len(lines)} MCP server(s) for "{query.strip()}":\n\n'
        + "\n\n".join(lines)
        + "\n\nUse import_mcp_server with a returned server ID."
    )
    return ToolExecutionOutcome(
        status="succeeded",
        result_summary=summary,
        result_ref=None,
    )


async def search_registries(query: str, max_results: int = 5, agent_id: uuid.UUID | None = None) -> str:
    """Legacy display adapter for registry discovery."""
    outcome = await search_registries_outcome(query, max_results, agent_id)
    return outcome.result_summary or "Resource discovery returned no summary."


# Keep backward-compatible alias
async def search_smithery(query: str, max_results: int = 5, agent_id: uuid.UUID | None = None) -> str:
    return await search_registries(query, max_results, agent_id=agent_id)


# ── Import MCP Server ───────────────────────────────────────────

async def _ensure_smithery_connection(api_key: str, mcp_url: str, display_name: str) -> dict:
    """Create or reuse a Smithery Connect namespace + connection.

    Returns dict with keys: namespace, connection_id, auth_url (if OAuth needed).
    """
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    write_dispatched = False
    try:
        async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
            # Get or create namespace
            ns_resp = await client.get("https://api.smithery.ai/namespaces", headers=headers)
            namespaces = ns_resp.json().get("namespaces", []) if ns_resp.status_code == 200 else []
            if namespaces:
                namespace = namespaces[0]["name"]
            else:
                write_dispatched = True
                create_ns = await client.post(
                    "https://api.smithery.ai/namespaces",
                    json={"name": "clawith"},
                    headers=headers,
                )
                if create_ns.status_code not in (200, 201):
                    return {
                        "error": f"Failed to create namespace: HTTP {create_ns.status_code}",
                        "unknown": False,
                    }
                namespace = create_ns.json()["name"]

            # Create connection
            conn_id = display_name.lower().replace(" ", "-").replace(":", "")
            write_dispatched = True
            conn_resp = await client.post(
                f"https://api.smithery.ai/connect/{namespace}",
                json={"connectionId": conn_id, "mcpUrl": mcp_url, "name": display_name},
                headers=headers,
            )
            if conn_resp.status_code not in (200, 201):
                return {
                    "error": f"Failed to create connection: HTTP {conn_resp.status_code}",
                    "unknown": False,
                }

            conn_data = conn_resp.json()
            result = {
                "namespace": namespace,
                "connection_id": conn_data.get("connectionId", conn_id),
            }
            status = conn_data.get("status", {})
            if isinstance(status, dict):
                state = str(status.get("state") or "").strip().lower()
                if state:
                    result["state"] = state
                if state == "auth_required":
                    result["auth_url"] = status.get("authorizationUrl", "")
            return result
    except Exception as e:
        return {
            "error": type(e).__name__,
            "unknown": write_dispatched,
        }


def _safe_smithery_authorization_url(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    candidate = value.strip()
    try:
        parsed = urlparse(candidate)
    except ValueError:
        return None
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    return candidate


async def get_smithery_connection_status(
    api_key: str,
    namespace: str,
    connection_id: str,
) -> dict:
    """Read one Smithery connection without creating or mutating it."""
    if not api_key or not namespace or not connection_id:
        return {"state": "unavailable"}

    url = (
        f"{SMITHERY_CONNECT_API_BASE}/connect/"
        f"{quote(str(namespace), safe='')}/{quote(str(connection_id), safe='')}"
    )
    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=False) as client:
            response = await client.get(
                url,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Accept": "application/json",
                },
            )
        if response.status_code != 200:
            return {"state": "unavailable"}
        payload = response.json()
    except Exception:
        return {"state": "unavailable"}

    if not isinstance(payload, dict):
        return {"state": "unavailable"}
    raw_status = payload.get("status")
    if isinstance(raw_status, dict):
        state = str(raw_status.get("state") or "").strip().lower()
        authorization_url = raw_status.get("authorizationUrl")
    else:
        state = str(raw_status or payload.get("state") or "").strip().lower()
        authorization_url = payload.get("authorizationUrl")

    if state == "connected":
        return {"state": "connected"}
    if state == "auth_required":
        result = {"state": "auth_required"}
        safe_url = _safe_smithery_authorization_url(authorization_url)
        if safe_url:
            result["authorization_url"] = safe_url
        return result
    return {"state": "unavailable"}


def _smithery_connection_receipt(connection: dict) -> str | None:
    namespace = str(connection.get("namespace") or "").strip()
    connection_id = str(connection.get("connection_id") or "").strip()
    if not namespace or not connection_id:
        return None
    safe_namespace = quote(namespace, safe="@._-")
    safe_connection_id = quote(connection_id, safe="@._-")
    return f"smithery-connection:{safe_namespace}:{safe_connection_id}"


def _smithery_import_completion_outcome(
    *,
    display_name: str,
    server_id: str,
    imported_tools: list[str],
    connection: dict,
) -> ToolExecutionOutcome:
    """Map a committed local import plus provider status to one safe fact."""
    del display_name, server_id
    tool_count = len(imported_tools)
    result_ref = _smithery_connection_receipt(connection)
    state = str(connection.get("state") or "").strip().lower()
    if not state:
        # Backward compatibility for the existing connection-creation helper.
        state = "auth_required" if connection.get("auth_url") else "connected"

    if state == "auth_required":
        return ToolExecutionOutcome(
            status="failed",
            result_summary=(
                f"Saved {tool_count} Smithery tool definition(s), but they are "
                "not available until an authorized user completes OAuth from "
                "the Tools page."
            ),
            result_ref=result_ref,
            error_code="mcp_auth_required",
            retryable=False,
        )
    if state == "connected":
        return ToolExecutionOutcome(
            status="succeeded",
            result_summary=(
                f"Saved {tool_count} Smithery tool definition(s); the "
                "connection is authorized and available."
            ),
            result_ref=result_ref,
        )
    return ToolExecutionOutcome(
        status="failed",
        result_summary=(
            f"Saved {tool_count} Smithery tool definition(s), but connection "
            "authorization status could not be verified. Check it from the "
            "Tools page before use."
        ),
        result_ref=result_ref,
        error_code="mcp_authorization_status_unavailable",
        retryable=False,
    )


async def _existing_smithery_import_outcome(
    *,
    display_name: str,
    server_id: str,
    existing_tools: list[Tool],
    assignments: list[AgentTool],
    api_key: str,
) -> ToolExecutionOutcome:
    """Re-check an already imported connection instead of trusting local rows."""
    if not assignments or len(assignments) < len(existing_tools):
        return ToolExecutionOutcome(
            status="failed",
            result_summary=(
                "Existing Smithery tools do not have a complete assignment set "
                "and cannot be reported ready."
            ),
            result_ref=None,
            error_code="mcp_connection_configuration_missing",
            retryable=False,
        )

    coordinates: set[tuple[str, str]] = set()
    for assignment in assignments:
        assignment_config = assignment.config or {}
        namespace = str(assignment_config.get("smithery_namespace") or "").strip()
        connection_id = str(
            assignment_config.get("smithery_connection_id") or ""
        ).strip()
        if not namespace or not connection_id:
            return ToolExecutionOutcome(
                status="failed",
                result_summary=(
                    "Existing Smithery tools are missing server-side connection "
                    "configuration and cannot be reported ready."
                ),
                result_ref=None,
                error_code="mcp_connection_configuration_missing",
                retryable=False,
            )
        coordinates.add((namespace, connection_id))

    if len(coordinates) != 1:
        return ToolExecutionOutcome(
            status="failed",
            result_summary=(
                "Existing Smithery tools are missing one consistent server-side "
                "connection configuration and cannot be reported ready."
            ),
            result_ref=None,
            error_code="mcp_connection_configuration_missing",
            retryable=False,
        )

    namespace, connection_id = next(iter(coordinates))
    status = await get_smithery_connection_status(
        api_key,
        namespace,
        connection_id,
    )
    return _smithery_import_completion_outcome(
        display_name=display_name,
        server_id=server_id,
        imported_tools=[tool.display_name for tool in existing_tools],
        connection={
            "namespace": namespace,
            "connection_id": connection_id,
            **status,
        },
    )


async def import_mcp_from_smithery_outcome(
    server_id: str,
    agent_id: uuid.UUID,
    config: dict | None = None,
    reauthorize: bool = False,
) -> ToolExecutionOutcome:
    """Import an MCP server from Smithery into the platform.

    Uses the Smithery Registry detail API to get tool definitions,
    and stores the deploymentUrl for runtime execution via Smithery Connect.
    If config contains 'smithery_api_key', it is stored in encrypted tenant
    tool configuration for future use.
    """
    config = dict(config) if config else {}  # mutable copy

    # Extract smithery_api_key from config (user-provided) or fallback to stored
    api_key = config.pop("smithery_api_key", None) or await _get_smithery_api_key(agent_id)
    if not api_key:
        return ToolExecutionOutcome(
            status="failed",
            result_summary="Smithery credentials are required to import this MCP server.",
            result_ref=None,
            error_code="resource_credentials_missing",
        )

    # Persist the key only in encrypted tenant tool config. Dynamic Tool and
    # AgentTool rows keep non-secret connection coordinates, never credentials.
    try:
        async with async_session() as db:
            from app.models.agent import Agent as AgentModel

            tenant_r = await db.execute(
                select(AgentModel.tenant_id).where(AgentModel.id == agent_id)
            )
            tenant_id = tenant_r.scalar_one_or_none()
            if not tenant_id:
                raise RuntimeError("Agent tenant is required for Smithery config")
            for tool_name in ("discover_resources", "import_mcp_server"):
                r = await db.execute(select(Tool).where(Tool.name == tool_name))
                tool = r.scalar_one_or_none()
                if not tool:
                    continue
                current_config = await get_tenant_tool_config(
                    db,
                    tenant_id,
                    tool.name,
                    tool.config_schema,
                )
                await set_tenant_tool_config(
                    db,
                    tenant_id,
                    tool.name,
                    {**current_config, "smithery_api_key": api_key},
                    tool.config_schema,
                )
            await db.commit()
    except Exception:
        pass  # Non-critical for the current import; never fall back to Tool rows.

    # ---- Early exit: check if this server's tools are already installed for this agent ----
    # Check by both tool name prefix AND mcp_server_name to catch different server_id variants
    # (e.g., "github" vs "@anthropic/github" both produce server_name "GitHub")
    clean_id_check = server_id.replace("/", "_").replace("@", "")
    try:
        async with async_session() as db:
            from sqlalchemy import or_
            existing_server_r = await db.execute(
                select(Tool).where(
                    Tool.type == "mcp",
                    or_(
                        Tool.name.like(f"mcp_{clean_id_check}%"),
                        Tool.name.like(f"mcp_{clean_id_check.split('_')[-1]}%"),
                    ),
                )
            )
            existing_server_tools = existing_server_r.scalars().all()
            if existing_server_tools:
                # Check if this agent has assignments for these tools
                tool_ids = [t.id for t in existing_server_tools]
                agent_assignments_r = await db.execute(
                    select(AgentTool).where(
                        AgentTool.agent_id == agent_id,
                        AgentTool.tool_id.in_(tool_ids),
                    )
                )
                agent_assignments = agent_assignments_r.scalars().all()
                if len(agent_assignments) >= len(existing_server_tools):
                    if config:
                        for assignment in agent_assignments:
                            assignment.config = {
                                **(assignment.config or {}),
                                **config,
                            }
                        await db.commit()
                    existing_display_name = (
                        existing_server_tools[0].mcp_server_name or server_id
                    )
                    return await _existing_smithery_import_outcome(
                        display_name=existing_display_name,
                        server_id=server_id,
                        existing_tools=existing_server_tools,
                        assignments=agent_assignments,
                        api_key=api_key,
                    )
    except Exception:
        return ToolExecutionOutcome(
            status="failed",
            result_summary=(
                "Existing Smithery installation status could not be checked; "
                "no connection write was attempted."
            ),
            result_ref=None,
            error_code="mcp_existing_import_check_failed",
            retryable=False,
        )

    # Step 1: Search for server by ID
    headers = {"Accept": "application/json"}

    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            resp = await client.get(
                f"{SMITHERY_API_BASE}/servers",
                params={"q": server_id.lstrip("@"), "pageSize": 5},
                headers=headers,
            )
            if resp.status_code != 200:
                return ToolExecutionOutcome(
                    status="failed",
                    result_summary=(
                        f"Server '{server_id}' could not be loaded from Smithery "
                        f"(HTTP {resp.status_code})."
                    ),
                    result_ref=None,
                    error_code="mcp_server_lookup_rejected",
                )
            data = resp.json()
            servers = data.get("servers", [])
            server_info = None
            clean_id = server_id.lstrip("@")
            for s in servers:
                if s.get("qualifiedName") == clean_id or s.get("qualifiedName") == server_id:
                    server_info = s
                    break
            if not server_info and servers:
                server_info = servers[0]
            if not server_info:
                return ToolExecutionOutcome(
                    status="failed",
                    result_summary=f"Server '{server_id}' was not found on Smithery.",
                    result_ref=None,
                    error_code="mcp_server_not_found",
                )
    except Exception as e:
        return ToolExecutionOutcome(
            status="failed",
            result_summary=f"Server lookup failed: {type(e).__name__}.",
            result_ref=None,
            error_code="mcp_server_lookup_failed",
        )

    display_name = server_info.get("displayName", server_id.split("/")[-1])
    description = server_info.get("description", "")
    qualified_name = server_info.get("qualifiedName", server_id.lstrip("@"))

    # Check if server supports remote hosting
    if not server_info.get("remote"):
        return ToolExecutionOutcome(
            status="failed",
            result_summary=(
                f"{display_name} ({qualified_name}) does not support remote hosting "
                "and cannot be imported automatically."
            ),
            result_ref=None,
            error_code="mcp_server_not_remote",
        )

    # Step 2: Get full server details including tools from registry API
    tools_discovered = []
    deployment_url = None
    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            detail_resp = await client.get(
                f"{SMITHERY_API_BASE}/servers/{qualified_name}",
                headers=headers,
            )
            if detail_resp.status_code == 200:
                detail = detail_resp.json()
                deployment_url = detail.get("deploymentUrl")
                raw_tools = detail.get("tools", [])
                tools_discovered = [
                    {
                        "name": t.get("name", ""),
                        "description": t.get("description", ""),
                        "inputSchema": t.get("inputSchema", {}),
                    }
                    for t in raw_tools if t.get("name")
                ]
                logger.info(f"[ResourceDiscovery] Got {len(tools_discovered)} tools from registry for {qualified_name}")
            else:
                logger.warning(f"[ResourceDiscovery] Could not fetch detail for {qualified_name}: HTTP {detail_resp.status_code}")
    except Exception as e:
        logger.error(f"[ResourceDiscovery] Could not fetch server detail: {e}")

    # Step 3: Determine the MCP server URL for runtime execution
    base_mcp_url = deployment_url or f"https://{qualified_name}.run.tools"

    # Step 3.5: Auto-create Smithery Connect namespace + connection
    smithery_config = {}  # will be merged into every AgentTool.config
    conn_result = await _ensure_smithery_connection(api_key, base_mcp_url, display_name)
    if "error" in conn_result:
        if conn_result.get("unknown"):
            return ToolExecutionOutcome(
                status="unknown",
                result_summary=(
                    "Smithery connection creation outcome is unknown; "
                    "reconcile before retrying."
                ),
                result_ref=None,
                error_code="mcp_import_outcome_unknown",
            )
        return ToolExecutionOutcome(
            status="failed",
            result_summary="Smithery rejected connection creation.",
            result_ref=None,
            error_code="mcp_connection_rejected",
        )
    else:
        smithery_config = {
            "smithery_namespace": conn_result["namespace"],
            "smithery_connection_id": conn_result["connection_id"],
        }

    # Step 3.6: Override registry-advertised schema with the runtime server's
    # actual tools/list. Smithery's registry detail can drift behind the live
    # server (we hit this with shibui/finance: registry said `sql`, server
    # required `user_prompt` + `query`). The truth is whatever tools/list
    # returns at call time, so prefer it whenever available.
    connection_state = str(conn_result.get("state") or "").strip().lower()
    if smithery_config and connection_state != "auth_required" and not conn_result.get("auth_url"):
        ns_ = smithery_config["smithery_namespace"]
        conn_ = smithery_config["smithery_connection_id"]
        try:
            import json as _json
            async with httpx.AsyncClient(timeout=15) as client:
                live_resp = await client.post(
                    f"https://api.smithery.ai/connect/{ns_}/{conn_}/mcp",
                    json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                        "Accept": "application/json, text/event-stream",
                    },
                )
            if live_resp.status_code == 200:
                live_data = None
                # Smithery Connect returns SSE; parse the first data: line.
                for line in live_resp.text.split("\n"):
                    line = line.strip()
                    if line.startswith("data: "):
                        try:
                            live_data = _json.loads(line[6:])
                            break
                        except _json.JSONDecodeError:
                            pass
                if live_data is None:
                    try:
                        live_data = _json.loads(live_resp.text)
                    except _json.JSONDecodeError:
                        live_data = None
                live_tools = (live_data or {}).get("result", {}).get("tools", []) if live_data else []
                # MCP servers also return prompts here; only treat actual tools.
                live_tools_normalized = [
                    {
                        "name": t.get("name", ""),
                        "description": t.get("description", ""),
                        "inputSchema": t.get("inputSchema", {}),
                    }
                    for t in live_tools
                    if t.get("name") and isinstance(t.get("inputSchema"), dict)
                ]
                if live_tools_normalized:
                    logger.info(
                        f"[ResourceDiscovery] Using live tools/list for {qualified_name}: "
                        f"{len(live_tools_normalized)} tool(s) override registry's "
                        f"{len(tools_discovered)}"
                    )
                    tools_discovered = live_tools_normalized
        except Exception as e:
            logger.warning(
                f"[ResourceDiscovery] Live tools/list failed for {qualified_name}, "
                f"falling back to registry schema: {e}"
            )

    # Merge smithery_config + user config for AgentTool
    agent_tool_config = {**smithery_config, **config}

    async with async_session() as db:
        imported_tools = []

        # Helper: ensure AgentTool link exists and save config
        async def _ensure_agent_tool(tool_id: uuid.UUID):
            agent_check = await db.execute(
                select(AgentTool).where(
                    AgentTool.agent_id == agent_id,
                    AgentTool.tool_id == tool_id,
                )
            )
            at = agent_check.scalar_one_or_none()
            if at:
                at.config = {**(at.config or {}), **agent_tool_config}
            else:
                db.add(AgentTool(
                    agent_id=agent_id, tool_id=tool_id, enabled=True,
                    source="user_installed", installed_by_agent_id=agent_id,
                    config=agent_tool_config,
                ))

        # On re-import/reauthorize: update ALL existing tools for this server
        if config or reauthorize:
            existing_server_tools_r = await db.execute(
                select(Tool).where(Tool.mcp_server_name == display_name, Tool.type == "mcp")
            )
            for et in existing_server_tools_r.scalars().all():
                et.mcp_server_url = base_mcp_url
                await _ensure_agent_tool(et.id)

        if tools_discovered:
            # Clean up old generic entry if individual tools are now discovered
            generic_name = f"mcp_{server_id.replace('/', '_').replace('@', '')}"
            old_generic_r = await db.execute(select(Tool).where(Tool.name == generic_name))
            old_generic = old_generic_r.scalar_one_or_none()
            if old_generic:
                await db.execute(
                    AgentTool.__table__.delete().where(AgentTool.tool_id == old_generic.id)
                )
                await db.delete(old_generic)
                await db.flush()

            # Create one Tool record per MCP tool
            for mcp_tool in tools_discovered:
                tool_name = f"mcp_{server_id.replace('/', '_').replace('@', '')}_{mcp_tool['name']}"
                tool_display = f"{display_name}: {mcp_tool['name']}"

                existing_r = await db.execute(select(Tool).where(Tool.name == tool_name))
                existing_tool = existing_r.scalar_one_or_none()
                if existing_tool:
                    existing_tool.mcp_server_url = base_mcp_url
                    await _ensure_agent_tool(existing_tool.id)
                    if reauthorize:
                        imported_tools.append(f"🔄 {tool_display} (reauthorized)")
                    elif config:
                        imported_tools.append(f"🔄 {tool_display} (config updated)")
                    else:
                        imported_tools.append(f"⏭️ {tool_display} (already imported)")
                    continue

                tool = Tool(
                    name=tool_name,
                    display_name=tool_display,
                    description=mcp_tool.get("description", description)[:500],
                    type="mcp",
                    category="mcp",
                    icon="🔌",
                    parameters_schema=mcp_tool.get("inputSchema", {"type": "object", "properties": {}}),
                    mcp_server_url=base_mcp_url,
                    mcp_server_name=display_name,
                    mcp_tool_name=mcp_tool["name"],
                    enabled=True,
                    is_default=False,
                    source="agent",
                )
                db.add(tool)
                await db.flush()
                await _ensure_agent_tool(tool.id)
                imported_tools.append(f"✅ {tool_display}")
        else:
            # Fallback: create a single generic tool entry
            tool_name = f"mcp_{server_id.replace('/', '_').replace('@', '')}"
            tool_display = display_name

            existing_r = await db.execute(select(Tool).where(Tool.name == tool_name))
            existing_tool = existing_r.scalar_one_or_none()
            if existing_tool:
                existing_tool.mcp_server_url = base_mcp_url
                await _ensure_agent_tool(existing_tool.id)
                if config:
                    imported_tools.append(f"🔄 {tool_display} (config updated)")
                else:
                    imported_tools.append(f"⏭️ {tool_display} (already imported)")
            else:
                tool = Tool(
                    name=tool_name,
                    display_name=tool_display,
                    description=description[:500] or f"MCP Server: {server_id}",
                    type="mcp",
                    category="mcp",
                    icon="🔌",
                    parameters_schema={"type": "object", "properties": {}},
                    mcp_server_url=base_mcp_url,
                    mcp_server_name=display_name,
                    enabled=True,
                    is_default=False,
                    source="agent",
                )
                db.add(tool)
                await db.flush()
                await _ensure_agent_tool(tool.id)
                imported_tools.append(
                    f"✅ {tool_display} "
                    "(tool list not available from registry — may need configuration)"
                )

        await db.commit()

    return _smithery_import_completion_outcome(
        display_name=display_name,
        server_id=server_id,
        imported_tools=imported_tools,
        connection=conn_result,
    )


async def import_mcp_from_smithery(
    server_id: str,
    agent_id: uuid.UUID,
    config: dict | None = None,
    reauthorize: bool = False,
) -> str:
    """Legacy display adapter for typed Smithery import."""
    outcome = await import_mcp_from_smithery_outcome(
        server_id,
        agent_id,
        config,
        reauthorize,
    )
    return outcome.result_summary or "MCP import returned no summary."


# ── Direct URL Import ───────────────────────────────────────────

async def import_mcp_direct_outcome(
    mcp_url: str,
    agent_id: uuid.UUID,
    server_name: str | None = None,
    api_key: str | None = None,
) -> ToolExecutionOutcome:
    """Import an MCP server by directly connecting to its HTTP/SSE endpoint.

    This bypasses Smithery entirely — useful for self-hosted or third-party
    MCP servers that provide their own public endpoint.
    """
    from app.services.mcp_client import MCPClient

    # Build URL with apiKey if provided
    full_url = mcp_url
    if api_key and "?" in mcp_url:
        full_url = f"{mcp_url}&apiKey={api_key}"
    elif api_key:
        full_url = f"{mcp_url}?apiKey={api_key}"

    display_name = server_name or mcp_url.split("//")[-1].split("/")[0].split(":")[0]
    safe_name = display_name.replace(".", "_").replace("/", "_").replace(":", "_").replace("-", "_")

    # Try to list tools from the endpoint
    tools_discovered = []
    try:
        client = MCPClient(full_url)
        tools_discovered = await client.list_tools()
        logger.info(f"[DirectImport] Got {len(tools_discovered)} tools from {mcp_url}")
    except Exception as e:
        logger.error(f"[DirectImport] Could not list tools from {mcp_url}: {e}")

    # Config to store in AgentTool
    agent_tool_config = {}
    if api_key:
        agent_tool_config["api_key"] = api_key

    async with async_session() as db:
        imported_tools = []

        async def _ensure_agent_tool(tool_id: uuid.UUID):
            agent_check = await db.execute(
                select(AgentTool).where(
                    AgentTool.agent_id == agent_id,
                    AgentTool.tool_id == tool_id,
                )
            )
            at = agent_check.scalar_one_or_none()
            if at:
                at.config = {**(at.config or {}), **agent_tool_config}
            else:
                db.add(AgentTool(
                    agent_id=agent_id, tool_id=tool_id, enabled=True,
                    source="user_installed", installed_by_agent_id=agent_id,
                    config=agent_tool_config,
                ))

        if tools_discovered:
            for mcp_tool in tools_discovered:
                tool_name = f"mcp_{safe_name}_{mcp_tool['name']}"
                tool_display = f"{display_name}: {mcp_tool['name']}"

                existing_r = await db.execute(select(Tool).where(Tool.name == tool_name))
                existing_tool = existing_r.scalar_one_or_none()
                if existing_tool:
                    existing_tool.mcp_server_url = mcp_url
                    await _ensure_agent_tool(existing_tool.id)
                    imported_tools.append(f"⏭️ {tool_display} (already imported)")
                    continue

                tool = Tool(
                    name=tool_name,
                    display_name=tool_display,
                    description=mcp_tool.get("description", "")[:500],
                    type="mcp",
                    category="mcp",
                    icon="🔌",
                    parameters_schema=mcp_tool.get("inputSchema", {"type": "object", "properties": {}}),
                    mcp_server_url=mcp_url,
                    mcp_server_name=display_name,
                    mcp_tool_name=mcp_tool["name"],
                    enabled=True,
                    is_default=False,
                    source="agent",
                )
                db.add(tool)
                await db.flush()
                await _ensure_agent_tool(tool.id)
                imported_tools.append(f"✅ {tool_display}")
        else:
            tool_name = f"mcp_{safe_name}"
            existing_r = await db.execute(select(Tool).where(Tool.name == tool_name))
            existing_tool = existing_r.scalar_one_or_none()
            if existing_tool:
                existing_tool.mcp_server_url = mcp_url
                await _ensure_agent_tool(existing_tool.id)
                await db.commit()
                return ToolExecutionOutcome(
                    status="succeeded",
                    result_summary=f"{display_name} is already imported.",
                    result_ref=None,
                )

            tool = Tool(
                name=tool_name,
                display_name=display_name,
                description=f"MCP Server: {mcp_url}",
                type="mcp",
                category="mcp",
                icon="🔌",
                parameters_schema={"type": "object", "properties": {}},
                mcp_server_url=mcp_url,
                mcp_server_name=display_name,
                enabled=True,
                is_default=False,
                source="agent",
            )
            db.add(tool)
            await db.flush()
            await _ensure_agent_tool(tool.id)
            imported_tools.append(f"✅ {display_name} (tools couldn't be listed — server may need configuration)")

        await db.commit()

    result = f"Imported MCP server: **{display_name}**\n\n"
    result += "\n".join(imported_tools)
    result += "\n\nThe imported tools are now available for use."
    return ToolExecutionOutcome(
        status="succeeded",
        result_summary=result,
        result_ref=None,
    )


async def import_mcp_direct(
    mcp_url: str,
    agent_id: uuid.UUID,
    server_name: str | None = None,
    api_key: str | None = None,
) -> str:
    """Legacy display adapter for typed direct MCP import."""
    outcome = await import_mcp_direct_outcome(
        mcp_url,
        agent_id,
        server_name,
        api_key,
    )
    return outcome.result_summary or "MCP import returned no summary."


# ── Atlassian Rovo MCP Auto-Seeding ─────────────────────────────────────────

ATLASSIAN_ROVO_MCP_URL = "https://mcp.atlassian.com/v1/mcp"
ATLASSIAN_ROVO_SERVER_NAME = "Atlassian Rovo"
ATLASSIAN_ROVO_TOOL_PREFIX = "atlassian_rovo_"


async def seed_atlassian_rovo_tools(api_key: str) -> None:
    """Connect to Atlassian Rovo MCP and seed all available tools as platform-level MCP tools.

    Called on startup when an API key is configured. Existing tools are updated in-place;
    new tools discovered from the server are created. The api_key is stored in each tool's
    config so _execute_mcp_tool can authenticate requests.
    """
    from app.services.mcp_client import MCPClient

    logger.info(f"[AtlassianRovo] Connecting to {ATLASSIAN_ROVO_MCP_URL} ...")
    try:
        client = MCPClient(ATLASSIAN_ROVO_MCP_URL, api_key=api_key)
        tools_discovered = await client.list_tools()
    except Exception as e:
        logger.error(f"[AtlassianRovo] Could not list tools: {e}")
        return

    if not tools_discovered:
        logger.warning("[AtlassianRovo] No tools returned from server")
        return

    logger.info(f"[AtlassianRovo] Discovered {len(tools_discovered)} tools")

    async with async_session() as db:
        upserted = 0
        for mcp_tool in tools_discovered:
            raw_name = mcp_tool.get("name", "")
            if not raw_name:
                continue

            tool_name = f"{ATLASSIAN_ROVO_TOOL_PREFIX}{raw_name}"
            tool_display = f"Atlassian: {raw_name}"
            tool_desc = mcp_tool.get("description", "")[:500]
            tool_schema = mcp_tool.get("inputSchema", {"type": "object", "properties": {}})

            # Determine icon based on tool name hints
            if "jira" in raw_name.lower() or "issue" in raw_name.lower():
                icon = "🔵"
            elif "confluence" in raw_name.lower() or "page" in raw_name.lower():
                icon = "📘"
            elif "compass" in raw_name.lower() or "component" in raw_name.lower():
                icon = "🧭"
            else:
                icon = "🔷"

            existing_r = await db.execute(select(Tool).where(Tool.name == tool_name))
            existing_tool = existing_r.scalar_one_or_none()

            if existing_tool:
                # Update description and schema in case they changed
                existing_tool.description = tool_desc
                existing_tool.parameters_schema = tool_schema
                existing_tool.config = {"api_key": api_key}
            else:
                tool = Tool(
                    name=tool_name,
                    display_name=tool_display,
                    description=tool_desc,
                    type="mcp",
                    category="atlassian",
                    icon=icon,
                    parameters_schema=tool_schema,
                    mcp_server_url=ATLASSIAN_ROVO_MCP_URL,
                    mcp_server_name=ATLASSIAN_ROVO_SERVER_NAME,
                    mcp_tool_name=raw_name,
                    enabled=True,
                    is_default=False,
                    config={"api_key": api_key},
                    source="admin",
                )
                db.add(tool)
                upserted += 1

        await db.commit()

    logger.info(f"[AtlassianRovo] Seeded {upserted} new Atlassian Rovo tools")


async def refresh_atlassian_rovo_api_key(api_key: str) -> None:
    """Update the stored api_key in all Atlassian Rovo tool records.

    Called when the user updates the API key via the config UI.
    """
    async with async_session() as db:
        from sqlalchemy import update as _update
        await db.execute(
            _update(Tool)
            .where(Tool.mcp_server_name == ATLASSIAN_ROVO_SERVER_NAME, Tool.type == "mcp")
            .values(config={"api_key": api_key})
        )
        await db.commit()
    logger.info("[AtlassianRovo] API key refreshed for all Rovo tools")
