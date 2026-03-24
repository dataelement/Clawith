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


# ─── Global Tool CRUD ──────────────────────────────────────
@router.get("")
async def list_tools(
    tenant_id: str | None = None,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List platform tools scoped by tenant (builtin + tenant-specific)."""
    from sqlalchemy import or_ as _or
    # Exclude tools that were installed by agents via import_mcp_server
    agent_installed_tids = select(AgentTool.tool_id).where(AgentTool.source == "user_installed")
    query = (
        select(Tool)
        .where(~Tool.id.in_(agent_installed_tids))
        .order_by(Tool.category, Tool.name)
    )
    # Scope by tenant: show builtin (tenant_id is NULL) + tenant-specific tools
    tid = tenant_id or (str(current_user.tenant_id) if current_user.tenant_id else None)
    if tid:
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
            "config": t.config or {},
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
    """Create a new tool (typically MCP)."""
    # Check unique name
    existing = await db.execute(select(Tool).where(Tool.name == data.name))
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
        tenant_id=current_user.tenant_id,
    )
    db.add(tool)
    await db.commit()
    await db.refresh(tool)
    return {"id": str(tool.id), "name": tool.name}


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

    for field, value in data.model_dump(exclude_unset=True).items():
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
    has_feishu = await _agent_has_feishu(agent_id)

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
        tid = str(t.id)
        at = assignments.get(tid)
        # Agent-installed MCP tools only show for agents that have an explicit assignment.
        # Gateway tools (category=mcp-gateway) are platform-level and always visible.
        if t.type == "mcp" and t.category != "mcp-gateway" and not at:
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


@router.post("/test-mcp")
async def test_mcp_connection(
    data: MCPTestRequest,
    current_user: User = Depends(get_current_user),
):
    """Test connection to an MCP server and list available tools."""
    from app.services.mcp_client import MCPClient

    try:
        client = MCPClient(data.server_url)
        tools = await client.list_tools()
        return {"ok": True, "tools": tools}
    except Exception as e:
        return {"ok": False, "error": str(e)[:300]}


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
    """Get merged tool config (global defaults + agent overrides) and config_schema."""
    tool_r = await db.execute(select(Tool).where(Tool.id == tool_id))
    tool = tool_r.scalar_one_or_none()
    if not tool:
        raise HTTPException(status_code=404, detail="Tool not found")
    at_r = await db.execute(
        select(AgentTool).where(AgentTool.agent_id == agent_id, AgentTool.tool_id == tool_id)
    )
    at = at_r.scalar_one_or_none()
    agent_config = at.config if at else {}
    merged = {**(tool.config or {}), **(agent_config or {})}
    return {
        "global_config": tool.config or {},
        "agent_config": agent_config or {},
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
    at_r = await db.execute(
        select(AgentTool).where(AgentTool.agent_id == agent_id, AgentTool.tool_id == tool_id)
    )
    at = at_r.scalar_one_or_none()
    if at:
        at.config = data.config
    else:
        # Create assignment if not exists
        db.add(AgentTool(agent_id=agent_id, tool_id=tool_id, enabled=True, config=data.config))
    await db.commit()
    return {"ok": True}


@router.get("/agents/{agent_id}/with-config")
async def get_agent_tools_with_config(
    agent_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get agent's enabled tools with per-agent config info and config_schema for settings UI."""
    from app.services.agent_tools import _agent_has_feishu
    has_feishu = await _agent_has_feishu(agent_id)

    all_tools_r = await db.execute(select(Tool).where(Tool.enabled == True).order_by(Tool.category, Tool.name))
    all_tools = all_tools_r.scalars().all()
    agent_tools_r = await db.execute(select(AgentTool).where(AgentTool.agent_id == agent_id))
    assignments = {str(at.tool_id): at for at in agent_tools_r.scalars().all()}

    result = []
    for t in all_tools:
        # Hide feishu tools for agents without Feishu channel
        if t.category == "feishu" and not has_feishu:
            continue
        tid = str(t.id)
        at = assignments.get(tid)
        # Agent-installed MCP tools only show for agents that have an explicit assignment.
        # Gateway tools (category=mcp-gateway) are platform-level and always visible.
        if t.type == "mcp" and t.category != "mcp-gateway" and not at:
            continue
        enabled = at.enabled if at else t.is_default
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
            "global_config": t.config or {},
            "agent_config": (at.config if at else {}) or {},
            "source": at.source if at else "system",
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

