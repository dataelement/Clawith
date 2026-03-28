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


# Sensitive field keys that should be encrypted when stored
SENSITIVE_FIELD_KEYS = {"api_key", "private_key", "auth_code", "password", "secret"}


def _encrypt_sensitive_fields(config: dict) -> dict:
    """Encrypt sensitive fields in config dict.

    Args:
        config: Tool config dict

    Returns:
        Config dict with sensitive fields encrypted
    """
    from app.core.security import encrypt_data
    from app.config import get_settings

    if not config:
        return config

    settings = get_settings()
    result = dict(config)

    for key in SENSITIVE_FIELD_KEYS:
        if key in result and result[key]:
            # Only encrypt if not already encrypted (check if it looks like base64)
            value = result[key]
            if isinstance(value, str) and value:
                try:
                    result[key] = encrypt_data(value, settings.SECRET_KEY)
                except Exception:
                    # If encryption fails, keep the value as-is
                    pass

    return result


def _decrypt_sensitive_fields(config: dict) -> dict:
    """Decrypt sensitive fields in config dict.

    Args:
        config: Tool config dict

    Returns:
        Config dict with sensitive fields decrypted
    """
    from app.core.security import decrypt_data
    from app.config import get_settings

    if not config:
        return config

    settings = get_settings()
    result = dict(config)

    for key in SENSITIVE_FIELD_KEYS:
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
    from sqlalchemy import or_ as _or, and_ as _and
    # Exclude tools that were installed by agents via import_mcp_server
    agent_installed_tids = select(AgentTool.tool_id).where(AgentTool.source == "user_installed")
    # Also exclude orphaned MCP tools (no AgentTool records, no tenant_id)
    # These can appear when admin deletes agent-tool assignments but Tool records remain
    all_assigned_tids = select(AgentTool.tool_id).distinct()
    orphaned_mcp = _and(Tool.type == "mcp", Tool.tenant_id == None, ~Tool.id.in_(all_assigned_tids))
    query = (
        select(Tool)
        .where(~Tool.id.in_(agent_installed_tids))
        .where(~orphaned_mcp)
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

    update_data = data.model_dump(exclude_unset=True)
    # Encrypt sensitive fields in config
    if "config" in update_data and update_data["config"]:
        update_data["config"] = _encrypt_sensitive_fields(update_data["config"])

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
        # MCP tools installed by agents (no tenant_id) only show for that agent.
        # MCP tools imported by admin in company settings (tenant_id set) show for all agents (default disabled).
        if t.type == "mcp" and not at:
            if not t.tenant_id:
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
    # Check permission: only platform_admin and org_admin can modify allow_network
    if "allow_network" in data.config:
        if current_user.role not in ("platform_admin", "org_admin"):
            raise HTTPException(
                status_code=403,
                detail="Only platform admin or organization admin can modify network access settings"
            )

    # Encrypt sensitive fields
    encrypted_config = _encrypt_sensitive_fields(data.config)

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
        # MCP tools installed by agents (no tenant_id) only show for that agent.
        # MCP tools imported by admin in company settings (tenant_id set) show for all agents (default disabled).
        if t.type == "mcp" and not at:
            if not t.tenant_id:
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
# ─── Tool Category Sharing Config (Generic ChannelConfig) ───

@router.get("/agents/{agent_id}/category-config/{category}")
async def get_category_config(
    agent_id: uuid.UUID,
    category: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get shared configuration for a tool category (stored in ChannelConfig)."""
    from app.core.permissions import check_agent_access
    from app.models.channel_config import ChannelConfig

    await check_agent_access(db, current_user, agent_id)
    result = await db.execute(
        select(ChannelConfig).where(
            ChannelConfig.agent_id == agent_id,
            ChannelConfig.channel_type == category,
        )
    )
    config = result.scalar_one_or_none()
    
    config_id = None
    is_configured = False
    decrypted_config = {}
    
    if config:
        config_id = str(config.id)
        is_configured = config.is_configured
        
        # If it's encrypted, decrypt it for the UI
        full_config = {
            "api_key": config.app_secret,
            **(config.extra_config or {})
        }
        decrypted_config = _decrypt_sensitive_fields(full_config)
    else:
        # Fallback to global Tool.config for this category (Company Settings)
        from app.models.tool import Tool
        tool_result = await db.execute(
            select(Tool).where(
                Tool.category == category,
                Tool.enabled == True,
            ).limit(1)
        )
        global_tool = tool_result.scalar_one_or_none()
        if global_tool and global_tool.config:
            decrypted_config = _decrypt_sensitive_fields(global_tool.config)

    return {
        "id": config_id,
        "agent_id": str(agent_id),
        "category": category,
        "is_configured": is_configured,
        "config": decrypted_config
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
