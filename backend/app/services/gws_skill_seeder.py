"""GWS Skill Seeder - Import Google Workspace CLI skills from GitHub."""

import base64
import re
import uuid

import httpx
from loguru import logger
from sqlalchemy import select

from app.database import async_session
from app.models.skill import Skill, SkillFile


GITHUB_API = "https://api.github.com"
GWS_REPO_OWNER = "googleworkspace"
GWS_REPO_NAME = "cli"
GWS_SKILLS_PATH = "skills"

GWS_SKILL_PREFIXES = ("gws-", "persona-", "recipe-")


def is_gws_skill(folder_name: str) -> bool:
    """Check if a skill folder belongs to the GWS ecosystem.

    The Google Workspace CLI skills repo contains three categories:
    - gws-*      : core Google Workspace API skills
    - persona-*  : role-based skill bundles (exec-assistant, etc.)
    - recipe-*   : pre-built workflow recipes
    All of them require the 'gws' tool to function.
    """
    return any(folder_name.startswith(p) for p in GWS_SKILL_PREFIXES)


async def _get_github_token(tenant_id: str | None = None) -> str:
    """Resolve GitHub token from tenant settings DB."""
    if not tenant_id:
        return ""
    
    try:
        from app.models.tenant_setting import TenantSetting
        async with async_session() as db:
            result = await db.execute(
                select(TenantSetting).where(
                    TenantSetting.tenant_id == uuid.UUID(tenant_id),
                    TenantSetting.key == "github_token",
                )
            )
            setting = result.scalar_one_or_none()
            if setting and setting.value.get("token"):
                return setting.value["token"]
    except Exception:
        pass
    return ""


def _parse_skill_md_frontmatter(content: str) -> dict:
    """Extract YAML frontmatter from SKILL.md content."""
    import yaml
    match = re.match(r"^---\s*\n(.*?)\n---", content, re.DOTALL)
    if not match:
        return {}
    try:
        return yaml.safe_load(match.group(1)) or {}
    except Exception:
        return {}


async def _fetch_gws_skill_content(
    skill_name: str,
    token: str = "",
) -> str | None:
    """Fetch SKILL.md content for a specific GWS skill from GitHub."""
    url = f"{GITHUB_API}/repos/{GWS_REPO_OWNER}/{GWS_REPO_NAME}/contents/{GWS_SKILLS_PATH}/{skill_name}/SKILL.md"
    headers = {"Authorization": f"Bearer {token}"} if token else {}

    async with httpx.AsyncClient(timeout=30, headers=headers) as client:
        try:
            resp = await client.get(url)
            if resp.status_code == 200:
                data = resp.json()
                content = base64.b64decode(data["content"]).decode("utf-8")
                return content
            elif resp.status_code == 404:
                logger.warning(f"[GWS Seeder] SKILL.md not found for {skill_name}")
                return None
            elif resp.status_code == 429:
                logger.warning(f"[GWS Seeder] GitHub rate limit hit while fetching {skill_name}")
                return None
            else:
                logger.error(f"[GWS Seeder] GitHub API error for {skill_name}: {resp.status_code}")
                return None
        except Exception as e:
            logger.error(f"[GWS Seeder] Failed to fetch {skill_name}: {e}")
            return None


def _filter_gws_skills(entries: list[dict]) -> list[str]:
    """
    Filter and sort GWS skill directory names.

    Includes all three categories from the GWS CLI repo:
    - gws-*      : core Google Workspace API skills
    - persona-*  : role-based skill bundles
    - recipe-*   : pre-built workflow recipes

    Priority ordering:
    1. gws-shared (dependency for all other GWS skills)
    2. Remaining skills (alphabetical)
    """
    skill_names = []
    has_gws_shared = False

    for entry in entries:
        if entry.get("type") != "dir":
            continue
        name = entry.get("name", "")

        if name == "gws-shared":
            has_gws_shared = True
            continue

        if is_gws_skill(name):
            skill_names.append(name)

    skill_names.sort()

    if has_gws_shared:
        skill_names.insert(0, "gws-shared")

    return skill_names


async def _save_gws_skill_to_db(
    folder_name: str,
    skill_md_content: str,
    tenant_id: str | None = None,
) -> bool:
    """
    Save a GWS skill to the database.
    
    Returns:
        True if saved successfully, False if skipped (already exists or error)
    """
    # Parse frontmatter
    frontmatter = _parse_skill_md_frontmatter(skill_md_content)
    name = frontmatter.get("name", folder_name)
    description = frontmatter.get("description", "")
    
    async with async_session() as db:
        # Check for conflict (folder_name + tenant_id)
        conflict_q = select(Skill).where(Skill.folder_name == folder_name)
        if tenant_id:
            conflict_q = conflict_q.where(Skill.tenant_id == uuid.UUID(tenant_id))
        else:
            conflict_q = conflict_q.where(Skill.tenant_id.is_(None))
        
        existing = await db.execute(conflict_q)
        if existing.scalar_one_or_none():
            logger.info(f"[GWS Seeder] Skill {folder_name} already exists, skipping")
            return False
        
        # Create Skill
        skill = Skill(
            name=name,
            description=description,
            category="gws",
            icon="",
            folder_name=folder_name,
            is_builtin=True,
            tenant_id=uuid.UUID(tenant_id) if tenant_id else None,
        )
        db.add(skill)
        await db.flush()
        
        # Create SkillFile
        # PostgreSQL text columns cannot store null bytes
        clean_content = skill_md_content.replace("\x00", "")
        db.add(SkillFile(
            skill_id=skill.id,
            path="SKILL.md",
            content=clean_content,
        ))
        
        await db.commit()
        logger.info(f"[GWS Seeder] Imported GWS skill: {name}")
        return True


async def import_gws_skills(tenant_id: str | None = None) -> int:
    """
    Import GWS skills from GitHub into the skill registry.
    
    Args:
        tenant_id: Optional tenant ID for tenant-scoped import.
                   If None, creates global builtin skills (visible to all tenants).
    
    Returns:
        Number of skills imported successfully.
    """
    logger.info(f"[GWS Seeder] Starting import (tenant_id={tenant_id})")
    
    # Get GitHub token for higher rate limits
    token = await _get_github_token(tenant_id)
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    
    # Fetch directory listing
    url = f"{GITHUB_API}/repos/{GWS_REPO_OWNER}/{GWS_REPO_NAME}/contents/{GWS_SKILLS_PATH}"
    
    async with httpx.AsyncClient(timeout=30, headers=headers) as client:
        try:
            resp = await client.get(url)
            if resp.status_code == 404:
                logger.error(f"[GWS Seeder] GitHub repository path not found: {url}")
                return 0
            if resp.status_code == 429:
                logger.warning("[GWS Seeder] GitHub rate limit exceeded")
                return 0
            if resp.status_code != 200:
                logger.error(f"[GWS Seeder] GitHub API error: {resp.status_code}")
                return 0
            
            entries = resp.json()
            if isinstance(entries, dict):
                entries = [entries]
        except Exception as e:
            logger.error(f"[GWS Seeder] Failed to fetch directory: {e}")
            return 0
    
    # Filter and sort skills
    skill_names = _filter_gws_skills(entries)
    logger.info(f"[GWS Seeder] Found {len(skill_names)} GWS skills to import")
    
    # Import each skill
    imported_count = 0
    for skill_name in skill_names:
        # Fetch SKILL.md content
        content = await _fetch_gws_skill_content(skill_name, token)
        if not content:
            logger.warning(f"[GWS Seeder] Skipping {skill_name} (no SKILL.md)")
            continue
        
        # Save to database
        saved = await _save_gws_skill_to_db(skill_name, content, tenant_id)
        if saved:
            imported_count += 1
    
    logger.info(f"[GWS Seeder] Imported {imported_count}/{len(skill_names)} skills")
    return imported_count


async def ensure_gws_shared_for_agent(agent_id: str, tenant_id: str | None = None):
    """
    Ensure gws-shared skill is installed when any gws-* skill is assigned to an agent.
    
    This will be called during skill assignment flow (separate implementation).
    
    Args:
        agent_id: The agent UUID
        tenant_id: Optional tenant ID for scoping
    """
    # TODO: Implement in skill assignment flow
    # Check if agent has any gws-* skills
    # If yes, ensure gws-shared is also in the agent's skills list
    pass


async def ensure_gws_tool_for_agents_with_skills() -> int:
    """
    Startup task: scan all agents and enable the 'gws' tool for any agent
    that has gws-* skill files in its workspace but lacks the tool assignment.

    Returns:
        Number of agents that were updated.
    """
    from pathlib import Path
    from app.models.agent import Agent
    from app.config import get_settings

    settings = get_settings()
    agents_root = Path(settings.AGENT_DATA_DIR)

    if not agents_root.exists():
        return 0

    async with async_session() as db:
        agents_r = await db.execute(select(Agent))
        agents = agents_r.scalars().all()

    count = 0
    for agent in agents:
        skills_dir = agents_root / str(agent.id) / "skills"
        if not skills_dir.exists():
            continue
        has_gws = any(
            d.is_dir() and is_gws_skill(d.name)
            for d in skills_dir.iterdir()
        )
        if has_gws:
            enabled = await ensure_gws_tool_enabled_for_agent(agent.id)
            if enabled:
                count += 1

    if count > 0:
        logger.info(f"[GWS Seeder] Auto-enabled 'gws' tool for {count} agent(s) with GWS skills")
    return count


async def ensure_gws_tool_enabled_for_agent(agent_id: uuid.UUID) -> bool:
    """
    Ensure the 'gws' tool is enabled for an agent.

    When GWS skills are installed in an agent's workspace, the agent needs
    the 'gws' tool to be in its function-calling tool list so the LLM can
    actually execute GWS CLI commands.

    Returns:
        True if the tool was enabled (created or updated), False if already enabled or tool not found.
    """
    from app.models.tool import Tool, AgentTool

    async with async_session() as db:
        tool_r = await db.execute(select(Tool).where(Tool.name == "gws"))
        gws_tool = tool_r.scalar_one_or_none()
        if not gws_tool:
            logger.warning("[GWS Seeder] 'gws' tool not found in tools table, cannot auto-enable")
            return False

        at_r = await db.execute(
            select(AgentTool).where(
                AgentTool.agent_id == agent_id,
                AgentTool.tool_id == gws_tool.id,
            )
        )
        existing = at_r.scalar_one_or_none()

        if existing:
            if existing.enabled:
                return False
            existing.enabled = True
            await db.commit()
            logger.info(f"[GWS Seeder] Re-enabled 'gws' tool for agent {agent_id}")
            return True

        db.add(AgentTool(
            agent_id=agent_id,
            tool_id=gws_tool.id,
            enabled=True,
            source="system",
        ))
        await db.commit()
        logger.info(f"[GWS Seeder] Enabled 'gws' tool for agent {agent_id}")
        return True
