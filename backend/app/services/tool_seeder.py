"""Seed builtin tools into the database on startup."""

from loguru import logger
from sqlalchemy import select
from app.dao import query_dao
from app.models.tenant import Tenant
from app.models.tenant_setting import TenantSetting
from app.models.tool import Tool
from app.services.builtin_tool_definitions import BUILTIN_TOOL_SEEDS
from app.services.tool_config import meaningful_config, tenant_tool_config_key

SYNC_IS_DEFAULT_TOOL_NAMES = {
    "read_webpage",
    "duckduckgo_search",
    "jina_search",
    "jina_read",
    "update_objective",
    # AgentBay tools should NOT be is_default=True. Older seeder versions may
    # have set them to True; include them here so the seeder corrects the DB.
    "agentbay_browser_navigate",
    "agentbay_browser_screenshot",
    "agentbay_browser_save_screenshot",
    "agentbay_browser_click",
    "agentbay_browser_type",
    "agentbay_browser_extract",
    "agentbay_browser_observe",
    "agentbay_browser_login",
    "agentbay_code_execute",
    "agentbay_code_write_file",
    "agentbay_code_read_file",
    "agentbay_code_edit_file",
    "agentbay_command_exec",
    "agentbay_computer_screenshot",
    "agentbay_computer_save_screenshot",
    "agentbay_computer_click",
    "agentbay_computer_precision_screenshot",
    "agentbay_computer_input_text",
    "agentbay_computer_press_keys",
    "agentbay_computer_scroll",
    "agentbay_computer_move_mouse",
    "agentbay_computer_drag_mouse",
    "agentbay_computer_get_installed_apps",
    "agentbay_computer_start_app",
    "agentbay_computer_list_windows",
    "agentbay_computer_close_window",
    "agentbay_computer_dismiss_dialog",
    "agentbay_file_transfer",
}

LEGACY_IMAGE_TOOL_MODEL_DEFAULTS = {
    "generate_image_siliconflow": "black-forest-labs/FLUX.1-schnell",
    "generate_image_openai": "dall-e-3",
    "generate_image_google": "gemini-2.5-flash-image",
}


def _global_builtin_config(tool_data: dict) -> dict:
    """Return config safe to store on the global builtin Tool row."""
    # Builtin tools specify defaults (like 'allow_network': True) in their 'config' dict.
    # The actual sensitive data defaults are empty strings ("") so this is safe to store globally.
    return tool_data.get("config", {})

# Compatibility export for UI/tests. The canonical module owns every builtin
# name, description, schema, and execution policy.
BUILTIN_TOOLS = BUILTIN_TOOL_SEEDS


async def seed_builtin_tools():
    """Insert or update builtin tools in the database."""
    from app.models.tool import AgentTool
    from app.models.agent import Agent


    async with query_dao.session() as db:
        # Legacy rename: older environments persisted this tool as
        # `send_web_message`. Rename or merge it in-place so agents keep the
        # same assignment after the first startup on the new version.
        old_name = "send_web_message"
        new_name = "send_platform_message"
        old_result = await query_dao.execute(db, select(Tool).where(Tool.name == old_name))
        old_tool = old_result.scalar_one_or_none()
        new_result = await query_dao.execute(db, select(Tool).where(Tool.name == new_name))
        new_tool = new_result.scalar_one_or_none()
        if old_tool and not new_tool:
            old_tool.name = new_name
            logger.info(f"[ToolSeeder] Renamed builtin tool: {old_name} -> {new_name}")
        elif old_tool and new_tool:
            old_assignments = await query_dao.execute(db, select(AgentTool).where(AgentTool.tool_id == old_tool.id))
            for assignment in old_assignments.scalars().all():
                existing_assignment = await query_dao.execute(db, 
                    select(AgentTool).where(
                        AgentTool.agent_id == assignment.agent_id,
                        AgentTool.tool_id == new_tool.id,
                    )
                )
                if not existing_assignment.scalar_one_or_none():
                    assignment.tool_id = new_tool.id
            await query_dao.delete(db, old_tool)
            logger.info(f"[ToolSeeder] Merged legacy builtin tool into {new_name}")

        new_tool_ids = []
        for t in BUILTIN_TOOL_SEEDS:
            seed_config = _global_builtin_config(t)
            result = await query_dao.execute(db, select(Tool).where(Tool.name == t["name"]))
            existing = result.scalar_one_or_none()
            if not existing:
                tool = Tool(
                    name=t["name"],
                    display_name=t["display_name"],
                    description=t["description"],
                    type="builtin",
                    category=t["category"],
                    icon=t["icon"],
                    is_default=t["is_default"],
                    parameters_schema=t.get("parameters_schema", {"type": "object", "properties": {}}),
                    config=seed_config,
                    config_schema=t.get("config_schema", {}),
                    source="builtin",
                )
                query_dao.add(db, tool)
                await query_dao.flush(db)  # get tool.id
                if t["is_default"]:
                    new_tool_ids.append(tool.id)
                logger.info(f"[ToolSeeder] Created builtin tool: {t['name']}")
            else:
                # Sync fields that may evolve
                updated_fields = []
                if existing.category != t["category"]:
                    existing.category = t["category"]
                    updated_fields.append("category")
                if existing.description != t["description"]:
                    existing.description = t["description"]
                    updated_fields.append("description")
                if existing.display_name != t["display_name"]:
                    existing.display_name = t["display_name"]
                    updated_fields.append("display_name")
                if existing.icon != t["icon"]:
                    existing.icon = t["icon"]
                    updated_fields.append("icon")
                if t["name"] in SYNC_IS_DEFAULT_TOOL_NAMES and existing.is_default != t["is_default"]:
                    existing.is_default = t["is_default"]
                    updated_fields.append("is_default")
                if t.get("config_schema") and existing.config_schema != t["config_schema"]:
                    existing.config_schema = t["config_schema"]
                    updated_fields.append("config_schema")
                    # Merge new config defaults when config_schema changes
                    if seed_config:
                        existing.config = {**seed_config, **(existing.config or {})}
                        updated_fields.append("config")
                if not existing.config and seed_config:
                    existing.config = seed_config
                    updated_fields.append("config")
                elif seed_config and existing.config != seed_config:
                    # Merge new config keys into existing config so that flags like
                    # okr_agent_only are propagated to already-created tool records.
                    # Existing keys take precedence (agent-specific overrides are preserved).
                    merged = {**seed_config, **(existing.config or {})}
                    if merged != existing.config:
                        existing.config = merged
                        updated_fields.append("config")
                legacy_model = LEGACY_IMAGE_TOOL_MODEL_DEFAULTS.get(t["name"])
                if legacy_model and existing.config == {
                    "model": legacy_model,
                    "api_key": "",
                    "base_url": "",
                }:
                    existing.config = {
                        "model": "",
                        "api_key": "",
                        "base_url": "",
                    }
                    updated_fields.append("config")
                if existing.parameters_schema != t["parameters_schema"]:
                    existing.parameters_schema = t["parameters_schema"]
                    updated_fields.append("parameters_schema")
                if updated_fields:
                    logger.info(f"[ToolSeeder] Updated {', '.join(updated_fields)}: {t['name']}")

        # Auto-assign new default tools to all existing agents
        if new_tool_ids:
            agents_result = await query_dao.execute(db, select(Agent.id))
            agent_ids = [row[0] for row in agents_result.fetchall()]
            for agent_id in agent_ids:
                for tool_id in new_tool_ids:
                    # Check if already assigned
                    check = await query_dao.execute(db, 
                        select(AgentTool).where(
                            AgentTool.agent_id == agent_id,
                            AgentTool.tool_id == tool_id,
                        )
                    )
                    if not check.scalar_one_or_none():
                        query_dao.add(db, AgentTool(agent_id=agent_id, tool_id=tool_id, enabled=True))
            logger.info(f"[ToolSeeder] Auto-assigned {len(new_tool_ids)} new tools to {len(agent_ids)} agents")

        # AgentBay desktop window helpers are non-default tools, but should be
        # available wherever the user has already enabled Cloud Desktop tools.
        computer_anchor_names = [
            "agentbay_computer_screenshot",
            "agentbay_computer_precision_screenshot",
            "agentbay_computer_click",
            "agentbay_computer_get_active_window",
            "agentbay_computer_activate_window",
        ]
        computer_helper_names = [
            "agentbay_computer_precision_screenshot",
            "agentbay_computer_save_screenshot",
            "agentbay_computer_list_windows",
            "agentbay_computer_close_window",
            "agentbay_computer_dismiss_dialog",
        ]
        anchor_tools_r = await query_dao.execute(db, select(Tool.id).where(Tool.name.in_(computer_anchor_names)))
        anchor_tool_ids = [row[0] for row in anchor_tools_r.fetchall()]
        helper_tools_r = await query_dao.execute(db, select(Tool).where(Tool.name.in_(computer_helper_names)))
        helper_tools = helper_tools_r.scalars().all()
        if anchor_tool_ids and helper_tools:
            enabled_agent_r = await query_dao.execute(db, 
                select(AgentTool.agent_id)
                .where(AgentTool.tool_id.in_(anchor_tool_ids), AgentTool.enabled == True)  # noqa: E712
                .distinct()
            )
            enabled_agent_ids = [row[0] for row in enabled_agent_r.fetchall()]
            assigned_count = 0
            for agent_id in enabled_agent_ids:
                for helper_tool in helper_tools:
                    existing_assignment = await query_dao.execute(db, 
                        select(AgentTool).where(
                            AgentTool.agent_id == agent_id,
                            AgentTool.tool_id == helper_tool.id,
                        )
                    )
                    if not existing_assignment.scalar_one_or_none():
                        query_dao.add(db, AgentTool(agent_id=agent_id, tool_id=helper_tool.id, enabled=True))
                        assigned_count += 1
            if assigned_count:
                logger.info(
                    f"[ToolSeeder] Auto-assigned {assigned_count} AgentBay computer helper tool(s) "
                    f"to {len(enabled_agent_ids)} agent(s)"
                )

        # Save-screenshot is non-default, but should be available wherever the
        # user has enabled the AgentBay browser screenshot tool.
        browser_anchor_names = [
            "agentbay_browser_navigate",
            "agentbay_browser_screenshot",
        ]
        browser_helper_names = ["agentbay_browser_save_screenshot"]
        browser_anchor_tools_r = await query_dao.execute(db, select(Tool.id).where(Tool.name.in_(browser_anchor_names)))
        browser_anchor_tool_ids = [row[0] for row in browser_anchor_tools_r.fetchall()]
        browser_helper_tools_r = await query_dao.execute(db, select(Tool).where(Tool.name.in_(browser_helper_names)))
        browser_helper_tools = browser_helper_tools_r.scalars().all()
        if browser_anchor_tool_ids and browser_helper_tools:
            browser_enabled_agent_r = await query_dao.execute(db, 
                select(AgentTool.agent_id)
                .where(AgentTool.tool_id.in_(browser_anchor_tool_ids), AgentTool.enabled == True)  # noqa: E712
                .distinct()
            )
            browser_enabled_agent_ids = [row[0] for row in browser_enabled_agent_r.fetchall()]
            browser_assigned_count = 0
            for agent_id in browser_enabled_agent_ids:
                for helper_tool in browser_helper_tools:
                    existing_assignment = await query_dao.execute(db, 
                        select(AgentTool).where(
                            AgentTool.agent_id == agent_id,
                            AgentTool.tool_id == helper_tool.id,
                        )
                    )
                    if not existing_assignment.scalar_one_or_none():
                        query_dao.add(db, AgentTool(agent_id=agent_id, tool_id=helper_tool.id, enabled=True))
                        browser_assigned_count += 1
            if browser_assigned_count:
                logger.info(
                    f"[ToolSeeder] Auto-assigned {browser_assigned_count} AgentBay browser helper tool(s) "
                    f"to {len(browser_enabled_agent_ids)} agent(s)"
                )

        # Code sandbox file helpers are non-default, but should be available
        # wherever the user has already enabled AgentBay code execution tools.
        code_anchor_names = [
            "agentbay_code_execute",
            "agentbay_command_exec",
            "agentbay_file_transfer",
        ]
        code_helper_names = [
            "agentbay_code_write_file",
            "agentbay_code_read_file",
            "agentbay_code_edit_file",
        ]
        code_anchor_tools_r = await query_dao.execute(db, select(Tool.id).where(Tool.name.in_(code_anchor_names)))
        code_anchor_tool_ids = [row[0] for row in code_anchor_tools_r.fetchall()]
        code_helper_tools_r = await query_dao.execute(db, select(Tool).where(Tool.name.in_(code_helper_names)))
        code_helper_tools = code_helper_tools_r.scalars().all()
        if code_anchor_tool_ids and code_helper_tools:
            code_enabled_agent_r = await query_dao.execute(db, 
                select(AgentTool.agent_id)
                .where(AgentTool.tool_id.in_(code_anchor_tool_ids), AgentTool.enabled == True)  # noqa: E712
                .distinct()
            )
            code_enabled_agent_ids = [row[0] for row in code_enabled_agent_r.fetchall()]
            code_assigned_count = 0
            for agent_id in code_enabled_agent_ids:
                for helper_tool in code_helper_tools:
                    existing_assignment = await query_dao.execute(db, 
                        select(AgentTool).where(
                            AgentTool.agent_id == agent_id,
                            AgentTool.tool_id == helper_tool.id,
                        )
                    )
                    if not existing_assignment.scalar_one_or_none():
                        query_dao.add(db, AgentTool(agent_id=agent_id, tool_id=helper_tool.id, enabled=True))
                        code_assigned_count += 1
            if code_assigned_count:
                logger.info(
                    f"[ToolSeeder] Auto-assigned {code_assigned_count} AgentBay code file helper tool(s) "
                    f"to {len(code_enabled_agent_ids)} agent(s)"
                )

        OBSOLETE_TOOLS = ["bing_search", "manage_tasks"]
        for obsolete_name in OBSOLETE_TOOLS:
            result = await query_dao.execute(db, select(Tool).where(Tool.name == obsolete_name))
            obsolete = result.scalar_one_or_none()
            if obsolete:
                await query_dao.delete(db, obsolete)
                logger.info(f"[ToolSeeder] Removed obsolete tool: {obsolete_name}")

        # Legacy deployments stored company credentials for builtin tools in
        # the global tools.config row. Move those values into the first tenant's
        # tenant_settings once, then clear the global row so new companies do
        # not inherit another company's keys.
        first_tenant_r = await query_dao.execute(db, select(Tenant).order_by(Tenant.created_at).limit(1))
        first_tenant = first_tenant_r.scalar_one_or_none()
        if first_tenant:
            builtin_config_tools_r = await query_dao.execute(db, select(Tool).where(Tool.source == "builtin"))
            migrated = 0
            for tool in builtin_config_tools_r.scalars().all():
                if not (tool.config_schema or {}).get("fields"):
                    continue
                legacy_config = meaningful_config(tool.config or {})
                if not legacy_config:
                    continue
                setting_key = tenant_tool_config_key(tool.name)
                existing_setting_r = await query_dao.execute(db, 
                    select(TenantSetting).where(
                        TenantSetting.tenant_id == first_tenant.id,
                        TenantSetting.key == setting_key,
                    )
                )
                if not existing_setting_r.scalar_one_or_none():
                    query_dao.add(db, TenantSetting(
                        tenant_id=first_tenant.id,
                        key=setting_key,
                        value={"config": legacy_config},
                    ))
                    migrated += 1
                
                # Remove sensitive fields from global config instead of wiping it
                clean_config = {}
                schema_fields = (tool.config_schema or {}).get("fields", [])
                sensitive_keys = {f["key"] for f in schema_fields if f.get("type") == "password"}
                for k, v in (tool.config or {}).items():
                    if k not in sensitive_keys:
                        clean_config[k] = v
                tool.config = clean_config
            if migrated:
                logger.info(
                    f"[ToolSeeder] Migrated {migrated} legacy builtin tool config(s) "
                    f"to tenant_settings for tenant {first_tenant.id}"
                )

        await query_dao.commit(db)
        logger.info("[ToolSeeder] Builtin tools seeded")


async def clean_orphaned_mcp_tools():
    """Clean up orphan MCP tools that lost all their AgentTool assignments.
    
    This happens when an Agent is deleted (cascade deletes AgentTool) but the
    shared Tool record remains. We run this periodically/on-startup to prevent
    the database from filling up with abandoned tool records.
    """
    from app.models.tool import AgentTool
    from sqlalchemy import and_, delete
    
    async with query_dao.session() as db:
        # 1. Get all currently assigned tool IDs
        all_assigned_r = await query_dao.execute(db, select(AgentTool.tool_id).distinct())
        assigned_ids = [row[0] for row in all_assigned_r.fetchall()]
        
        # 2. Delete MCP tools that have NO tenant_id AND are NOT in the assigned list
        # tenant_id == None ensures we don't delete Global Tools manually added by company admins
        stmt = delete(Tool).where(
            and_(
                Tool.type == "mcp",
                Tool.tenant_id.is_(None),
                ~Tool.id.in_(assigned_ids) if assigned_ids else True
            )
        )
        result = await query_dao.execute(db, stmt)
        deleted_count = result.rowcount
        await query_dao.commit(db)
        
        if deleted_count > 0:
            logger.info(f"[ToolSeeder] Cleaned up {deleted_count} orphaned MCP tools")

# ── Atlassian Rovo MCP Server Integration ──────────────────────────────────

ATLASSIAN_ROVO_MCP_URL = "https://mcp.atlassian.com/v1/mcp"

ATLASSIAN_ROVO_CONFIG_TOOL = {
    "name": "atlassian_rovo",
    "display_name": "Atlassian Rovo (Jira / Confluence / Compass)",
    "description": (
        "Connect to Atlassian Rovo MCP Server to access Jira, Confluence, and Compass. "
        "Configure your API key to enable Jira issue management, Confluence page creation, "
        "and Compass component queries."
    ),
    "category": "atlassian",
    "icon": "🔷",
    "is_default": False,
    "parameters_schema": {"type": "object", "properties": {}},
    "config": {"api_key": ""},
    "config_schema": {
        "fields": [
            {
                "key": "api_key",
                "label": "Atlassian API Key",
                "type": "password",
                "default": "",
                "placeholder": "ATSTT3x... (service account key) or Basic base64(email:token)",
                "description": (
                    "Service account API key (Bearer) or base64-encoded email:api_token (Basic). "
                    "Get your API key from id.atlassian.com/manage-profile/security/api-tokens"
                ),
            },
        ]
    },
}


async def seed_atlassian_rovo_config():
    """Ensure the Atlassian Rovo platform config tool exists in the database.

    If the env var ATLASSIAN_API_KEY is set, it will be written into the tool config
    so the platform is immediately ready without manual UI setup.
    """
    import os
    env_key = os.environ.get("ATLASSIAN_API_KEY", "").strip()

    async with query_dao.session() as db:
        t = ATLASSIAN_ROVO_CONFIG_TOOL
        result = await query_dao.execute(db, select(Tool).where(Tool.name == t["name"]))
        existing = result.scalar_one_or_none()
        if not existing:
            initial_config = dict(t["config"])
            if env_key:
                initial_config["api_key"] = env_key
            tool = Tool(
                name=t["name"],
                display_name=t["display_name"],
                description=t["description"],
                type="mcp_config",
                category=t["category"],
                icon=t["icon"],
                is_default=t["is_default"],
                parameters_schema=t["parameters_schema"],
                config=initial_config,
                config_schema=t["config_schema"],
                mcp_server_url=ATLASSIAN_ROVO_MCP_URL,
                mcp_server_name="Atlassian Rovo",
                source="admin",
            )
            query_dao.add(db, tool)
            await query_dao.commit(db)
            logger.info("[ToolSeeder] Created Atlassian Rovo config tool")
        else:
            updated = False
            if existing.config_schema != t["config_schema"]:
                existing.config_schema = t["config_schema"]
                updated = True
            if existing.mcp_server_url != ATLASSIAN_ROVO_MCP_URL:
                existing.mcp_server_url = ATLASSIAN_ROVO_MCP_URL
                updated = True
            # Write env key into DB if not already stored
            if env_key and (not existing.config or not existing.config.get("api_key")):
                existing.config = {**(existing.config or {}), "api_key": env_key}
                updated = True
            if updated:
                await query_dao.commit(db)
                logger.info("[ToolSeeder] Updated Atlassian Rovo config tool")


async def get_atlassian_api_key() -> str:
    """Read the Atlassian API key from the platform config tool."""
    async with query_dao.session() as db:
        result = await query_dao.execute(db, select(Tool).where(Tool.name == "atlassian_rovo"))
        tool = result.scalar_one_or_none()
        if tool and tool.config:
            return tool.config.get("api_key", "")
    return ""
