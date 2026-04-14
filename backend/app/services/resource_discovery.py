"""Resource discovery — search Smithery & ModelScope registries and import MCP servers."""

import uuid
import httpx
from loguru import logger
from sqlalchemy import select
from app.database import async_session
from app.models.tool import Tool, AgentTool


# ── Smithery Registry Search ────────────────────────────────────

SMITHERY_API_BASE = "https://registry.smithery.ai"
MODELSCOPE_API_BASE = "https://modelscope.cn"


async def _get_smithery_api_key(agent_id: uuid.UUID | None = None) -> str:
    """Read Smithery API key.

    Priority: 1) per-agent AgentTool config, 2) system-level tool config.
    """
    try:
        async with async_session() as db:
            # 1) Per-agent: check AgentTool configs for any MCP tool with a smithery_api_key
            if agent_id:
                at_r = await db.execute(
                    select(AgentTool).where(AgentTool.agent_id == agent_id)
                )
                for at in at_r.scalars().all():
                    if at.config and at.config.get("smithery_api_key"):
                        return at.config["smithery_api_key"]
            # 2) System-level fallback
            for tool_name in ("discover_resources", "import_mcp_server"):
                r = await db.execute(select(Tool).where(Tool.name == tool_name))
                tool = r.scalar_one_or_none()
                if tool and tool.config and tool.config.get("smithery_api_key"):
                    return tool.config["smithery_api_key"]
    except Exception:
        pass
    return ""


async def _search_smithery_api(query: str, max_results: int, api_key: str) -> list[dict]:
    """Search Smithery registry, returns normalized results."""
    headers = {"Accept": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            resp = await client.get(
                f"{SMITHERY_API_BASE}/servers",
                params={"q": query, "pageSize": max_results},
                headers=headers,
            )
            if resp.status_code != 200:
                return []
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
    except Exception:
        return []


async def _get_modelscope_api_token() -> str:
    """Read ModelScope API token from discover_resources tool config."""
    try:
        async with async_session() as db:
            for tool_name in ("discover_resources", "import_mcp_server"):
                r = await db.execute(select(Tool).where(Tool.name == tool_name))
                tool = r.scalar_one_or_none()
                if tool and tool.config and tool.config.get("modelscope_api_token"):
                    return tool.config["modelscope_api_token"]
    except Exception:
        pass
    return ""


async def _search_modelscope_api(query: str, max_results: int) -> list[dict]:
    """Search ModelScope MCP Hub via official OpenAPI (no WAF issues)."""
    api_token = await _get_modelscope_api_token()
    if not api_token:
        return []  # Silently skip if no token configured

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_token}",
        "Cookie": f"m_session_id={api_token}",
        "User-Agent": "modelscope-mcp-server/1.0",
    }
    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            resp = await client.put(
                f"{MODELSCOPE_API_BASE}/openapi/v1/mcp/servers",
                json={"page_size": max_results, "page_number": 1, "search": query, "filter": {}},
                headers=headers,
            )
            if resp.status_code != 200:
                return []
            data = resp.json()
            if not data.get("success"):
                return []

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
    except Exception as e:
        logger.error(f"[ResourceDiscovery] ModelScope search failed: {e}")
        return []


async def search_registries(query: str, max_results: int = 5) -> str:
    """Search both Smithery and ModelScope for MCP servers."""
    api_key = await _get_smithery_api_key()

    # Search both registries in parallel
    import asyncio
    smithery_task = _search_smithery_api(query, max_results, api_key)
    modelscope_task = _search_modelscope_api(query, max_results)
    smithery_results, modelscope_results = await asyncio.gather(smithery_task, modelscope_task)

    # Merge: Smithery first, then ModelScope (deduplicate by name)
    seen_names = set()
    all_results = []
    for r in smithery_results + modelscope_results:
        if r["name"] not in seen_names:
            seen_names.add(r["name"])
            all_results.append(r)

    if not all_results:
        return f'🔍 No MCP servers found for "{query}" on Smithery or ModelScope. Try different keywords.'

    results = []
    for i, srv in enumerate(all_results[:max_results], 1):
        verified = " ✅" if srv["verified"] else ""
        source_tag = f"[{srv['source']}]"
        if srv["remote"]:
            deploy_info = "🌐 Remote (no local install needed)"
        else:
            deploy_info = "💻 Local install required"
        use_info = f" · 👥 {srv['use_count']:,} users" if srv["use_count"] else ""
        hp = srv['homepage']

        results.append(
            f"**{i}. {srv['display_name']}**{verified} {source_tag}\n"
            f"   ID: `{srv['name']}`\n"
            f"   {srv['description']}\n"
            f"   {deploy_info}{use_info}\n"
            f"   {'🔗 ' + hp if hp else ''}"
        )

    header = f'🔍 Found {len(results)} MCP server(s) for "{query}":\n\n'
    footer = (
        "\n\n---\n"
        "💡 To import a remote server, use `import_mcp_server` with the server ID.\n"
        '   Example: import_mcp_server(server_id="gmail")'
    )
    return header + "\n\n".join(results) + footer


# Keep backward-compatible alias
async def search_smithery(query: str, max_results: int = 5) -> str:
    return await search_registries(query, max_results)


# ── Import MCP Server ───────────────────────────────────────────

async def _ensure_smithery_connection(api_key: str, mcp_url: str, display_name: str) -> dict:
    """Create or reuse a Smithery Connect namespace + connection.

    Returns dict with keys: namespace, connection_id, auth_url (if OAuth needed).
    """
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    try:
        async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
            # Get or create namespace
            ns_resp = await client.get("https://api.smithery.ai/namespaces", headers=headers)
            namespaces = ns_resp.json().get("namespaces", []) if ns_resp.status_code == 200 else []
            if namespaces:
                namespace = namespaces[0]["name"]
            else:
                create_ns = await client.post(
                    "https://api.smithery.ai/namespaces",
                    json={"name": "clawith"},
                    headers=headers,
                )
                if create_ns.status_code not in (200, 201):
                    return {"error": f"Failed to create namespace: HTTP {create_ns.status_code}"}
                namespace = create_ns.json()["name"]

            # Create connection
            conn_id = display_name.lower().replace(" ", "-").replace(":", "")
            conn_resp = await client.post(
                f"https://api.smithery.ai/connect/{namespace}",
                json={"connectionId": conn_id, "mcpUrl": mcp_url, "name": display_name},
                headers=headers,
            )
            if conn_resp.status_code not in (200, 201):
                return {"error": f"Failed to create connection: HTTP {conn_resp.status_code} — {conn_resp.text[:200]}"}

            conn_data = conn_resp.json()
            result = {
                "namespace": namespace,
                "connection_id": conn_data.get("connectionId", conn_id),
            }
            status = conn_data.get("status", {})
            if isinstance(status, dict) and status.get("state") == "auth_required":
                result["auth_url"] = status.get("authorizationUrl", "")
            return result
    except Exception as e:
        return {"error": str(e)[:200]}


async def import_mcp_from_smithery(
    server_id: str,
    agent_id: uuid.UUID,
    config: dict | None = None,
    reauthorize: bool = False,
) -> str:
    """Import an MCP server from Smithery into the platform.

    Uses the Smithery Registry detail API to get tool definitions,
    and stores the deploymentUrl for runtime execution via Smithery Connect.
    If config contains 'smithery_api_key', it's stored per-agent for future use.
    """
    config = dict(config) if config else {}  # mutable copy

    # Extract smithery_api_key from config (user-provided) or fallback to stored
    api_key = config.pop("smithery_api_key", None) or await _get_smithery_api_key(agent_id)
    if not api_key:
        return (
            "❌ Smithery API key is required to import MCP servers.\n\n"
            "请提供你的 Smithery API Key，你可以通过以下步骤获取：\n"
            "1. 注册/登录 https://smithery.ai\n"
            "2. 前往 https://smithery.ai/account/api-keys 创建 API Key\n"
            "3. 将 Key 提供给我，例如：\n"
            '   `import_mcp_server(server_id="github", config={"smithery_api_key": "your-key"})`'
        )

    # Write key back to discover_resources / import_mcp_server AgentTool configs
    # so it shows up in the Config dialog
    try:
        async with async_session() as db:
            for tool_name in ("discover_resources", "import_mcp_server"):
                r = await db.execute(select(Tool).where(Tool.name == tool_name))
                tool = r.scalar_one_or_none()
                if not tool:
                    continue
                at_r = await db.execute(
                    select(AgentTool).where(
                        AgentTool.agent_id == agent_id,
                        AgentTool.tool_id == tool.id,
                    )
                )
                at = at_r.scalar_one_or_none()
                if at:
                    at.config = {**(at.config or {}), "smithery_api_key": api_key}
                else:
                    db.add(AgentTool(
                        agent_id=agent_id, tool_id=tool.id, enabled=True,
                        source="system", config={"smithery_api_key": api_key},
                    ))
            await db.commit()
    except Exception:
        pass  # non-critical — key is still usable from MCP tool configs

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
            if existing_server_tools and not config and not reauthorize:
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
                    tool_names = [t.display_name for t in existing_server_tools[:5]]
                    more = f" ... and {len(existing_server_tools) - 5} more" if len(existing_server_tools) > 5 else ""
                    return (
                        f"⏭️ You already have **{len(existing_server_tools)}** tools from this MCP server installed:\n"
                        + "\n".join(f"  • {n}" for n in tool_names) + more
                        + "\n\nNo action needed. These tools are ready to use."
                        + "\n\n💡 If tools stopped working (e.g. OAuth expired), use `import_mcp_server(server_id=\"....\", reauthorize=true)` to re-authorize."
                    )
    except Exception:
        pass  # non-critical — proceed to normal import flow

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
                return f"❌ Server '{server_id}' not found on Smithery (HTTP {resp.status_code})"
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
                return f"❌ Server '{server_id}' not found on Smithery."
    except Exception as e:
        return f"❌ Failed to fetch server info: {str(e)[:200]}"

    display_name = server_info.get("displayName", server_id.split("/")[-1])
    description = server_info.get("description", "")
    qualified_name = server_info.get("qualifiedName", server_id.lstrip("@"))

    # Check if server supports remote hosting
    if not server_info.get("remote"):
        return (
            f"⚠️ **{display_name}** (`{qualified_name}`) does not support remote hosting via Smithery Connect.\n"
            f"This server requires local installation and cannot be imported automatically.\n"
            f"🔗 {server_info.get('homepage', '')}"
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
    auth_message = ""
    conn_result = await _ensure_smithery_connection(api_key, base_mcp_url, display_name)
    if "error" in conn_result:
        auth_message = f"\n\n⚠️ Could not auto-create Smithery connection: {conn_result['error']}"
    else:
        smithery_config = {
            "smithery_namespace": conn_result["namespace"],
            "smithery_connection_id": conn_result["connection_id"],
        }
        if conn_result.get("auth_url"):
            auth_message = (
                f"\n\n🔐 **OAuth 授权需要**: 请在浏览器中访问以下链接完成授权：\n"
                f"{conn_result['auth_url']}\n"
                f"授权完成后，工具即可使用。"
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
                    await db.commit()
                    return f"🔄 {tool_display} config updated. The tool is now ready to use."
                else:
                    return f"⏭️ {tool_display} is already imported."

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
            imported_tools.append(f"✅ {tool_display} (tool list not available from registry — may need configuration)")

        await db.commit()

    result = f"🔌 Imported MCP server: **{display_name}** (`{server_id}`)\n\n"
    result += "\n".join(imported_tools)
    result += f"\n\n📡 MCP Server URL: `{base_mcp_url}`"
    if auth_message:
        result += auth_message
    else:
        result += "\n\n💡 The imported tools are now available for use."
    return result


# ── Direct URL Import ───────────────────────────────────────────

async def import_mcp_direct(
    mcp_url: str,
    agent_id: uuid.UUID,
    server_name: str | None = None,
    api_key: str | None = None,
) -> str:
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
                return f"⏭️ {display_name} is already imported."

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

    result = f"🔌 Imported MCP server: **{display_name}**\n\n"
    result += "\n".join(imported_tools)
    result += f"\n\n📡 MCP Server URL: `{mcp_url}`"
    result += "\n\n💡 The imported tools are now available for use."
    return result


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

