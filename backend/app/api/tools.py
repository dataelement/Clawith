"""Tool management API — CRUD for tools and per-agent assignments."""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import get_current_user
from app.database import get_db
from app.models.tool import Tool, AgentTool
from app.models.user import User

router = APIRouter(prefix="/tools", tags=["tools"])


# Sensitive field keys that should be encrypted when stored.
# This is used as a FALLBACK for tools that don't have config_schema.
# When config_schema is available, fields with type='password' are used instead.
SENSITIVE_FIELD_KEYS = {"api_key", "private_key", "auth_code", "password", "secret"}


def _get_sensitive_keys(config_schema: dict | None = None) -> set[str]:
    """Determine which config keys are sensitive.

    If config_schema is provided, extract keys whose field type is 'password'.
    Always includes the hardcoded SENSITIVE_FIELD_KEYS as a fallback so that
    tools without config_schema still get encrypted/decrypted correctly.
    """
    keys = set(SENSITIVE_FIELD_KEYS)
    if config_schema:
        for field in config_schema.get("fields", []):
            if field.get("type") == "password":
                keys.add(field.get("key", ""))
    keys.discard("")  # remove empty string if any
    return keys


def _encrypt_sensitive_fields(config: dict, config_schema: dict | None = None) -> dict:
    """Encrypt sensitive fields in config dict.

    Args:
        config: Tool config dict
        config_schema: Optional config_schema to extract password-type field keys

    Returns:
        Config dict with sensitive fields encrypted
    """
    from app.core.security import encrypt_data, decrypt_data
    from app.config import get_settings

    if not config:
        return config

    settings = get_settings()
    result = dict(config)
    sensitive_keys = _get_sensitive_keys(config_schema)

    for key in sensitive_keys:
        if key in result and result[key]:
            value = result[key]
            if isinstance(value, str) and value:
                # Guard against double-encryption: if the value can be
                # successfully decrypted, it is already encrypted — skip it.
                # This happens when the frontend re-submits a config without
                # the user changing the password field (the field value comes
                # from a previous list_tools response which returns decrypted
                # values… EXCEPT when list_tools runs against a tool whose
                # config_schema is empty and therefore couldn't decrypt).
                try:
                    decrypt_data(value, settings.SECRET_KEY)
                    # Decryption succeeded → value is already encrypted, keep as-is
                    continue
                except Exception:
                    # Not encrypted yet → proceed to encrypt
                    pass

                try:
                    result[key] = encrypt_data(value, settings.SECRET_KEY)
                except Exception:
                    # If encryption fails, keep the value as-is
                    pass

    return result


def _decrypt_sensitive_fields(config: dict, config_schema: dict | None = None) -> dict:
    """Decrypt sensitive fields in config dict.

    Args:
        config: Tool config dict
        config_schema: Optional config_schema to extract password-type field keys

    Returns:
        Config dict with sensitive fields decrypted
    """
    from app.core.security import decrypt_data
    from app.config import get_settings

    if not config:
        return config

    settings = get_settings()
    result = dict(config)
    sensitive_keys = _get_sensitive_keys(config_schema)

    for key in sensitive_keys:
        if key in result and result[key]:
            value = result[key]
            if isinstance(value, str) and value:
                try:
                    result[key] = decrypt_data(value, settings.SECRET_KEY)
                except Exception:
                    # If decryption fails, assume it's plaintext
                    pass

    return result


# ─── Schemas ────────────────────────────────────────────────
class ToolCreate(BaseModel):
    name: str
    display_name: str
    description: str = ""
    type: str = "mcp"
    category: str = "custom"
    icon: str = "🔧"
    parameters_schema: dict = {}
    mcp_server_url: str | None = None
    mcp_server_name: str | None = None
    mcp_tool_name: str | None = None
    is_default: bool = False
    # Optional: platform admins can specify target tenant (e.g. when managing
    # another company's tools via the Enterprise Settings page).
    tenant_id: str | None = None


class ToolUpdate(BaseModel):
    display_name: str | None = None
    description: str | None = None
    icon: str | None = None
    enabled: bool | None = None
    mcp_server_url: str | None = None
    mcp_server_name: str | None = None
    parameters_schema: dict | None = None
    is_default: bool | None = None
    config: dict | None = None


class AgentToolUpdate(BaseModel):
    tool_id: str
    enabled: bool


class CategoryConfigUpdate(BaseModel):
    config: dict


# ─── Global Tool CRUD ──────────────────────────────────────
@router.get("")
async def list_tools(
    tenant_id: str | None = None,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List platform tools scoped by tenant (builtin + tenant-specific)."""
    query = (
        select(Tool)
        .where(Tool.source.in_(["builtin", "admin"]))
        .order_by(Tool.category, Tool.name)
    )
    # Scope by tenant: show builtin (tenant_id is NULL) + tenant-specific tools
    tid = tenant_id or (str(current_user.tenant_id) if current_user.tenant_id else None)
    if tid:
        from sqlalchemy import or_ as _or
        query = query.where(_or(Tool.tenant_id == None, Tool.tenant_id == uuid.UUID(tid)))
    result = await db.execute(query)
    tools = result.scalars().all()
    return [
        {
            "id": str(t.id),
            "name": t.name,
            "display_name": t.display_name,
            "description": t.description,
            "type": t.type,
            "category": t.category,
            "icon": t.icon,
            "parameters_schema": t.parameters_schema,
            "mcp_server_url": t.mcp_server_url,
            "mcp_server_name": t.mcp_server_name,
            "mcp_tool_name": t.mcp_tool_name,
            "enabled": t.enabled,
            "is_default": t.is_default,
            # Decrypt config for the admin UI so saved values are readable
            "config": _decrypt_sensitive_fields(t.config or {}, t.config_schema),
            "config_schema": t.config_schema or {},
            "created_at": t.created_at.isoformat() if t.created_at else None,
        }
        for t in tools
    ]


@router.post("")
async def create_tool(
    data: ToolCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Create a new tool (typically MCP).

    The tool is scoped to the target tenant, which defaults to the caller's
    own tenant but can be overridden via data.tenant_id. This allows platform
    admins to import MCP tools while viewing another company's settings page.
    """
    # Resolve target tenant: explicit payload value takes priority so that
    # platform admins importing tools for another company work correctly.
    target_tenant_id: uuid.UUID | None = None
    if data.tenant_id:
        try:
            target_tenant_id = uuid.UUID(data.tenant_id)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid tenant_id format")
    else:
        target_tenant_id = current_user.tenant_id

    # Unique name check is scoped per tenant to avoid cross-tenant collisions.
    existing = await db.execute(
        select(Tool).where(Tool.name == data.name, Tool.tenant_id == target_tenant_id)
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=400, detail=f"Tool '{data.name}' already exists")

    tool = Tool(
        name=data.name,
        display_name=data.display_name,
        description=data.description,
        type=data.type,
        category=data.category,
        icon=data.icon,
        parameters_schema=data.parameters_schema,
        mcp_server_url=data.mcp_server_url,
        mcp_server_name=data.mcp_server_name,
        mcp_tool_name=data.mcp_tool_name,
        is_default=data.is_default,
        tenant_id=target_tenant_id,
        source="admin",
    )
    db.add(tool)
    await db.commit()
    await db.refresh(tool)
    return {"id": str(tool.id), "name": tool.name}


# NOTE: Literal path routes (/bulk, /mcp-server) MUST be defined BEFORE
# parameterized routes (/{tool_id}) to avoid older FastAPI/Starlette versions
# matching "bulk" as a uuid.UUID path parameter and returning 422.

class BulkToolUpdateItem(BaseModel):
    tool_id: str
    enabled: bool

@router.put("/bulk")
async def update_tools_bulk(
    updates: list[BulkToolUpdateItem],
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Bulk update the enabled status of multiple tools."""
    tool_ids = [uuid.UUID(u.tool_id) for u in updates]
    result = await db.execute(select(Tool).where(Tool.id.in_(tool_ids)))
    tools_map = {str(t.id): t for t in result.scalars().all()}
    
    for update in updates:
        if update.tool_id in tools_map:
            tools_map[update.tool_id].enabled = update.enabled
            
    await db.commit()
    return {"ok": True}


@router.put("/{tool_id}")
async def update_tool(
    tool_id: uuid.UUID,
    data: ToolUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Update a tool."""
    result = await db.execute(select(Tool).where(Tool.id == tool_id))
    tool = result.scalar_one_or_none()
    if not tool:
        raise HTTPException(status_code=404, detail="Tool not found")

    update_data = data.model_dump(exclude_unset=True)
    # Encrypt sensitive fields in config
    if "config" in update_data and update_data["config"]:
        update_data["config"] = _encrypt_sensitive_fields(update_data["config"], tool.config_schema)

    for field, value in update_data.items():
        setattr(tool, field, value)
    await db.commit()
    return {"ok": True}


@router.delete("/{tool_id}")
async def delete_tool(
    tool_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Delete a tool (only non-builtin)."""
    result = await db.execute(select(Tool).where(Tool.id == tool_id))
    tool = result.scalar_one_or_none()
    if not tool:
        raise HTTPException(status_code=404, detail="Tool not found")
    if tool.type == "builtin":
        raise HTTPException(status_code=400, detail="Cannot delete builtin tools")

    await db.execute(delete(AgentTool).where(AgentTool.tool_id == tool_id))
    await db.delete(tool)
    await db.commit()
    return {"ok": True}


# ─── Per-Agent Tool Assignment ─────────────────────────────
@router.get("/agents/{agent_id}")
async def get_agent_tools(
    agent_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get tools for a specific agent with their enabled status."""
    from app.services.agent_tools import _agent_has_feishu
    from app.models.agent import Agent as AgentModel
    has_feishu = await _agent_has_feishu(agent_id)

    # Determine if this is a system agent (e.g. OKR Agent).
    # System agents can see all tools; regular agents cannot see okr_agent_only tools.
    agent_r = await db.execute(select(AgentModel).where(AgentModel.id == agent_id))
    agent_obj = agent_r.scalar_one_or_none()
    is_system_agent = bool(agent_obj and agent_obj.is_system)

    # All available tools
    all_tools_r = await db.execute(select(Tool).where(Tool.enabled == True).order_by(Tool.category, Tool.name))
    all_tools = all_tools_r.scalars().all()

    # Agent-specific assignments
    agent_tools_r = await db.execute(select(AgentTool).where(AgentTool.agent_id == agent_id))
    assignments = {str(at.tool_id): at for at in agent_tools_r.scalars().all()}

    result = []
    for t in all_tools:
        # Hide feishu tools for agents without Feishu channel
        if t.category == "feishu" and not has_feishu:
            continue
        # Hide OKR Agent-exclusive tools from regular agents.
        # These tools (create_objective, collect_okr_progress, etc.) should only
        # appear in the tool panel of system agents such as the OKR Agent.
        if (t.config or {}).get("okr_agent_only") and not is_system_agent:
            continue
        tid = str(t.id)
        at = assignments.get(tid)
        # MCP tools installed by agents only show for that agent.
        # MCP admin tools show for all agents (default disabled).
        if t.source == "agent" and not at:
            continue
        # If no explicit assignment, use is_default
        enabled = at.enabled if at else t.is_default
        result.append({
            "id": tid,
            "name": t.name,
            "display_name": t.display_name,
            "description": t.description,
            "type": t.type,
            "category": t.category,
            "icon": t.icon,
            "enabled": enabled,
            "is_default": t.is_default,
            "mcp_server_name": t.mcp_server_name,
        })
    return result


@router.put("/agents/{agent_id}")
async def update_agent_tools(
    agent_id: uuid.UUID,
    updates: list[AgentToolUpdate],
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Update tool assignments for an agent."""
    for u in updates:
        tool_id = uuid.UUID(u.tool_id)
        # Upsert
        result = await db.execute(
            select(AgentTool).where(AgentTool.agent_id == agent_id, AgentTool.tool_id == tool_id)
        )
        at = result.scalar_one_or_none()
        if at:
            at.enabled = u.enabled
        else:
            db.add(AgentTool(agent_id=agent_id, tool_id=tool_id, enabled=u.enabled))
    await db.commit()
    return {"ok": True}


# ─── MCP Server Testing ────────────────────────────────────
class MCPTestRequest(BaseModel):
    server_url: str
    # Optional standalone API Key. If provided, it is sent as
    # 'Authorization: Bearer {api_key}' and is NOT embedded in the URL.
    api_key: str | None = None


@router.post("/test-mcp")
async def test_mcp_connection(
    data: MCPTestRequest,
    current_user: User = Depends(get_current_user),
):
    """Test connection to an MCP server and list available tools.

    Supports two authentication modes:
    - URL-embedded key (e.g. ?tavilyApiKey=xxx) — include in server_url.
    - Bearer token — pass via api_key field; sent as Authorization header.
    """
    from app.services.mcp_client import MCPClient

    try:
        client = MCPClient(data.server_url, api_key=data.api_key or None)
        tools = await client.list_tools()
        return {"ok": True, "tools": tools}
    except Exception as e:
        return {"ok": False, "error": str(e)[:300]}


# ─── MCP Server-level Credential Management ────────────────
class MCPServerUpdate(BaseModel):
    server_name: str            # Identifies which server's tools to update
    server_url: str             # New MCP server URL (may contain embedded key)
    api_key: str | None = None  # Optional standalone Bearer key
    # Target tenant (platform admins may manage another company's tools)
    tenant_id: str | None = None


@router.put("/mcp-server")
async def update_mcp_server(
    data: MCPServerUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Bulk-update the Server URL and API Key for all tools from an MCP server.

    All tools sharing the same mcp_server_name under the target tenant are
    updated atomically. The API Key is stored encrypted in tool.config so
    the agent runner can resolve it at execution time without re-configuring
    each tool individually.

    Authentication priority at runtime (handled by MCPClient):
    1. tool.config['api_key'] — sent as Authorization: Bearer header.
    2. URL query param (e.g. ?tavilyApiKey=xxx) — extracted from the URL
       and converted to Bearer by MCPClient automatically.
    """
    # Resolve target tenant
    target_tenant_id: uuid.UUID | None = None
    if data.tenant_id:
        try:
            target_tenant_id = uuid.UUID(data.tenant_id)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid tenant_id format")
    else:
        target_tenant_id = current_user.tenant_id

    # Load all tools from this server under the target tenant
    result = await db.execute(
        select(Tool).where(
            Tool.mcp_server_name == data.server_name,
            Tool.tenant_id == target_tenant_id,
        )
    )
    tools = result.scalars().all()
    if not tools:
        raise HTTPException(
            status_code=404,
            detail=f"No tools found for server '{data.server_name}'",
        )

    for tool in tools:
        tool.mcp_server_url = data.server_url
        if data.api_key is not None:
            # Merge api_key into existing config (other keys preserved) and encrypt
            current_config = dict(tool.config or {})
            current_config["api_key"] = data.api_key
            tool.config = _encrypt_sensitive_fields(current_config, tool.config_schema)
        # If api_key is None (not provided), preserve the existing encrypted key

    await db.commit()
    return {"ok": True, "updated": len(tools)}




# ─── Agent-installed Tools Management (admin) ───────────────

@router.get("/agent-installed")
async def list_agent_installed_tools(
    tenant_id: str | None = None,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Admin endpoint: list user-installed tools scoped by tenant."""
    from app.models.agent import Agent
    query = (
        select(AgentTool, Tool, Agent)
        .join(Tool, AgentTool.tool_id == Tool.id)
        .outerjoin(Agent, AgentTool.installed_by_agent_id == Agent.id)
        .where(AgentTool.source == "user_installed")
        .order_by(AgentTool.created_at.desc())
    )
    # Scope by tenant: only show tools installed by agents in this tenant
    tid = tenant_id or (str(current_user.tenant_id) if current_user.tenant_id else None)
    if tid:
        from app.models.agent import Agent as Ag
        tenant_agent_ids = select(Ag.id).where(Ag.tenant_id == tid)
        query = query.where(AgentTool.agent_id.in_(tenant_agent_ids))
    result = await db.execute(query)
    rows = result.all()
    return [
        {
            "agent_tool_id": str(at.id),
            "agent_id": str(at.agent_id),
            "tool_id": str(t.id),
            "tool_name": t.name,
            "tool_display_name": t.display_name,
            "mcp_server_name": t.mcp_server_name,
            "installed_by_agent_id": str(at.installed_by_agent_id) if at.installed_by_agent_id else None,
            "installed_by_agent_name": a.name if a else None,
            "enabled": at.enabled,
            "installed_at": at.created_at.isoformat() if at.created_at else None,
        }
        for at, t, a in rows
    ]


@router.delete("/agent-tool/{agent_tool_id}")
async def delete_agent_tool(
    agent_tool_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Admin: remove an agent-tool assignment. Also deletes the tool record if no other agents use it."""
    at_r = await db.execute(select(AgentTool).where(AgentTool.id == agent_tool_id))
    at = at_r.scalar_one_or_none()
    if not at:
        raise HTTPException(status_code=404, detail="Agent tool assignment not found")
    tool_id = at.tool_id
    await db.delete(at)
    await db.flush()
    # If no other agent uses this tool, delete the tool record too (for MCP tools)
    remaining_r = await db.execute(select(AgentTool).where(AgentTool.tool_id == tool_id).limit(1))
    if not remaining_r.scalar_one_or_none():
        tool_r = await db.execute(select(Tool).where(Tool.id == tool_id))
        tool = tool_r.scalar_one_or_none()
        if tool and tool.type == "mcp":
            await db.delete(tool)
    await db.commit()
    return {"ok": True}


# ─── Per-Agent Tool Config ───────────────────────────────────

class AgentToolConfigUpdate(BaseModel):
    config: dict


@router.get("/agents/{agent_id}/tool-config/{tool_id}")
async def get_agent_tool_config(
    agent_id: uuid.UUID,
    tool_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get merged tool config (global defaults + agent overrides) and config_schema.

    Both configs are decrypted before returning. Global sensitive fields are
    masked so the frontend can show a key is configured without exposing it.
    """
    tool_r = await db.execute(select(Tool).where(Tool.id == tool_id))
    tool = tool_r.scalar_one_or_none()
    if not tool:
        raise HTTPException(status_code=404, detail="Tool not found")
    at_r = await db.execute(
        select(AgentTool).where(AgentTool.agent_id == agent_id, AgentTool.tool_id == tool_id)
    )
    at = at_r.scalar_one_or_none()

    # Decrypt both configs using the tool's config_schema for field type awareness
    schema = tool.config_schema
    raw_global = _decrypt_sensitive_fields(tool.config or {}, schema)
    raw_agent = _decrypt_sensitive_fields(at.config if at else {}, schema)

    # Mask sensitive fields in global config for display
    masked_global = dict(raw_global)
    sensitive_keys = _get_sensitive_keys(schema)
    for key in sensitive_keys:
        val = masked_global.get(key)
        if val and isinstance(val, str) and len(val) > 0:
            suffix = val[-4:] if len(val) > 4 else val
            masked_global[key] = f"****{suffix}"

    # Merged: agent overrides take precedence over global defaults.
    # Use raw (non-masked) global as the base so the agent inherits actual values
    # at runtime, but the UI will show masked_global for display hints.
    merged = {**raw_global, **(raw_agent or {})}
    return {
        "global_config": masked_global,
        "agent_config": raw_agent or {},
        "merged_config": merged,
        "config_schema": tool.config_schema or {},
    }


@router.put("/agents/{agent_id}/tool-config/{tool_id}")
async def update_agent_tool_config(
    agent_id: uuid.UUID,
    tool_id: uuid.UUID,
    data: AgentToolConfigUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Save per-agent config override for a tool."""
    # Check permission: only platform_admin and org_admin can modify allow_network
    if "allow_network" in data.config:
        if current_user.role not in ("platform_admin", "org_admin"):
            raise HTTPException(
                status_code=403,
                detail="Only platform admin or organization admin can modify network access settings"
            )

    # Encrypt sensitive fields using the tool's config_schema for field type awareness
    tool_r2 = await db.execute(select(Tool).where(Tool.id == tool_id))
    tool_for_schema = tool_r2.scalar_one_or_none()
    encrypted_config = _encrypt_sensitive_fields(data.config, tool_for_schema.config_schema if tool_for_schema else None)

    at_r = await db.execute(
        select(AgentTool).where(AgentTool.agent_id == agent_id, AgentTool.tool_id == tool_id)
    )
    at = at_r.scalar_one_or_none()
    if at:
        at.config = encrypted_config
    else:
        # Create assignment if not exists
        db.add(AgentTool(agent_id=agent_id, tool_id=tool_id, enabled=True, config=encrypted_config))
    await db.commit()
    return {"ok": True}


@router.get("/agents/{agent_id}/with-config")
async def get_agent_tools_with_config(
    agent_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get agent's enabled tools with per-agent config info and config_schema for settings UI.

    Both global_config and agent_config are decrypted before returning.
    For global_config, sensitive fields are masked (e.g. "sk-****abcd") so the
    frontend can show that a company key is configured without exposing it.

    Special handling: some tools (Jina) store their API key in system_settings
    rather than Tool.config. We resolve those as part of the global config so
    the agent-level UI can show the inherited key hint.
    """
    from app.services.agent_tools import _agent_has_feishu
    from app.models.agent import Agent as AgentModel
    has_feishu = await _agent_has_feishu(agent_id)

    # Determine if this is a system agent (e.g. OKR Agent).
    agent_r2 = await db.execute(select(AgentModel).where(AgentModel.id == agent_id))
    agent_obj2 = agent_r2.scalar_one_or_none()
    is_system_agent2 = bool(agent_obj2 and agent_obj2.is_system)

    all_tools_r = await db.execute(select(Tool).where(Tool.enabled == True).order_by(Tool.category, Tool.name))
    all_tools = all_tools_r.scalars().all()
    agent_tools_r = await db.execute(select(AgentTool).where(AgentTool.agent_id == agent_id))
    assignments = {str(at.tool_id): at for at in agent_tools_r.scalars().all()}

    # Pre-fetch system_settings keys that some tools use as an alternative
    # config storage (e.g. Jina stores its API key in system_settings.jina_api_key)
    system_keys_cache: dict[str, str] = {}
    SYSTEM_SETTINGS_TOOL_MAP = {
        # tool_name -> system_settings key + value path
        "jina_search": ("jina_api_key", "api_key"),
        "jina_read": ("jina_api_key", "api_key"),
    }

    result = []
    for t in all_tools:
        # Hide feishu tools for agents without Feishu channel
        if t.category == "feishu" and not has_feishu:
            continue
        # Hide OKR Agent-exclusive tools from regular agents.
        if (t.config or {}).get("okr_agent_only") and not is_system_agent2:
            continue
        tid = str(t.id)
        at = assignments.get(tid)
        # MCP tools installed by agents only show for that agent.
        # MCP admin tools show for all agents (default disabled).
        if t.source == "agent" and not at:
            continue
        enabled = at.enabled if at else t.is_default

        # Decrypt configs for the frontend
        raw_global = _decrypt_sensitive_fields(t.config or {}, t.config_schema)

        # Fallback: resolve api_key from system_settings for tools that store
        # their key there (e.g. Jina). Only if Tool.config doesn't have it.
        if t.name in SYSTEM_SETTINGS_TOOL_MAP and not raw_global.get("api_key"):
            ss_key, ss_field = SYSTEM_SETTINGS_TOOL_MAP[t.name]
            if ss_key not in system_keys_cache:
                try:
                    from app.models.system_settings import SystemSetting
                    ss_r = await db.execute(
                        select(SystemSetting).where(SystemSetting.key == ss_key)
                    )
                    ss = ss_r.scalar_one_or_none()
                    system_keys_cache[ss_key] = (
                        ss.value.get(ss_field, "") if ss and ss.value else ""
                    )
                except Exception:
                    system_keys_cache[ss_key] = ""
            if system_keys_cache[ss_key]:
                raw_global["api_key"] = system_keys_cache[ss_key]

        raw_agent = _decrypt_sensitive_fields((at.config if at else {}) or {}, t.config_schema)

        # Mask sensitive fields in global_config so users can see that a key
        # is configured at the company level without exposing the full value.
        masked_global = dict(raw_global)
        sensitive_keys = _get_sensitive_keys(t.config_schema)
        for key in sensitive_keys:
            val = masked_global.get(key)
            if val and isinstance(val, str) and len(val) > 0:
                # Show "****" + last 4 chars as a hint
                suffix = val[-4:] if len(val) > 4 else val
                masked_global[key] = f"****{suffix}"

        result.append({
            "id": tid,
            "agent_tool_id": str(at.id) if at else None,
            "name": t.name,
            "display_name": t.display_name,
            "description": t.description,
            "type": t.type,
            "category": t.category,
            "icon": t.icon,
            "enabled": enabled,
            "is_default": t.is_default,
            "mcp_server_name": t.mcp_server_name,
            "config_schema": t.config_schema or {},
            "global_config": masked_global,
            "agent_config": raw_agent,
            "source": t.source,
        })
    return result


# ─── Email Connection Testing ──────────────────────────────

class EmailTestRequest(BaseModel):
    config: dict


@router.post("/test-email")
async def test_email_connection(
    data: EmailTestRequest,
    current_user: User = Depends(get_current_user),
):
    """Test IMAP and SMTP email connections with provided config."""
    from app.services.email_service import test_connection

    try:
        result = await test_connection(data.config)
        return result
    except Exception as e:
        return {"ok": False, "error": str(e)[:300]}


@router.get("/email-providers")
async def get_email_providers(
    current_user: User = Depends(get_current_user),
):
    """Get list of supported email provider presets with help text."""
    from app.services.email_service import EMAIL_PROVIDERS

    return {
        key: {
            "label": p["label"],
            "help_url": p.get("help_url", ""),
            "help_text": p.get("help_text", ""),
        }
        for key, p in EMAIL_PROVIDERS.items()
    }
# ─── Tool Category Sharing Config (Generic ChannelConfig) ───

@router.get("/agents/{agent_id}/category-config/{category}")
async def get_category_config(
    agent_id: uuid.UUID,
    category: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get shared configuration for a tool category.

    Returns both global_config (company-level, from Tool.config) and
    agent_config (agent-level override, from ChannelConfig) separately.
    Sensitive fields in global_config are masked for display.
    Company-level values always take precedence at runtime.
    """
    from app.core.permissions import check_agent_access
    from app.models.channel_config import ChannelConfig

    await check_agent_access(db, current_user, agent_id)

    # ── 1. Load company-level (global) config from Tool.config ──────────────
    # Find a tool in this category that actually has config data.
    # We cannot just LIMIT 1 because most tools may have empty config.
    all_cat_tools = await db.execute(
        select(Tool).where(
            Tool.category == category,
            Tool.enabled == True,
        ).order_by(Tool.name)
    )
    raw_global: dict = {}
    cat_schema: dict | None = None
    for ct in all_cat_tools.scalars():
        if ct.config and ct.config != {}:
            cat_schema = ct.config_schema
            raw_global = _decrypt_sensitive_fields(ct.config, cat_schema)
            break

    # Mask sensitive fields for UI display
    masked_global = dict(raw_global)
    sensitive_keys = _get_sensitive_keys(cat_schema)
    for key in sensitive_keys:
        val = masked_global.get(key)
        if val and isinstance(val, str):
            suffix = val[-4:] if len(val) > 4 else val
            masked_global[key] = f"****{suffix}"

    # ── 2. Load agent-level config from ChannelConfig ───────────────────────
    result = await db.execute(
        select(ChannelConfig).where(
            ChannelConfig.agent_id == agent_id,
            ChannelConfig.channel_type == category,
        )
    )
    config = result.scalar_one_or_none()

    config_id = None
    is_configured = bool(raw_global) or config is not None
    raw_agent: dict = {}

    if config:
        config_id = str(config.id)
        full_agent = {
            "api_key": config.app_secret,
            **(config.extra_config or {}),
        }
        raw_agent = _decrypt_sensitive_fields(full_agent)
        # Remove None values produced by missing app_secret
        raw_agent = {k: v for k, v in raw_agent.items() if v is not None}

    # ── 3. Build effective config ───────────────────────────────────────────
    # Priority: Agent config > Company config > Default
    # Agent can override company values by setting their own.
    effective_config = {**raw_global, **raw_agent}

    return {
        "id": config_id,
        "agent_id": str(agent_id),
        "category": category,
        "is_configured": is_configured,
        # Legacy field (backward-compat): full effective config for display
        "config": effective_config,
        # New fields for richer UI: show global and agent configs separately
        "global_config": masked_global,
        "agent_config": raw_agent,
    }


@router.post("/agents/{agent_id}/category-config/{category}")
async def update_category_config(
    agent_id: uuid.UUID,
    category: str,
    data: CategoryConfigUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Update or create shared configuration for a tool category."""
    from app.core.permissions import check_agent_access, is_agent_creator
    from app.models.channel_config import ChannelConfig

    agent, _ = await check_agent_access(db, current_user, agent_id)
    if not is_agent_creator(current_user, agent):
        raise HTTPException(status_code=403, detail="Only creator can configure category")

    # Encrypt sensitive fields
    encrypted_config = _encrypt_sensitive_fields(data.config)
    app_secret = encrypted_config.get("api_key") or encrypted_config.get("api_secret") or encrypted_config.get("app_secret")
    extra = {k: v for k, v in encrypted_config.items() if k not in ("api_key", "api_secret", "app_secret")}

    result = await db.execute(
        select(ChannelConfig).where(
            ChannelConfig.agent_id == agent_id,
            ChannelConfig.channel_type == category,
        )
    )
    existing = result.scalar_one_or_none()
    if existing:
        if app_secret:
            existing.app_secret = app_secret
        # Merge extra config (note: extra is already encrypted)
        existing.extra_config = {**(existing.extra_config or {}), **extra}
        existing.is_configured = True
    else:
        config = ChannelConfig(
            agent_id=agent_id,
            channel_type=category,
            app_id=category,
            app_secret=app_secret,
            extra_config=extra,
            is_configured=True,
        )
        db.add(config)

    await db.commit()

    # Special logic for Atlassian: trigger sync
    if category == "atlassian":
        from app.api.atlassian import _sync_atlassian_tools_for_agent
        import asyncio
        # Need plaintext key for sync
        plaintext_key = data.config.get("api_key") or data.config.get("api_secret") or data.config.get("app_secret")
        asyncio.create_task(_sync_atlassian_tools_for_agent(agent_id, plaintext_key))

    return {"ok": True}


@router.delete("/agents/{agent_id}/category-config/{category}", status_code=204)
async def delete_category_config(
    agent_id: uuid.UUID,
    category: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Remove shared configuration for a tool category."""
    from app.core.permissions import check_agent_access, is_agent_creator
    from app.models.channel_config import ChannelConfig

    agent, _ = await check_agent_access(db, current_user, agent_id)
    if not is_agent_creator(current_user, agent):
        raise HTTPException(status_code=403, detail="Only creator can remove config")

    await db.execute(
        delete(ChannelConfig).where(
            ChannelConfig.agent_id == agent_id,
            ChannelConfig.channel_type == category,
        )
    )
    await db.commit()


@router.post("/agents/{agent_id}/category-config/{category}/test")
async def test_category_config(
    agent_id: uuid.UUID,
    category: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Test connectivity for a tool category."""
    if category == "atlassian":
        from app.api.atlassian import test_atlassian_channel
        return await test_atlassian_channel(agent_id, current_user, db)
    elif category == "agentbay":
        from app.services.agentbay_client import test_agentbay_channel
        return await test_agentbay_channel(agent_id, current_user, db)

    return {"ok": True, "message": f"Settings for {category} saved."}
