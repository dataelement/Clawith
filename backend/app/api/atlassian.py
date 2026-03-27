"""Atlassian Rovo MCP Channel API routes.

Provides per-agent Atlassian integration configuration.
Unlike Slack/Discord (messaging channels), Atlassian is a tool-access channel:
the agent uses Jira, Confluence, and Compass via the Atlassian Rovo MCP server.
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException
from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.permissions import check_agent_access, is_agent_creator
from app.core.security import get_current_user
from app.database import get_db
from app.models.channel_config import ChannelConfig
from app.models.user import User

router = APIRouter(tags=["atlassian"])

ATLASSIAN_MCP_URL = "https://mcp.atlassian.com/v1/mcp"


# ─── Config CRUD ────────────────────────────────────────

@router.post("/agents/{agent_id}/atlassian-channel", status_code=201)
async def configure_atlassian_channel(
    agent_id: uuid.UUID,
    data: dict,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Configure Atlassian Rovo MCP for an agent.

    Required field: api_key (Bearer token starting with ATSTT, or Basic base64(email:token)).
    Optional: cloud_id (Atlassian cloud site ID for multi-site setups).
    """
    agent, _ = await check_agent_access(db, current_user, agent_id)
    if not is_agent_creator(current_user, agent):
        raise HTTPException(status_code=403, detail="Only creator can configure channel")

    api_key = (data.get("api_key") or "").strip()
    if not api_key:
        raise HTTPException(status_code=422, detail="api_key is required")

    cloud_id = (data.get("cloud_id") or "").strip()

    from app.core.security import encrypt_data
    from app.config import get_settings
    encrypted_key = encrypt_data(api_key, get_settings().SECRET_KEY)

    result = await db.execute(
        select(ChannelConfig).where(
            ChannelConfig.agent_id == agent_id,
            ChannelConfig.channel_type == "atlassian",
        )
    )
    existing = result.scalar_one_or_none()
    if existing:
        existing.app_secret = encrypted_key
        existing.is_configured = True
        existing.extra_config = {**(existing.extra_config or {}), "cloud_id": cloud_id}
        await db.commit()
        # Sync tools for this agent in background
        import asyncio
        asyncio.create_task(_sync_atlassian_tools_for_agent(agent_id, api_key))
        return _serialize(existing)

    config = ChannelConfig(
        agent_id=agent_id,
        channel_type="atlassian",
        app_id="atlassian",
        app_secret=encrypted_key,
        is_configured=True,
        extra_config={"cloud_id": cloud_id},
    )
    db.add(config)
    await db.commit()
    await db.refresh(config)
    # Sync tools for this agent in background
    import asyncio
    asyncio.create_task(_sync_atlassian_tools_for_agent(agent_id, api_key))
    return _serialize(config)


@router.get("/agents/{agent_id}/atlassian-channel")
async def get_atlassian_channel(
    agent_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await check_agent_access(db, current_user, agent_id)
    result = await db.execute(
        select(ChannelConfig).where(
            ChannelConfig.agent_id == agent_id,
            ChannelConfig.channel_type == "atlassian",
        )
    )
    config = result.scalar_one_or_none()
    if not config:
        raise HTTPException(status_code=404, detail="Atlassian not configured")
    return _serialize(config)


@router.delete("/agents/{agent_id}/atlassian-channel", status_code=204)
async def delete_atlassian_channel(
    agent_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    agent, _ = await check_agent_access(db, current_user, agent_id)
    if not is_agent_creator(current_user, agent):
        raise HTTPException(status_code=403, detail="Only creator can remove channel")
    result = await db.execute(
        select(ChannelConfig).where(
            ChannelConfig.agent_id == agent_id,
            ChannelConfig.channel_type == "atlassian",
        )
    )
    config = result.scalar_one_or_none()
    if not config:
        raise HTTPException(status_code=404, detail="Atlassian not configured")
    await db.delete(config)
    await db.commit()


@router.post("/agents/{agent_id}/atlassian-channel/test")
async def test_atlassian_channel(
    agent_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Test connectivity to Atlassian Rovo MCP and list available tools."""
    await check_agent_access(db, current_user, agent_id)
    result = await db.execute(
        select(ChannelConfig).where(
            ChannelConfig.agent_id == agent_id,
            ChannelConfig.channel_type == "atlassian",
        )
    )
    config = result.scalar_one_or_none()
    if not config or not config.app_secret:
        raise HTTPException(status_code=400, detail="Atlassian not configured")

    from app.services.mcp_client import MCPClient
    try:
        client = MCPClient(ATLASSIAN_MCP_URL, api_key=config.app_secret)
        tools = await client.list_tools()
        return {
            "ok": True,
            "tool_count": len(tools),
            "tools": [{"name": t["name"], "description": t.get("description", "")[:100]} for t in tools[:10]],
            "message": f"✅ Connected to Atlassian Rovo MCP — {len(tools)} tools available",
        }
    except Exception as e:
        return {"ok": False, "error": str(e)[:300]}


# ─── Internal helper ────────────────────────────────────

def _serialize(config: ChannelConfig) -> dict:
    return {
        "id": str(config.id),
        "agent_id": str(config.agent_id),
        "channel_type": config.channel_type,
        "is_configured": config.is_configured,
        "is_connected": config.is_connected,
        "cloud_id": (config.extra_config or {}).get("cloud_id", ""),
        "extra_config": config.extra_config or {},
        "created_at": config.created_at.isoformat() if config.created_at else None,
    }


# ─── Utility for internal use ──────────────────────────

async def _sync_atlassian_tools_for_agent(agent_id: uuid.UUID, api_key: str) -> None:
    """Connect to Atlassian Rovo MCP and ensure all tools are seeded + assigned to this agent.

    Discovers tools from the MCP server, creates Tool records if needed,
    and creates AgentTool assignments for this specific agent.
    """
    from app.services.mcp_client import MCPClient
    from app.models.tool import Tool, AgentTool
    from app.database import async_session
    from sqlalchemy import select as sa_select

    logger.info(f"[AtlassianChannel] Syncing tools for agent {agent_id} ...")
    try:
        client = MCPClient(ATLASSIAN_MCP_URL, api_key=api_key)
        tools_discovered = await client.list_tools()
    except Exception as e:
        logger.error(f"[AtlassianChannel] Could not list tools: {e}")
        return

    if not tools_discovered:
        logger.warning("[AtlassianChannel] No tools returned from Atlassian MCP")
        return

    logger.info(f"[AtlassianChannel] Found {len(tools_discovered)} tools, assigning to agent {agent_id}")

    async with async_session() as db:
        assigned = 0
        for mcp_tool in tools_discovered:
            raw_name = mcp_tool.get("name", "")
            if not raw_name:
                continue

            tool_name = f"atlassian_rovo_{raw_name}"
            tool_desc = mcp_tool.get("description", "")[:500]
            tool_schema = mcp_tool.get("inputSchema", {"type": "object", "properties": {}})

            if "jira" in raw_name.lower() or "issue" in raw_name.lower():
                icon = "🔵"
            elif "confluence" in raw_name.lower() or "page" in raw_name.lower():
                icon = "📘"
            elif "compass" in raw_name.lower() or "component" in raw_name.lower():
                icon = "🧭"
            else:
                icon = "🔷"

            # Ensure Tool record exists (shared across all agents)
            tool_r = await db.execute(sa_select(Tool).where(Tool.name == tool_name))
            tool = tool_r.scalar_one_or_none()
            if not tool:
                tool = Tool(
                    name=tool_name,
                    display_name=f"Atlassian: {raw_name}",
                    description=tool_desc,
                    type="mcp",
                    category="atlassian",
                    icon=icon,
                    parameters_schema=tool_schema,
                    mcp_server_url=ATLASSIAN_MCP_URL,
                    mcp_server_name="Atlassian Rovo",
                    mcp_tool_name=raw_name,
                    enabled=True,
                    is_default=False,
                )
                db.add(tool)
                await db.flush()
            else:
                # Update schema in case it changed
                tool.description = tool_desc
                tool.parameters_schema = tool_schema

            # Assign to this specific agent (api_key stored per-agent via channel config,
            # but we also put it in AgentTool.config as fallback for _execute_mcp_tool)
            at_r = await db.execute(
                sa_select(AgentTool).where(
                    AgentTool.agent_id == agent_id,
                    AgentTool.tool_id == tool.id,
                )
            )
            at = at_r.scalar_one_or_none()
            if at:
                at.enabled = True
                at.config = {"api_key": api_key}
            else:
                db.add(AgentTool(
                    agent_id=agent_id,
                    tool_id=tool.id,
                    enabled=True,
                    source="user_installed",
                    installed_by_agent_id=agent_id,
                    config={"api_key": api_key},
                ))
                assigned += 1

        await db.commit()
    logger.info(f"[AtlassianChannel] {assigned} new tool assignments for agent {agent_id}")


async def get_atlassian_api_key_for_agent(agent_id: uuid.UUID, db=None) -> str | None:
    """Return the configured Atlassian API key for the given agent, or None."""
    from app.database import async_session

    async def _fetch(session):
        from app.core.security import decrypt_data
        from app.config import get_settings
        result = await session.execute(
            select(ChannelConfig).where(
                ChannelConfig.agent_id == agent_id,
                ChannelConfig.channel_type == "atlassian",
                ChannelConfig.is_configured == True,
            )
        )
        config = result.scalar_one_or_none()
        if not config or not config.app_secret:
            return None
        
        try:
            return decrypt_data(config.app_secret, get_settings().SECRET_KEY)
        except Exception:
            return config.app_secret

    if db is not None:
        return await _fetch(db)
    async with async_session() as session:
        return await _fetch(session)
