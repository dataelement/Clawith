"""Agent tools — unified file-based tools that give digital employees
access to their own structured workspace.

Design principle:  ONE set of file tools covers EVERYTHING.
The agent's workspace uses well-known paths:
  - tasks.json          → task list (auto-synced from DB)
  - soul.md             → personality definition
  - memory.md           → long-term memory / notes
  - skills/             → skill definitions (markdown files)
  - workspace/          → general working files, reports, etc.

The agent reads/writes these files directly. No per-concept tools needed.
"""

import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select

from app.database import async_session
from app.models.task import Task
from app.config import get_settings

_settings = get_settings()
WORKSPACE_ROOT = Path(_settings.AGENT_DATA_DIR)

# ─── Tool Definitions (OpenAI function-calling format) ──────────

AGENT_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "list_files",
            "description": "List files and folders in a directory within my workspace. Can also list enterprise_info/ for shared company information.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Directory path to list, defaults to root (empty string). e.g.: '', 'skills', 'workspace', 'enterprise_info', 'enterprise_info/knowledge_base'",
                    }
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read file contents from the workspace. Can read tasks.json for tasks, soul.md for personality, memory/memory.md for memory, skills/ for skill files, and enterprise_info/ for shared company info.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "File path, e.g.: tasks.json, soul.md, memory/memory.md, skills/xxx.md, enterprise_info/company_profile.md",
                    }
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Write or update a file in the workspace. Can update memory/memory.md, create documents in workspace/, create skills in skills/. Note: tasks.json is read-only — use manage_tasks instead.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "File path, e.g.: memory/memory.md, workspace/report.md, skills/data_analysis.md",
                    },
                    "content": {
                        "type": "string",
                        "description": "File content to write",
                    },
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "delete_file",
            "description": "Delete a file from the workspace. Cannot delete soul.md or tasks.json.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "File path to delete",
                    }
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "manage_tasks",
            "description": "Manage tasks (create, update status, delete). For supervision tasks, specify type='supervision' with a target person name and remind schedule.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["create", "update_status", "delete"],
                        "description": "Action type",
                    },
                    "title": {
                        "type": "string",
                        "description": "Task title (required for create, used to match for update/delete)",
                    },
                    "description": {
                        "type": "string",
                        "description": "Task description (optional for create)",
                    },
                    "priority": {
                        "type": "string",
                        "enum": ["low", "medium", "high", "urgent"],
                        "description": "Priority level (optional, defaults to medium)",
                    },
                    "status": {
                        "type": "string",
                        "enum": ["pending", "doing", "done"],
                        "description": "New status (required for update_status)",
                    },
                    "task_type": {
                        "type": "string",
                        "enum": ["todo", "supervision"],
                        "description": "Task type: 'todo' for normal tasks (default), 'supervision' for follow-up/reminder tasks",
                    },
                    "supervision_target_name": {
                        "type": "string",
                        "description": "Target person name for supervision tasks (who to remind)",
                    },
                    "remind_schedule": {
                        "type": "string",
                        "enum": ["daily", "every_2_days", "every_3_days", "weekly"],
                        "description": "Reminder frequency for supervision tasks: 'daily', 'every_2_days', 'every_3_days', or 'weekly'",
                    },
                },
                "required": ["action", "title"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "send_feishu_message",
            "description": "Send a message to a human colleague via Feishu. Can only message people listed in the 'Human Colleagues' section of your relationships.md. To contact digital employees, use send_message_to_agent instead.",
            "parameters": {
                "type": "object",
                "properties": {
                    "member_name": {
                        "type": "string",
                        "description": "Recipient name, must be someone in your relationships",
                    },
                    "message": {
                        "type": "string",
                        "description": "Message content to send",
                    },
                },
                "required": ["member_name", "message"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "send_message_to_agent",
            "description": "Send a message to a digital employee colleague and receive a reply. The recipient is another AI agent, not a human. This triggers the recipient's LLM reasoning and returns their response. Suitable for asking questions, delegating tasks, or collaboration. Your relationships.md lists available digital employees under 'Digital Employee Colleagues'.",
            "parameters": {
                "type": "object",
                "properties": {
                    "agent_name": {
                        "type": "string",
                        "description": "Target digital employee's name",
                    },
                    "message": {
                        "type": "string",
                        "description": "Message content to send",
                    },
                    "msg_type": {
                        "type": "string",
                        "enum": ["notify", "consult", "task_delegate"],
                        "description": "Message type: notify (notification), consult (ask a question), task_delegate (delegate a task). Defaults to notify.",
                    },
                },
                "required": ["agent_name", "message"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "Search the internet for real-time information. Useful for finding news, technical docs, product info, and latest data. Returns search result titles, links, and snippets.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search keywords, e.g. 'Python asyncio tutorial' or 'AI industry trends 2026'",
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "Number of results to return, default 5, max 10",
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_document",
            "description": "Read office document contents (PDF, Word, Excel, PPT, etc.) and extract text. Suitable for reading knowledge base documents.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Document file path, e.g.: workspace/knowledge_base/report.pdf, enterprise_info/knowledge_base/policy.docx",
                    }
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "execute_code",
            "description": "Execute code (Python, Bash, or Node.js) in a sandboxed environment within the agent's workspace directory. Useful for data processing, calculations, file transformations, and automation scripts. Code runs with the workspace as the working directory. Security restrictions apply: no network access commands, no system-level operations, 30-second timeout.",
            "parameters": {
                "type": "object",
                "properties": {
                    "language": {
                        "type": "string",
                        "enum": ["python", "bash", "node"],
                        "description": "Programming language to execute",
                    },
                    "code": {
                        "type": "string",
                        "description": "Code to execute. For Python, you can import standard libraries (json, csv, math, re, collections, etc.). Working directory is your workspace/.",
                    },
                    "timeout": {
                        "type": "integer",
                        "description": "Max execution time in seconds (default 30, max 60)",
                    },
                },
                "required": ["language", "code"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "discover_resources",
            "description": "Search public MCP registries (Smithery) for tools and capabilities that can extend your abilities. Use this when you encounter a task you cannot handle with your current tools.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Semantic description of the capability needed, e.g. 'send email', 'query SQL database', 'generate images'",
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "Max results to return (default 5, max 10)",
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "import_mcp_server",
            "description": "Import an MCP server from Smithery registry into the platform. The server's tools become available for use. Use discover_resources first to find the server ID.",
            "parameters": {
                "type": "object",
                "properties": {
                    "server_id": {
                        "type": "string",
                        "description": "Smithery server ID, e.g. '@anthropic/brave-search' or '@anthropic/fetch'",
                    },
                    "config": {
                        "type": "object",
                        "description": "Optional server configuration (e.g. API keys required by the server)",
                    },
                },
                "required": ["server_id"],
            },
        },
    },
]


# ─── Dynamic Tool Loading from DB ──────────────────────────────

async def get_agent_tools_for_llm(agent_id: uuid.UUID) -> list[dict]:
    """Load enabled tools for an agent from DB (OpenAI function-calling format).
    
    Falls back to hardcoded AGENT_TOOLS if DB not ready.
    """
    try:
        from app.models.tool import Tool, AgentTool

        async with async_session() as db:
            # Get all globally enabled tools
            all_tools_r = await db.execute(select(Tool).where(Tool.enabled == True))
            all_tools = all_tools_r.scalars().all()

            # Get agent-specific assignments
            agent_tools_r = await db.execute(select(AgentTool).where(AgentTool.agent_id == agent_id))
            assignments = {str(at.tool_id): at for at in agent_tools_r.scalars().all()}

            result = []
            for t in all_tools:
                tid = str(t.id)
                at = assignments.get(tid)
                enabled = at.enabled if at else t.is_default
                if not enabled:
                    continue

                # Build OpenAI function-calling format
                tool_def = {
                    "type": "function",
                    "function": {
                        "name": t.name,
                        "description": t.description,
                        "parameters": t.parameters_schema or {"type": "object", "properties": {}},
                    },
                }
                result.append(tool_def)

            if result:
                return result
    except Exception as e:
        print(f"[Tools] DB load failed, using fallback: {e}")

    # Fallback to hardcoded tools
    return AGENT_TOOLS


# ─── Workspace initialization ──────────────────────────────────

async def ensure_workspace(agent_id: uuid.UUID) -> Path:
    """Initialize agent workspace with standard structure."""
    ws = WORKSPACE_ROOT / str(agent_id)
    ws.mkdir(parents=True, exist_ok=True)

    # Create standard directories
    (ws / "skills").mkdir(exist_ok=True)
    (ws / "workspace").mkdir(exist_ok=True)
    (ws / "workspace" / "knowledge_base").mkdir(exist_ok=True)
    (ws / "memory").mkdir(exist_ok=True)

    # Ensure shared enterprise_info directory exists
    enterprise_dir = WORKSPACE_ROOT / "enterprise_info"
    enterprise_dir.mkdir(parents=True, exist_ok=True)
    (enterprise_dir / "knowledge_base").mkdir(exist_ok=True)
    # Create default company profile if missing
    profile_path = enterprise_dir / "company_profile.md"
    if not profile_path.exists():
        profile_path.write_text("# Company Profile\n\n_Edit company information here. All digital employees can access this._\n\n## Basic Info\n- Company Name:\n- Industry:\n- Founded:\n\n## Business Overview\n\n## Organization Structure\n\n## Company Culture\n", encoding="utf-8")

    # Migrate: move root-level memory.md into memory/ directory
    if (ws / "memory.md").exists() and not (ws / "memory" / "memory.md").exists():
        import shutil
        shutil.move(str(ws / "memory.md"), str(ws / "memory" / "memory.md"))

    # Create default memory file if missing
    if not (ws / "memory" / "memory.md").exists():
        (ws / "memory" / "memory.md").write_text("# Memory\n\n_Record important information and knowledge here._\n", encoding="utf-8")

    if not (ws / "soul.md").exists():
        # Try to load from DB
        try:
            from app.models.agent import Agent
            async with async_session() as db:
                r = await db.execute(select(Agent).where(Agent.id == agent_id))
                agent = r.scalar_one_or_none()
                if agent and agent.role_description:
                    (ws / "soul.md").write_text(
                        f"# Personality\n\n{agent.role_description}\n",
                        encoding="utf-8",
                    )
                else:
                    (ws / "soul.md").write_text("# Personality\n\n_Describe your role and responsibilities._\n", encoding="utf-8")
        except Exception:
            (ws / "soul.md").write_text("# Personality\n\n_Describe your role and responsibilities._\n", encoding="utf-8")

    # Always sync tasks from DB
    await _sync_tasks_to_file(agent_id, ws)

    return ws


async def _sync_tasks_to_file(agent_id: uuid.UUID, ws: Path):
    """Sync tasks from DB to tasks.json in workspace."""
    try:
        async with async_session() as db:
            result = await db.execute(
                select(Task).where(Task.agent_id == agent_id).order_by(Task.created_at.desc())
            )
            tasks = result.scalars().all()

        task_list = []
        for t in tasks:
            task_list.append({
                "title": t.title,
                "status": t.status,
                "priority": t.priority,
                "description": t.description or "",
                "created_at": t.created_at.isoformat() if t.created_at else "",
                "completed_at": t.completed_at.isoformat() if t.completed_at else "",
            })

        (ws / "tasks.json").write_text(
            json.dumps(task_list, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception as e:
        print(f"[AgentTools] Failed to sync tasks: {e}")


# ─── Tool Executors ─────────────────────────────────────────────

# Mapping from tool_name to autonomy action_type
_TOOL_AUTONOMY_MAP = {
    "write_file": "write_workspace_files",
    "delete_file": "delete_files",
    "send_feishu_message": "send_feishu_message",
    "send_message_to_agent": "send_feishu_message",
    "web_search": "web_search",
    "execute_code": "execute_code",
}


async def execute_tool(
    tool_name: str,
    arguments: dict,
    agent_id: uuid.UUID,
    user_id: uuid.UUID,
) -> str:
    """Execute a tool call and return the result as a string."""
    ws = await ensure_workspace(agent_id)

    # ── Autonomy boundary check ──
    action_type = _TOOL_AUTONOMY_MAP.get(tool_name)
    if action_type:
        try:
            from app.services.autonomy_service import autonomy_service
            from app.models.agent import Agent as AgentModel
            async with async_session() as _adb:
                _ar = await _adb.execute(select(AgentModel).where(AgentModel.id == agent_id))
                _agent = _ar.scalar_one_or_none()
                if _agent:
                    result_check = await autonomy_service.check_and_enforce(
                        _adb, _agent, action_type, {"tool": tool_name, "args": str(arguments)[:200]}
                    )
                    await _adb.commit()
                    if not result_check.get("allowed"):
                        level = result_check.get("level", "L3")
                        if level == "L3":
                            return f"⏳ This action requires approval. An approval request has been sent. Please wait for approval before retrying. (Approval ID: {result_check.get('approval_id', 'N/A')})"
                        return f"❌ Action denied: {result_check.get('message', 'unknown reason')}"
        except Exception as e:
            print(f"[Autonomy] Check failed — blocking as safety measure: {e}")
            return f"⚠️ Autonomy check failed ({e}). Operation blocked for safety. Please retry or contact admin."

    try:
        if tool_name == "list_files":
            result = _list_files(ws, arguments.get("path", ""))
        elif tool_name == "read_file":
            path = arguments.get("path")
            if not path:
                return "❌ Missing required argument 'path' for read_file"
            result = _read_file(ws, path)
        elif tool_name == "read_document":
            path = arguments.get("path")
            if not path:
                return "❌ Missing required argument 'path' for read_document"
            result = await _read_document(ws, path)
        elif tool_name == "write_file":
            path = arguments.get("path")
            content = arguments.get("content")
            if not path:
                return "❌ Missing required argument 'path' for write_file. Please provide a file path like 'skills/my-skill/SKILL.md'"
            if content is None:
                return "❌ Missing required argument 'content' for write_file"
            result = _write_file(ws, path, content)
        elif tool_name == "delete_file":
            result = _delete_file(ws, arguments.get("path", ""))
        elif tool_name == "manage_tasks":
            result = await _manage_tasks(agent_id, user_id, ws, arguments)
        elif tool_name == "send_feishu_message":
            result = await _send_feishu_message(agent_id, arguments)
        elif tool_name == "send_message_to_agent":
            result = await _send_message_to_agent(agent_id, arguments)
        elif tool_name == "web_search":
            result = await _web_search(arguments)
        elif tool_name == "plaza_get_new_posts":
            result = await _plaza_get_new_posts(arguments)
        elif tool_name == "plaza_create_post":
            result = await _plaza_create_post(agent_id, arguments)
        elif tool_name == "plaza_add_comment":
            result = await _plaza_add_comment(agent_id, arguments)
        elif tool_name == "execute_code":
            result = await _execute_code(ws, arguments)
        elif tool_name == "discover_resources":
            result = await _discover_resources(arguments)
        elif tool_name == "import_mcp_server":
            result = await _import_mcp_server(agent_id, arguments)
        else:
            # Try MCP tool execution
            result = await _execute_mcp_tool(tool_name, arguments)

        # Log tool call activity (skip noisy read operations)
        if tool_name not in ("list_files", "read_file", "read_document"):
            from app.services.activity_logger import log_activity
            await log_activity(
                agent_id, "tool_call",
                f"Called tool {tool_name}: {result[:80]}",
                detail={"tool": tool_name, "args": {k: str(v)[:100] for k, v in arguments.items()}, "result": result[:300]},
            )
        return result
    except Exception as e:
        import traceback
        traceback.print_exc()
        return f"Tool execution error ({tool_name}): {type(e).__name__}: {str(e)[:200]}"


async def _web_search(arguments: dict) -> str:
    """Search the web using a configurable search engine (reads config from DB)."""
    import httpx
    import re

    query = arguments.get("query", "")
    if not query:
        return "❌ Please provide search keywords"

    # Load config from DB
    config = {}
    try:
        from app.models.tool import Tool
        async with async_session() as db:
            r = await db.execute(select(Tool).where(Tool.name == "web_search"))
            tool = r.scalar_one_or_none()
            if tool and tool.config:
                config = tool.config
    except Exception:
        pass

    engine = config.get("search_engine", "duckduckgo")
    api_key = config.get("api_key", "")
    max_results = min(arguments.get("max_results", config.get("max_results", 5)), 10)
    language = config.get("language", "zh-CN")

    try:
        if engine == "tavily" and api_key:
            return await _search_tavily(query, api_key, max_results)
        elif engine == "google" and api_key:
            return await _search_google(query, api_key, max_results, language)
        elif engine == "bing" and api_key:
            return await _search_bing(query, api_key, max_results, language)
        else:
            return await _search_duckduckgo(query, max_results)
    except Exception as e:
        return f"❌ Search error ({engine}): {str(e)[:200]}"


async def _search_duckduckgo(query: str, max_results: int) -> str:
    """Search via DuckDuckGo HTML (free, no API key)."""
    import httpx, re

    async with httpx.AsyncClient(follow_redirects=True) as client:
        resp = await client.get(
            "https://html.duckduckgo.com/html/",
            params={"q": query},
            headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"},
            timeout=10,
        )

    results = []
    blocks = re.findall(
        r'<a[^>]*class="result__a"[^>]*href="([^"]*)"[^>]*>(.*?)</a>.*?'
        r'<a[^>]*class="result__snippet"[^>]*>(.*?)</a>',
        resp.text, re.DOTALL,
    )
    for url, title, snippet in blocks[:max_results]:
        title = re.sub(r'<[^>]+>', '', title).strip()
        snippet = re.sub(r'<[^>]+>', '', snippet).strip()
        if "uddg=" in url:
            from urllib.parse import unquote, parse_qs, urlparse
            parsed = parse_qs(urlparse(url).query)
            url = unquote(parsed.get("uddg", [url])[0])
        results.append(f"**{title}**\n{url}\n{snippet}")

    if not results:
        return f'🔍 No results found for "{query}"'
    return f'🔍 Search results for "{query}" ({len(results)} items):\n\n' + "\n\n---\n\n".join(results)


async def _search_tavily(query: str, api_key: str, max_results: int) -> str:
    """Search via Tavily API (AI-optimized search)."""
    import httpx

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            "https://api.tavily.com/search",
            json={"query": query, "max_results": max_results, "search_depth": "basic"},
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            timeout=15,
        )
        data = resp.json()

    if "results" not in data:
        return f"❌ Tavily search failed: {data.get('error', str(data)[:200])}"

    results = []
    for r in data["results"][:max_results]:
        results.append(f"**{r.get('title', '')}**\n{r.get('url', '')}\n{r.get('content', '')[:200]}")

    if not results:
        return f'🔍 No results found for "{query}"'
    return f'🔍 Tavily search for "{query}" ({len(results)} items):\n\n' + "\n\n---\n\n".join(results)


async def _search_google(query: str, api_key: str, max_results: int, language: str) -> str:
    """Search via Google Custom Search JSON API."""
    import httpx

    # api_key format: "API_KEY:CX_ID"
    parts = api_key.split(":", 1)
    if len(parts) != 2:
        return "❌ Google search requires API key in format 'API_KEY:SEARCH_ENGINE_ID'"

    gapi_key, cx = parts
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            "https://www.googleapis.com/customsearch/v1",
            params={"key": gapi_key, "cx": cx, "q": query, "num": max_results, "lr": f"lang_{language[:2]}"},
            timeout=10,
        )
        data = resp.json()

    results = []
    for item in data.get("items", [])[:max_results]:
        results.append(f"**{item.get('title', '')}**\n{item.get('link', '')}\n{item.get('snippet', '')}")

    if not results:
        return f'🔍 No results found for "{query}"'
    return f'🔍 Google search for "{query}" ({len(results)} items):\n\n' + "\n\n---\n\n".join(results)


async def _search_bing(query: str, api_key: str, max_results: int, language: str) -> str:
    """Search via Bing Web Search API."""
    import httpx

    async with httpx.AsyncClient() as client:
        resp = await client.get(
            "https://api.bing.microsoft.com/v7.0/search",
            params={"q": query, "count": max_results, "mkt": language},
            headers={"Ocp-Apim-Subscription-Key": api_key},
            timeout=10,
        )
        data = resp.json()

    results = []
    for item in data.get("webPages", {}).get("value", [])[:max_results]:
        results.append(f"**{item.get('name', '')}**\n{item.get('url', '')}\n{item.get('snippet', '')}")

    if not results:
        return f'🔍 No results found for "{query}"'
    return f'🔍 Bing search for "{query}" ({len(results)} items):\n\n' + "\n\n---\n\n".join(results)


async def _execute_mcp_tool(tool_name: str, arguments: dict) -> str:
    """Execute a tool via MCP if it exists in the DB as an MCP tool."""
    try:
        from app.models.tool import Tool
        from app.services.mcp_client import MCPClient

        async with async_session() as db:
            result = await db.execute(select(Tool).where(Tool.name == tool_name, Tool.type == "mcp"))
            tool = result.scalar_one_or_none()

        if not tool:
            return f"Unknown tool: {tool_name}"

        if not tool.mcp_server_url:
            return f"❌ MCP tool {tool_name} has no server URL configured"

        client = MCPClient(tool.mcp_server_url)
        mcp_name = tool.mcp_tool_name or tool_name
        return await client.call_tool(mcp_name, arguments)

    except Exception as e:
        return f"❌ MCP tool execution error: {str(e)[:200]}"


def _list_files(ws: Path, rel_path: str) -> str:
    # Handle enterprise_info/ as shared directory
    if rel_path and rel_path.startswith("enterprise_info"):
        target = (WORKSPACE_ROOT / rel_path).resolve()
        enterprise_root = (WORKSPACE_ROOT / "enterprise_info").resolve()
        if not str(target).startswith(str(enterprise_root)):
            return "Access denied for this path"
    else:
        target = (ws / rel_path) if rel_path else ws
        target = target.resolve()
        if not str(target).startswith(str(ws.resolve())):
            return "Access denied for this path"

    if not target.exists():
        return f"Directory not found: {rel_path or '/'}"

    items = []
    # If listing root, also show enterprise_info entry
    if not rel_path:
        enterprise_dir = WORKSPACE_ROOT / "enterprise_info"
        if enterprise_dir.exists():
            items.append("  📁 enterprise_info/ (shared company info)")

    dir_count = 0
    file_count = 0
    for p in sorted(target.iterdir()):
        if p.name.startswith("."):
            continue
        if p.is_dir():
            dir_count += 1
            child_count = len([c for c in p.iterdir() if not c.name.startswith(".")])
            items.append(f"  📁 {p.name}/ ({child_count} items)")
        elif p.is_file():
            file_count += 1
            size_bytes = p.stat().st_size
            if size_bytes < 1024:
                size_str = f"{size_bytes}B"
            else:
                size_str = f"{size_bytes/1024:.1f}KB"
            items.append(f"  📄 {p.name} ({size_str})")

    if not items:
        return f"📂 {rel_path or 'root'}: Empty directory (0 files, 0 folders)"

    header = f"📂 {rel_path or 'root'}: {dir_count} folder(s), {file_count} file(s)\n"
    return header + "\n".join(items)


def _read_file(ws: Path, rel_path: str) -> str:
    # Handle enterprise_info/ as shared directory
    if rel_path and rel_path.startswith("enterprise_info"):
        file_path = (WORKSPACE_ROOT / rel_path).resolve()
        enterprise_root = (WORKSPACE_ROOT / "enterprise_info").resolve()
        if not str(file_path).startswith(str(enterprise_root)):
            return "Access denied for this path"
    else:
        file_path = (ws / rel_path).resolve()
        if not str(file_path).startswith(str(ws.resolve())):
            return "Access denied for this path"

    if not file_path.exists():
        return f"File not found: {rel_path}"

    try:
        content = file_path.read_text(encoding="utf-8", errors="replace")
        if len(content) > 6000:
            content = content[:6000] + f"\n\n...[truncated, {len(content)} chars total]"
        return content
    except Exception as e:
        return f"Read failed: {e}"


async def _read_document(ws: Path, rel_path: str) -> str:
    """Read content from office documents (PDF, DOCX, XLSX, PPTX)."""
    # Handle enterprise_info/ as shared directory
    if rel_path and rel_path.startswith("enterprise_info"):
        file_path = (WORKSPACE_ROOT / rel_path).resolve()
        enterprise_root = (WORKSPACE_ROOT / "enterprise_info").resolve()
        if not str(file_path).startswith(str(enterprise_root)):
            return "Access denied for this path"
    else:
        file_path = (ws / rel_path).resolve()
        if not str(file_path).startswith(str(ws.resolve())):
            return "Access denied for this path"

    if not file_path.exists():
        return f"File not found: {rel_path}"

    ext = file_path.suffix.lower()
    try:
        if ext == ".pdf":
            import pdfplumber
            text_parts = []
            with pdfplumber.open(str(file_path)) as pdf:
                for i, page in enumerate(pdf.pages[:50]):  # Limit to 50 pages
                    page_text = page.extract_text() or ""
                    if page_text:
                        text_parts.append(f"--- Page {i+1} ---\n{page_text}")
            content = "\n\n".join(text_parts) if text_parts else "(PDF is empty or text extraction failed)"

        elif ext == ".docx":
            from docx import Document
            doc = Document(str(file_path))
            paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
            content = "\n".join(paragraphs) if paragraphs else "(Document is empty)"

        elif ext == ".xlsx":
            from openpyxl import load_workbook
            wb = load_workbook(str(file_path), read_only=True, data_only=True)
            sheets = []
            for ws_name in wb.sheetnames[:10]:  # Limit to 10 sheets
                sheet = wb[ws_name]
                rows = []
                for row in sheet.iter_rows(max_row=200, values_only=True):
                    row_str = "\t".join(str(c) if c is not None else "" for c in row)
                    if row_str.strip():
                        rows.append(row_str)
                if rows:
                    sheets.append(f"=== Sheet: {ws_name} ===\n" + "\n".join(rows))
            wb.close()
            content = "\n\n".join(sheets) if sheets else "(Excel is empty)"

        elif ext == ".pptx":
            from pptx import Presentation
            prs = Presentation(str(file_path))
            slides = []
            for i, slide in enumerate(prs.slides[:50]):
                texts = []
                for shape in slide.shapes:
                    if hasattr(shape, "text") and shape.text.strip():
                        texts.append(shape.text)
                if texts:
                    slides.append(f"--- Slide {i+1} ---\n" + "\n".join(texts))
            content = "\n\n".join(slides) if slides else "(PPT is empty)"

        elif ext in (".txt", ".md", ".json", ".csv", ".log"):
            content = file_path.read_text(encoding="utf-8", errors="replace")

        else:
            return f"Unsupported file format: {ext}. Supported: PDF, DOCX, XLSX, PPTX, TXT, MD, CSV"

        if len(content) > 8000:
            content = content[:8000] + f"\n\n...[truncated, {len(content)} chars total]"
        return content

    except ImportError as e:
        return f"Missing dependency: {e}. Install: pip install pdfplumber python-docx openpyxl python-pptx"
    except Exception as e:
        return f"Document read failed: {str(e)[:200]}"


def _write_file(ws: Path, rel_path: str, content: str) -> str:
    # Protect tasks.json from direct writes
    if rel_path.strip("/") == "tasks.json":
        return "tasks.json is read-only. Use manage_tasks tool to manage tasks."

    file_path = (ws / rel_path).resolve()
    if not str(file_path).startswith(str(ws.resolve())):
        return "Access denied for this path"

    try:
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(content, encoding="utf-8")
        return f"✅ Written to {rel_path} ({len(content)} chars)"
    except Exception as e:
        return f"Write failed: {e}"


def _delete_file(ws: Path, rel_path: str) -> str:
    protected = {"tasks.json", "soul.md"}
    if rel_path.strip("/") in protected:
        return f"{rel_path} cannot be deleted (protected)"

    file_path = (ws / rel_path).resolve()
    if not str(file_path).startswith(str(ws.resolve())):
        return "Access denied for this path"
    if not file_path.exists():
        return f"File not found: {rel_path}"

    try:
        if file_path.is_dir():
            import shutil
            shutil.rmtree(file_path)
            return f"✅ Deleted directory {rel_path}"
        else:
            file_path.unlink()
            return f"✅ Deleted {rel_path}"
    except Exception as e:
        return f"Delete failed: {e}"


async def _manage_tasks(
    agent_id: uuid.UUID,
    user_id: uuid.UUID,
    ws: Path,
    args: dict,
) -> str:
    """Create / update / delete tasks in DB and sync to workspace."""
    from app.models.task import TaskLog
    from datetime import datetime, timezone

    action = args["action"]
    title = args["title"]

    async with async_session() as db:
        if action == "create":
            task_type = args.get("task_type", "todo")
            task = Task(
                agent_id=agent_id,
                title=title,
                description=args.get("description"),
                type=task_type,
                priority=args.get("priority", "medium"),
                created_by=user_id,
                status="pending",
                supervision_target_name=args.get("supervision_target_name"),
                supervision_channel=args.get("supervision_channel", "feishu"),
                remind_schedule=args.get("remind_schedule"),
            )
            db.add(task)
            await db.commit()
            await db.refresh(task)

            if task_type == "todo":
                # Trigger auto-execution for todo tasks
                import asyncio
                from app.services.task_executor import execute_task
                asyncio.create_task(execute_task(task.id, agent_id))
                await _sync_tasks_to_file(agent_id, ws)
                return f"✅ Task created: {title} — auto-execution started"
            else:
                # Supervision task — reminder engine will pick it up
                target = args.get('supervision_target_name', 'someone')
                schedule = args.get('remind_schedule', 'not set')
                await _sync_tasks_to_file(agent_id, ws)
                return f"✅ Supervision task created: '{title}' — will remind {target} on schedule ({schedule})"

        elif action == "update_status":
            result = await db.execute(
                select(Task).where(Task.agent_id == agent_id, Task.title.ilike(f"%{title}%"))
            )
            task = result.scalars().first()
            if not task:
                return f"No task found matching '{title}'"
            old = task.status
            task.status = args["status"]
            if args["status"] == "done":
                task.completed_at = datetime.now(timezone.utc)
            await db.commit()
            await _sync_tasks_to_file(agent_id, ws)
            return f"✅ Updated '{task.title}' from {old} to {args['status']}"

        elif action == "delete":
            from sqlalchemy import delete as sa_delete
            result = await db.execute(
                select(Task).where(Task.agent_id == agent_id, Task.title.ilike(f"%{title}%"))
            )
            task = result.scalars().first()
            if not task:
                return f"No task found matching '{title}'"
            task_title = task.title
            await db.execute(sa_delete(TaskLog).where(TaskLog.task_id == task.id))
            await db.delete(task)
            await db.commit()
            await _sync_tasks_to_file(agent_id, ws)
            return f"✅ Task deleted: {task_title}"

        return f"Unknown action: {action}"


async def _send_feishu_message(agent_id: uuid.UUID, args: dict) -> str:
    """Send a Feishu message to a person in the agent's relationship list."""
    member_name = args.get("member_name", "").strip()
    message_text = args.get("message", "").strip()

    if not member_name or not message_text:
        return "❌ Please provide recipient name and message content"

    try:
        from app.models.org import AgentRelationship, OrgMember
        from app.models.channel_config import ChannelConfig
        from app.services.feishu_service import feishu_service
        from sqlalchemy.orm import selectinload

        async with async_session() as db:
            # Find the relationship member by name
            result = await db.execute(
                select(AgentRelationship)
                .where(AgentRelationship.agent_id == agent_id)
                .options(selectinload(AgentRelationship.member))
            )
            rels = result.scalars().all()

            target_member = None
            for r in rels:
                if r.member and r.member.name == member_name:
                    target_member = r.member
                    break

            if not target_member:
                names = [r.member.name for r in rels if r.member]
                return f"❌ No contact named '{member_name}' found. Your contacts: {', '.join(names) if names else 'none'}"

            if not target_member.feishu_open_id and not target_member.email and not target_member.phone:
                return f"❌ {member_name} has no linked Feishu account (no open_id, email, or phone)"

            # Get the agent's Feishu bot credentials
            config_result = await db.execute(
                select(ChannelConfig).where(ChannelConfig.agent_id == agent_id)
            )
            config = config_result.scalar_one_or_none()
            if not config:
                return "❌ This agent has no Feishu channel configured"

            import json as _json
            from app.models.system_settings import SystemSetting

            content = _json.dumps({"text": message_text}, ensure_ascii=False)

            async def _try_send(app_id: str, app_secret: str, open_id: str) -> dict:
                return await feishu_service.send_message(
                    app_id, app_secret,
                    receive_id=open_id, msg_type="text",
                    content=content, receive_id_type="open_id",
                )

            # Step 1: Try resolve open_id for this agent's app (needs contact:user.id:readonly)
            # If successful, update stored open_id so future sends work directly
            if target_member.email or target_member.phone:
                try:
                    resolved = await feishu_service.resolve_open_id(
                        config.app_id, config.app_secret,
                        email=target_member.email,
                        mobile=target_member.phone,
                    )
                    if resolved:
                        resp = await _try_send(config.app_id, config.app_secret, resolved)
                        if resp.get("code") == 0:
                            # Update stored open_id for this app so future sends skip resolve
                            target_member.feishu_open_id = resolved
                            await db.commit()
                            return f"✅ Successfully sent message to {member_name}"
                except Exception:
                    pass

            # Step 2: Use stored open_id with agent's app (works if from same app / OAuth)
            if target_member.feishu_open_id:
                resp = await _try_send(config.app_id, config.app_secret, target_member.feishu_open_id)
                if resp.get("code") == 0:
                    return f"✅ Successfully sent message to {member_name}"

                # Step 3: If cross-app error, try org sync app as fallback
                err_msg = resp.get("msg", "")
                if "cross" in err_msg.lower():
                    org_r = await db.execute(select(SystemSetting).where(SystemSetting.key == "feishu_org_sync"))
                    org_setting = org_r.scalar_one_or_none()
                    if org_setting and org_setting.value.get("app_id"):
                        resp2 = await _try_send(
                            org_setting.value["app_id"], org_setting.value["app_secret"],
                            target_member.feishu_open_id,
                        )
                        if resp2.get("code") == 0:
                            return f"✅ Successfully sent message to {member_name}"
                        return f"❌ Send failed: {resp2.get('msg', str(resp2))}"

                return f"❌ Send failed: {err_msg}"

            return f"❌ {member_name} has no Feishu open_id and cannot be resolved via email/phone"
    except Exception as e:
        return f"❌ Message send error: {str(e)[:200]}"


async def _send_message_to_agent(from_agent_id: uuid.UUID, args: dict) -> str:
    """Send a message to another digital employee and have a multi-turn conversation."""
    agent_name = args.get("agent_name", "").strip()
    message_text = args.get("message", "").strip()

    if not agent_name or not message_text:
        return "❌ Please provide target agent name and message content"

    try:
        from app.models.agent import Agent
        from app.models.message import Message
        from app.models.system_settings import SystemSetting

        async with async_session() as db:
            # Read max_rounds from system settings
            setting_r = await db.execute(select(SystemSetting).where(SystemSetting.key == "agent_conversation"))
            setting = setting_r.scalar_one_or_none()
            max_rounds = (setting.value.get("max_rounds", 5) if setting else 5)
            # Look up source agent
            src_result = await db.execute(select(Agent).where(Agent.id == from_agent_id))
            source_agent = src_result.scalar_one_or_none()
            source_name = source_agent.name if source_agent else "Unknown agent"

            # Find target agent by name
            result = await db.execute(
                select(Agent).where(Agent.name.ilike(f"%{agent_name}%"), Agent.id != from_agent_id)
            )
            target = result.scalars().first()
            if not target:
                all_r = await db.execute(select(Agent).where(Agent.id != from_agent_id))
                names = [a.name for a in all_r.scalars().all()]
                return f"❌ No agent found matching '{agent_name}'. Available: {', '.join(names) if names else 'none'}"

            # Check if target agent has expired
            if target.is_expired or (target.expires_at and datetime.now(timezone.utc) >= target.expires_at):
                return f"⚠️ {target.name} is currently unavailable — their service period has ended. Please contact the platform administrator."

            # Prepare LLM for both agents
            from app.services.agent_context import build_agent_context
            from app.models.llm import LLMModel
            import httpx

            # Check target has LLM configured
            if not target.primary_model_id:
                return f"⚠️ {target.name} has no LLM model configured"

            model_r = await db.execute(select(LLMModel).where(LLMModel.id == target.primary_model_id))
            target_model = model_r.scalar_one_or_none()
            if not target_model:
                return f"⚠️ {target.name}'s model configuration is invalid"

            # Also load source agent's model (for multi-turn, source needs to respond too)
            source_model = None
            if source_agent and source_agent.primary_model_id:
                sm_r = await db.execute(select(LLMModel).where(LLMModel.id == source_agent.primary_model_id))
                source_model = sm_r.scalar_one_or_none()

            # Conversation termination instruction — appended to both agents' system prompts
            CONV_INSTRUCTION = (
                "\n\n--- Agent-to-Agent Conversation Rules ---\n"
                "You are now in a conversation with another digital employee. Communicate efficiently and stay on topic.\n"
                "When you believe the conversation has achieved its purpose, information exchange is complete, "
                "or there is nothing more valuable to add, append the marker [DONE] at the end of your reply.\n"
                "Note: Only add [DONE] when you genuinely feel the conversation can naturally conclude. Do not end prematurely.\n"
            )

            # Build system prompts
            target_system = await build_agent_context(target.id, target.name, target.role_description or "")
            target_system += CONV_INSTRUCTION

            source_system = ""
            if source_agent:
                source_system = await build_agent_context(source_agent.id, source_agent.name, source_agent.role_description or "")
                source_system += CONV_INSTRUCTION

            # Helper: call LLM
            async def call_llm(model: LLMModel, system: str, messages_list: list[dict]) -> str:
                from app.services.llm_utils import get_provider_base_url
                base_url = get_provider_base_url(model.provider, model.base_url)
                url = f"{base_url.rstrip('/')}/chat/completions"
                full_msgs = [{"role": "system", "content": system}] + messages_list
                async with httpx.AsyncClient(timeout=60) as client:
                    resp = await client.post(
                        url,
                        json={"model": model.model, "messages": full_msgs, "temperature": 0.7, "max_tokens": 1024},
                        headers={"Authorization": f"Bearer {model.api_key_encrypted}"},
                    )
                    data = resp.json()
                if "choices" in data and data["choices"]:
                    return data["choices"][0].get("message", {}).get("content", "")
                return ""

            # Save a message record
            async def save_msg(sender_id, receiver_id, content):
                db.add(Message(
                    sender_type="agent", sender_id=sender_id,
                    receiver_type="agent", receiver_id=receiver_id,
                    content=content, msg_type="text",
                ))
                await db.commit()

            # -- Multi-turn conversation loop --
            # conversation_messages tracks the dialogue from target's perspective
            # (user = source agent, assistant = target agent)
            conversation_messages: list[dict] = []

            # Load recent history
            hist_result = await db.execute(
                select(Message)
                .where(
                    ((Message.sender_id == from_agent_id) & (Message.receiver_id == target.id)) |
                    ((Message.sender_id == target.id) & (Message.receiver_id == from_agent_id))
                )
                .order_by(Message.created_at.desc())
                .limit(6)
            )
            for m in reversed(hist_result.scalars().all()):
                role = "user" if m.sender_id == from_agent_id else "assistant"
                conversation_messages.append({"role": role, "content": m.content})

            # Add the initial message from source
            conversation_messages.append({"role": "user", "content": f"[From {source_name}] {message_text}"})
            await save_msg(from_agent_id, target.id, message_text)

            transcript = []  # Human-readable transcript
            transcript.append(f"📤 {source_name}: {message_text}")

            for round_num in range(1, max_rounds + 1):
                # -- Target agent responds --
                target_reply = await call_llm(target_model, target_system, conversation_messages)
                if not target_reply:
                    transcript.append(f"⚠️ {target.name} no reply (LLM returned empty)")
                    break

                # Check for DONE signal
                target_done = "[DONE]" in target_reply
                clean_reply = target_reply.replace("[DONE]", "").strip()

                await save_msg(target.id, from_agent_id, clean_reply)
                conversation_messages.append({"role": "assistant", "content": clean_reply})
                transcript.append(f"💬 {target.name}: {clean_reply}")

                if target_done:
                    break  # Target feels conversation is complete

                # -- Source agent responds (if has model and not last round) --
                if round_num >= max_rounds:
                    break  # Max rounds reached

                if not source_model:
                    break  # Source can't continue without a model

                # Build messages from source's perspective (swap roles)
                source_messages = []
                for m in conversation_messages:
                    if m["role"] == "user":
                        source_messages.append({"role": "assistant", "content": m["content"]})
                    else:
                        source_messages.append({"role": "user", "content": f"[From {target.name}] {m['content']}"})

                source_reply = await call_llm(source_model, source_system, source_messages)
                if not source_reply:
                    break

                source_done = "[DONE]" in source_reply
                clean_source = source_reply.replace("[DONE]", "").strip()

                await save_msg(from_agent_id, target.id, clean_source)
                conversation_messages.append({"role": "user", "content": clean_source})
                transcript.append(f"📤 {source_name}: {clean_source}")

                if source_done:
                    break  # Source feels conversation is complete

            # Log activity for both agents
            from app.services.activity_logger import log_activity
            round_count = len([t for t in transcript if t.startswith("💬") or t.startswith("📤")])
            await log_activity(
                target.id, "agent_msg_sent",
                f"Had {round_count} rounds of conversation with {source_name}",
                detail={"partner": source_name, "rounds": round_count, "transcript": "\n".join(transcript)[:2000]},
            )
            await log_activity(
                from_agent_id, "agent_msg_sent",
                f"Had {round_count} rounds of conversation with {target.name}",
                detail={"partner": target.name, "rounds": round_count, "transcript": "\n".join(transcript)[:2000]},
            )

            return "💬 Conversation transcript:\n" + "\n".join(transcript)

    except Exception as e:
        import traceback
        traceback.print_exc()
        return f"❌ Message send error: {str(e)[:200]}"


# ═══════════════════════════════════════════════════════
# Plaza Tools — Agent Square social feed
# ═══════════════════════════════════════════════════════

async def _plaza_get_new_posts(arguments: dict) -> str:
    """Get recent posts from the Agent Plaza."""
    from app.models.plaza import PlazaPost, PlazaComment
    from sqlalchemy import desc

    limit = min(arguments.get("limit", 10), 20)

    try:
        async with async_session() as db:
            q = select(PlazaPost).order_by(desc(PlazaPost.created_at)).limit(limit)
            result = await db.execute(q)
            posts = result.scalars().all()

            if not posts:
                return "📭 No posts in the plaza yet. Be the first to share something!"

            output = []
            for p in posts:
                # Load comments
                cr = await db.execute(
                    select(PlazaComment).where(PlazaComment.post_id == p.id).order_by(PlazaComment.created_at).limit(5)
                )
                comments = cr.scalars().all()
                icon = "🤖" if p.author_type == "agent" else "👤"
                time_str = p.created_at.strftime("%m-%d %H:%M") if p.created_at else ""
                post_text = f"{icon} **{p.author_name}** ({time_str}) [post_id: {p.id}]\n{p.content}\n❤️ {p.likes_count}  💬 {p.comments_count}"
                if comments:
                    for c in comments:
                        c_icon = "🤖" if c.author_type == "agent" else "👤"
                        post_text += f"\n  └─ {c_icon} {c.author_name}: {c.content}"
                output.append(post_text)

            return "🏛️ Agent Plaza — Recent Posts:\n\n" + "\n\n---\n\n".join(output)

    except Exception as e:
        return f"❌ Failed to load plaza posts: {str(e)[:200]}"


async def _plaza_create_post(agent_id: uuid.UUID, arguments: dict) -> str:
    """Create a new post in the Agent Plaza."""
    from app.models.plaza import PlazaPost
    from app.models.agent import Agent as AgentModel

    content = arguments.get("content", "").strip()
    if not content:
        return "❌ Post content cannot be empty."
    if len(content) > 500:
        content = content[:500]

    try:
        async with async_session() as db:
            # Get agent name
            ar = await db.execute(select(AgentModel).where(AgentModel.id == agent_id))
            agent = ar.scalar_one_or_none()
            if not agent:
                return "❌ Agent not found."

            post = PlazaPost(
                author_id=agent_id,
                author_type="agent",
                author_name=agent.name,
                content=content,
                tenant_id=agent.tenant_id,
            )
            db.add(post)
            await db.commit()
            await db.refresh(post)
            return f"✅ Post published! (ID: {post.id})"

    except Exception as e:
        return f"❌ Failed to create post: {str(e)[:200]}"


async def _plaza_add_comment(agent_id: uuid.UUID, arguments: dict) -> str:
    """Add a comment to a plaza post."""
    from app.models.plaza import PlazaPost, PlazaComment
    from app.models.agent import Agent as AgentModel

    post_id = arguments.get("post_id", "")
    content = arguments.get("content", "").strip()
    if not content:
        return "❌ Comment content cannot be empty."
    if len(content) > 300:
        content = content[:300]

    try:
        pid = uuid.UUID(str(post_id))
    except Exception:
        return "❌ Invalid post_id format."

    try:
        async with async_session() as db:
            # Verify post exists
            pr = await db.execute(select(PlazaPost).where(PlazaPost.id == pid))
            post = pr.scalar_one_or_none()
            if not post:
                return "❌ Post not found."

            # Get agent name
            ar = await db.execute(select(AgentModel).where(AgentModel.id == agent_id))
            agent = ar.scalar_one_or_none()
            if not agent:
                return "❌ Agent not found."

            comment = PlazaComment(
                post_id=pid,
                author_id=agent_id,
                author_type="agent",
                author_name=agent.name,
                content=content,
            )
            db.add(comment)
            post.comments_count = (post.comments_count or 0) + 1
            await db.commit()
            return f"✅ Comment added to post by {post.author_name}."

    except Exception as e:
        return f"❌ Failed to add comment: {str(e)[:200]}"


# ─── Code Execution ─────────────────────────────────────────────

# Dangerous patterns to block
_DANGEROUS_BASH = [
    "rm -rf /", "rm -rf ~", "sudo ", "mkfs", "dd if=",
    ":(){ :", "chmod 777 /", "chown ", "shutdown", "reboot",
    "curl ", "wget ", "nc ", "ncat ", "ssh ", "scp ",
    "python3 -c", "python -c",
]

_DANGEROUS_PYTHON_IMPORTS = [
    "subprocess", "shutil.rmtree", "os.system", "os.popen",
    "os.exec", "os.spawn",
    "socket", "http.client", "urllib.request", "requests",
    "ftplib", "smtplib", "telnetlib", "ctypes",
    "__import__", "importlib",
]


def _check_code_safety(language: str, code: str) -> str | None:
    """Check code for dangerous patterns. Returns error message if unsafe, None if ok."""
    code_lower = code.lower()

    if language == "bash":
        for pattern in _DANGEROUS_BASH:
            if pattern.lower() in code_lower:
                return f"❌ Blocked: dangerous command detected ({pattern.strip()})"
        # Block deep path traversal outside workspace
        if "../../" in code:
            return "❌ Blocked: directory traversal not allowed"

    elif language == "python":
        for pattern in _DANGEROUS_PYTHON_IMPORTS:
            if pattern.lower() in code_lower:
                return f"❌ Blocked: unsafe operation detected ({pattern})"

    elif language == "node":
        dangerous_node = ["child_process", "fs.rmSync", "fs.rmdirSync", "process.exit",
                          "require('http')", "require('https')", "require('net')"]
        for pattern in dangerous_node:
            if pattern.lower() in code_lower:
                return f"❌ Blocked: unsafe operation detected ({pattern})"

    return None


async def _execute_code(ws: Path, arguments: dict) -> str:
    """Execute code in a sandboxed subprocess within the agent's workspace."""
    import asyncio

    language = arguments.get("language", "python")
    code = arguments.get("code", "")
    timeout = min(arguments.get("timeout", 30), 60)  # Max 60 seconds

    if not code.strip():
        return "❌ No code provided"

    if language not in ("python", "bash", "node"):
        return f"❌ Unsupported language: {language}. Use: python, bash, or node"

    # Security check
    safety_error = _check_code_safety(language, code)
    if safety_error:
        return safety_error

    # Working directory is the agent's workspace/ subdirectory (must be absolute)
    work_dir = (ws / "workspace").resolve()
    work_dir.mkdir(parents=True, exist_ok=True)

    # Determine command and file extension
    if language == "python":
        ext = ".py"
        cmd_prefix = ["python3"]
    elif language == "bash":
        ext = ".sh"
        cmd_prefix = ["bash"]
    elif language == "node":
        ext = ".js"
        cmd_prefix = ["node"]
    else:
        return f"❌ Unsupported language: {language}"

    # Write code to a temp file inside workspace
    script_path = work_dir / f"_exec_tmp{ext}"
    try:
        script_path.write_text(code, encoding="utf-8")

        # Inherit parent environment but override HOME to workspace
        safe_env = dict(os.environ)
        safe_env["HOME"] = str(work_dir)
        safe_env["PYTHONDONTWRITEBYTECODE"] = "1"

        proc = await asyncio.create_subprocess_exec(
            *cmd_prefix, str(script_path),
            cwd=str(work_dir),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=safe_env,
        )

        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.communicate()
            return f"❌ Code execution timed out after {timeout}s"

        stdout_str = stdout.decode("utf-8", errors="replace")[:10000]
        stderr_str = stderr.decode("utf-8", errors="replace")[:5000]

        result_parts = []
        if stdout_str.strip():
            result_parts.append(f"📤 Output:\n{stdout_str}")
        if stderr_str.strip():
            result_parts.append(f"⚠️ Stderr:\n{stderr_str}")
        if proc.returncode != 0:
            result_parts.append(f"Exit code: {proc.returncode}")

        if not result_parts:
            return "✅ Code executed successfully (no output)"

        return "\n\n".join(result_parts)

    except Exception as e:
        return f"❌ Execution error: {str(e)[:200]}"
    finally:
        # Clean up temp script
        try:
            script_path.unlink(missing_ok=True)
        except Exception:
            pass


# ─── Resource Discovery Executors ───────────────────────────────

async def _discover_resources(arguments: dict) -> str:
    """Search Smithery registry for MCP servers."""
    query = arguments.get("query", "")
    if not query:
        return "❌ Please provide a search query describing the capability you need."
    max_results = min(arguments.get("max_results", 5), 10)

    from app.services.resource_discovery import search_smithery
    return await search_smithery(query, max_results)


async def _import_mcp_server(agent_id: uuid.UUID, arguments: dict) -> str:
    """Import an MCP server from Smithery."""
    server_id = arguments.get("server_id", "")
    if not server_id:
        return "❌ Please provide a server_id (e.g. '@anthropic/brave-search'). Use discover_resources first to find available servers."
    config = arguments.get("config")

    from app.services.resource_discovery import import_mcp_from_smithery
    return await import_mcp_from_smithery(server_id, agent_id, config)
