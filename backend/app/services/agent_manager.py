"""Agent lifecycle manager — Docker container management for OpenClaw Gateway instances."""

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

import docker
from docker.errors import DockerException, NotFound
from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models.agent import Agent, AgentTemplate
from app.models.llm import LLMModel
from app.services.llm import get_model_api_key
from app.services.llm.model_resolution import resolve_active_agent_model
from app.services.storage import get_storage_backend, normalize_storage_key

settings = get_settings()


def _render_soul_template(
    template_content: str | None,
    *,
    agent_name: str,
    creator_name: str,
    created_at: str,
) -> str:
    """Render Soul-owned fields without promoting product role metadata."""
    if not template_content:
        return "# Soul\n\n_Describe your role and responsibilities._\n"
    # D-017 keeps `role_description` as product/directory metadata. Remove any
    # legacy template line that would silently copy it into the authoritative
    # Soul identity before substituting Soul-owned fields.
    without_role_placeholder = "\n".join(
        line
        for line in template_content.splitlines()
        if "{{role_description}}" not in line
    )
    return (
        without_role_placeholder
        .replace("{{agent_name}}", agent_name)
        .replace("{name}", agent_name)
        .replace("{{creator_name}}", creator_name)
        .replace("{{created_at}}", created_at)
    )


class AgentManager:
    """Manage OpenClaw Gateway Docker containers for digital employees."""

    def __init__(self):
        try:
            self.docker_client = docker.from_env()
        except DockerException:
            logger.warning("Docker not available — agent containers will not be managed")
            self.docker_client = None

    def _agent_dir(self, agent_id: uuid.UUID) -> Path:
        local_root = settings.STORAGE_LOCAL_ROOT or settings.AGENT_DATA_DIR
        return Path(local_root) / str(agent_id)

    def _agent_storage_prefix(self, agent_id: uuid.UUID) -> str:
        return normalize_storage_key(str(agent_id))

    def _template_dir(self) -> Path:
        return Path(settings.AGENT_TEMPLATE_DIR)

    async def _materialize_agent_dir(self, agent_id: uuid.UUID) -> Path:
        """Create a local working tree from shared storage for container mounting."""
        agent_dir = self._agent_dir(agent_id)
        storage = get_storage_backend()
        agent_prefix = self._agent_storage_prefix(agent_id)
        agent_dir.mkdir(parents=True, exist_ok=True)
        if not await storage.exists(agent_prefix) and not await storage.is_dir(agent_prefix):
            return agent_dir
        for entry in await storage.list_dir(agent_prefix):
            await self._materialize_entry(storage, entry.key, agent_dir)
        return agent_dir

    async def _materialize_entry(self, storage, storage_key: str, local_root: Path) -> None:
        rel = Path(storage_key).relative_to(Path(storage_key).parts[0]).as_posix()
        local_path = local_root / rel
        if await storage.is_dir(storage_key):
            local_path.mkdir(parents=True, exist_ok=True)
            for child in await storage.list_dir(storage_key):
                await self._materialize_entry(storage, child.key, local_root)
            return
        local_path.parent.mkdir(parents=True, exist_ok=True)
        local_path.write_bytes(await storage.read_bytes(storage_key))

    async def initialize_agent_files(self, db: AsyncSession, agent: Agent,
                                      personality: str = "", boundaries: str = "") -> None:
        """Copy template files and customize for this agent."""
        agent_dir = self._agent_dir(agent.id)
        template_dir = self._template_dir()
        storage = get_storage_backend()
        agent_prefix = self._agent_storage_prefix(agent.id)

        if await storage.exists(agent_prefix) or await storage.is_dir(agent_prefix):
            logger.warning(f"Agent dir already exists: {agent_dir}")
            return

        if template_dir.exists():
            import asyncio
            import time
            t_start_files = time.perf_counter()
            tasks = []
            for src in template_dir.rglob("*"):
                if src.is_dir():
                    continue
                rel = src.relative_to(template_dir).as_posix()
                if rel == "tasks.json" or rel == "todo.json" or rel.startswith("enterprise_info/"):
                    continue
                tasks.append(
                    storage.write_bytes(
                        f"{agent_prefix}/{rel}",
                        src.read_bytes(),
                    )
                )
            if tasks:
                await asyncio.gather(*tasks)
            logger.info(f"[AgentManager] Uploaded {len(tasks)} template files concurrently in {time.perf_counter() - t_start_files:.2f}s for agent {agent.id}")
        else:
            logger.info(f"Template dir not found ({template_dir}), creating minimal workspace")
            await storage.write_text(f"{agent_prefix}/tasks.json", "[]", encoding="utf-8")
            await storage.write_text(f"{agent_prefix}/tasks.json", "[]", encoding="utf-8")
            for placeholder in (
                "workspace/.gitkeep",
                "workspace/knowledge_base/.gitkeep",
                "memory/.gitkeep",
                "skills/.gitkeep",
            ):
                await storage.write_text(f"{agent_prefix}/{placeholder}", "", encoding="utf-8")

        # Customize soul.md
        # Get creator name
        from app.models.user import User
        result = await db.execute(select(User).where(User.id == agent.creator_id))
        creator = result.scalar_one_or_none()
        creator_name = creator.display_name if creator else "Unknown"

        soul_key = f"{agent_prefix}/soul.md"
        template_content = None
        if agent.template_id is not None:
            template_result = await db.execute(
                select(AgentTemplate.soul_template).where(
                    AgentTemplate.id == agent.template_id
                )
            )
            selected_soul = template_result.scalar_one_or_none()
            if isinstance(selected_soul, str) and selected_soul.strip():
                template_content = selected_soul
        if template_content is None and await storage.exists(soul_key):
            template_content = await storage.read_text(soul_key, encoding="utf-8", errors="replace")
        soul_content = _render_soul_template(
            template_content,
            agent_name=agent.name,
            creator_name=creator_name,
            created_at=datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        )

        # Helper function to replace or append sections
        def replace_or_append_section(content: str, section_name: str, section_content: str) -> str:
            """Replace existing ## SectionName or append if not found."""
            if not section_content:
                return content
            
            # Pattern to match existing section (case-insensitive header)
            import re
            pattern = rf"^##\s+{re.escape(section_name)}\s*$"
            lines = content.split('\n')
            
            # Find the section header
            for i, line in enumerate(lines):
                if re.match(pattern, line.strip(), re.IGNORECASE):
                    # Found existing section - replace until next ## header or end
                    section_start = i
                    section_end = len(lines)
                    for j in range(i + 1, len(lines)):
                        if lines[j].strip().startswith('## '):
                            section_end = j
                            break
                    
                    # Replace the section content (with trailing newline for proper spacing)
                    new_section = f"## {section_name}\n{section_content}\n"
                    lines = lines[:section_start] + [new_section] + lines[section_end:]
                    return '\n'.join(lines)
            
            # Section not found - append at the end
            return content + f"\n## {section_name}\n{section_content}\n"

        # Use the helper to replace or append Personality and Boundaries
        soul_content = replace_or_append_section(soul_content, "Personality", personality)
        soul_content = replace_or_append_section(soul_content, "Boundaries", boundaries)

        await storage.write_text(soul_key, soul_content, encoding="utf-8")

        # Ensure memory.md exists
        mem_key = f"{agent_prefix}/memory/memory.md"
        if not await storage.exists(mem_key):
            await storage.write_text(mem_key, "# Memory\n\n_Record important information and knowledge here._\n", encoding="utf-8")

        # Ensure reflections.md exists — copy from central template
        refl_key = f"{agent_prefix}/memory/reflections.md"
        if not await storage.exists(refl_key):
            refl_template = Path(__file__).parent.parent / "templates" / "reflections.md"
            refl_content = refl_template.read_text(encoding="utf-8") if refl_template.exists() else "# Reflections Journal\n"
            await storage.write_text(refl_key, refl_content, encoding="utf-8")

        # Ensure HEARTBEAT.md exists — copy from central template
        hb_key = f"{agent_prefix}/HEARTBEAT.md"
        if not await storage.exists(hb_key):
            hb_template = Path(__file__).parent.parent / "templates" / "HEARTBEAT.md"
            hb_content = hb_template.read_text(encoding="utf-8") if hb_template.exists() else "# Heartbeat Instructions\n"
            await storage.write_text(hb_key, hb_content, encoding="utf-8")

        # Customize state.json
        state_key = f"{agent_prefix}/state.json"
        if await storage.exists(state_key):
            state = json.loads(await storage.read_text(state_key, encoding="utf-8", errors="replace"))
            state["agent_id"] = str(agent.id)
            state["name"] = agent.name
            await storage.write_text(state_key, json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")

        logger.info(f"Initialized agent files at {agent_dir}")

    def _generate_openclaw_config(self, agent: Agent, model: LLMModel | None) -> dict:
        """Generate openclaw.json config for the agent container."""
        config = {
            "agent": {
                "model": f"{model.provider}/{model.model}" if model else "anthropic/claude-sonnet-4-5",
            },
            "agents": {
                "defaults": {
                    "workspace": "/home/node/.openclaw/workspace",
                },
            },
        }

        if model:
            config["env"] = {
                f"{model.provider.upper()}_API_KEY": get_model_api_key(model),
            }

        return config

    async def start_container(self, db: AsyncSession, agent: Agent) -> str | None:
        """Start an OpenClaw Gateway Docker container for the agent.

        Returns container_id or None if Docker not available.
        """
        if agent.deleted_at is not None:
            logger.info("Agent {} is deleted; skipping container start", agent.id)
            return None

        if not self.docker_client:
            logger.info("Docker not available, skipping container start")
            agent.status = "idle"
            agent.last_active_at = datetime.now(timezone.utc)
            return None

        agent_dir = await self._materialize_agent_dir(agent.id)

        # Get model config
        model = await resolve_active_agent_model(db, agent)

        # Generate OpenClaw config
        config = self._generate_openclaw_config(agent, model)
        config_dir = agent_dir / ".openclaw"
        config_dir.mkdir(parents=True, exist_ok=True)
        (config_dir / "openclaw.json").write_text(json.dumps(config, indent=2), encoding="utf-8")

        # Create workspace symlink
        workspace_dir = config_dir / "workspace"
        if not workspace_dir.exists():
            workspace_dir.symlink_to(agent_dir / "workspace")

        # Assign a unique port
        container_port = 18789 + hash(str(agent.id)) % 10000

        try:
            container = self.docker_client.containers.run(
                settings.OPENCLAW_IMAGE,
                detach=True,
                name=f"clawith-agent-{str(agent.id)[:8]}",
                network=settings.DOCKER_NETWORK,
                ports={f"{settings.OPENCLAW_GATEWAY_PORT}/tcp": container_port},
                volumes={
                    str(agent_dir): {"bind": "/home/node/.openclaw", "mode": "rw"},
                },
                environment={
                    "OPENCLAW_GATEWAY_TOKEN": str(uuid.uuid4()),
                },
                restart_policy={"Name": "unless-stopped"},
                labels={
                    "clawith.agent_id": str(agent.id),
                    "clawith.agent_name": agent.name,
                },
            )

            agent.container_id = container.id
            agent.container_port = container_port
            agent.status = "running"
            agent.last_active_at = datetime.now(timezone.utc)

            logger.info(f"Started container {container.id[:12]} for agent {agent.name} on port {container_port}")
            return container.id

        except DockerException as e:
            logger.error(f"Failed to start container for agent {agent.name}: {e}")
            agent.status = "error"
            return None

    async def stop_container(self, agent: Agent) -> bool:
        """Stop the agent's Docker container."""
        if not self.docker_client or not agent.container_id:
            agent.status = "stopped"
            return True

        try:
            container = self.docker_client.containers.get(agent.container_id)
            container.stop(timeout=10)
            agent.status = "stopped"
            logger.info(f"Stopped container {agent.container_id[:12]} for agent {agent.name}")
            return True
        except NotFound:
            agent.status = "stopped"
            agent.container_id = None
            return True
        except DockerException as e:
            logger.error(f"Failed to stop container: {e}")
            return False

    async def remove_container(self, agent: Agent) -> bool:
        """Stop and remove the agent's Docker container."""
        if not self.docker_client or not agent.container_id:
            return True

        try:
            container = self.docker_client.containers.get(agent.container_id)
            container.stop(timeout=10)
            container.remove()
            agent.container_id = None
            agent.container_port = None
            logger.info(f"Removed container for agent {agent.name}")
            return True
        except NotFound:
            agent.container_id = None
            return True
        except DockerException as e:
            logger.error(f"Failed to remove container: {e}")
            return False

    def get_container_status(self, agent: Agent) -> dict:
        """Get real-time container status."""
        if not self.docker_client or not agent.container_id:
            return {"running": False, "status": agent.status}

        try:
            container = self.docker_client.containers.get(agent.container_id)
            return {
                "running": container.status == "running",
                "status": container.status,
                "ports": container.ports,
                "created": container.attrs.get("Created", ""),
            }
        except NotFound:
            return {"running": False, "status": "not_found"}
        except DockerException:
            return {"running": False, "status": "error"}


agent_manager = AgentManager()
