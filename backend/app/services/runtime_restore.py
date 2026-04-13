"""Restore Docker runtimes managed by consultant_backend."""

from dataclasses import dataclass, field
from pathlib import Path

from docker.errors import DockerException, ImageNotFound, NotFound
from loguru import logger
from sqlalchemy import select

from app.config import get_settings
from app.database import async_session
from app.models.agent import Agent
from app.models.workspace import WorkspaceProject
from app.services.agent_manager import AgentManager
from app.services.workspace_tools import (
    _get_docker_client,
    _reload_gateway,
    build_workspace_container_kwargs,
    workspace_container_name,
    workspace_nginx_conf_content,
)

settings = get_settings()


class DockerContainerMissing(Exception):
    pass


class DockerImageMissing(Exception):
    pass


@dataclass
class RuntimeRestoreItem:
    runtime_type: str
    key: str
    action: str
    message: str = ""
    container_id: str | None = None


@dataclass
class RuntimeRestoreResult:
    items: list[RuntimeRestoreItem] = field(default_factory=list)

    def add(self, item: RuntimeRestoreItem) -> None:
        self.items.append(item)


def _get_container(client, *keys: str):
    for key in keys:
        if not key:
            continue
        try:
            return client.containers.get(key)
        except (NotFound, DockerContainerMissing):
            continue
    raise DockerContainerMissing(keys[-1] if keys else "")


def _agent_container_name(agent) -> str:
    return f"clawith-agent-{str(agent.id)[:8]}"


def _ensure_image(client, image: str) -> None:
    try:
        client.images.get(image)
    except (ImageNotFound, DockerImageMissing) as exc:
        raise DockerImageMissing(image) from exc


def _ensure_workspace_nginx_conf(slug: str, port: int) -> bool:
    conf_dir = Path(settings.WORKSPACE_CONF_DIR)
    conf_dir.mkdir(parents=True, exist_ok=True)
    conf_path = conf_dir / f"{slug}.conf"
    expected = workspace_nginx_conf_content(slug, port)
    current = conf_path.read_text(encoding="utf-8") if conf_path.exists() else None
    if current == expected:
        return False
    conf_path.write_text(expected, encoding="utf-8")
    return True


async def restore_workspace_project(project) -> RuntimeRestoreItem:
    slug = project.slug
    name = workspace_container_name(slug)
    image = project.container_image
    port = project.container_port
    if not image or not port:
        return RuntimeRestoreItem("workspace", slug, "unrestorable", "missing image or port")

    client = None
    try:
        client = _get_docker_client()
        try:
            container = _get_container(client, project.container_id, name)
            if container.status == "running":
                changed = _ensure_workspace_nginx_conf(slug, port)
                if changed:
                    await _reload_gateway()
                return RuntimeRestoreItem("workspace", slug, "unchanged", container_id=container.id)
            container.start()
            changed = _ensure_workspace_nginx_conf(slug, port)
            if changed:
                await _reload_gateway()
            return RuntimeRestoreItem("workspace", slug, "started", container_id=container.id)
        except DockerContainerMissing:
            _ensure_image(client, image)
            kwargs = build_workspace_container_kwargs(slug, image, project.resource_limits)
            container = client.containers.run(**kwargs)
            changed = _ensure_workspace_nginx_conf(slug, port)
            if changed:
                await _reload_gateway()
            return RuntimeRestoreItem("workspace", slug, "created", container_id=container.id)
    except DockerImageMissing:
        logger.warning("Cannot restore workspace {}: image {} missing", slug, image)
        return RuntimeRestoreItem("workspace", slug, "unrestorable", f"image missing: {image}")
    except DockerException as exc:
        logger.exception("Docker error restoring workspace {}", slug)
        return RuntimeRestoreItem("workspace", slug, "error", str(exc))
    finally:
        if client is not None:
            close = getattr(client, "close", None)
            if close:
                close()


async def restore_agent_runtime(db, agent) -> RuntimeRestoreItem:
    client = None
    try:
        client = _get_docker_client()
        try:
            container = _get_container(client, agent.container_id, _agent_container_name(agent))
            if container.id != agent.container_id:
                agent.container_id = container.id
            if container.status == "running":
                return RuntimeRestoreItem("agent", str(agent.id), "unchanged", container_id=container.id)
            container.start()
            return RuntimeRestoreItem("agent", str(agent.id), "started", container_id=container.id)
        except DockerContainerMissing:
            manager = None
            try:
                manager = AgentManager()
                container_id = await manager.start_container(db, agent)
                if container_id:
                    return RuntimeRestoreItem("agent", str(agent.id), "created", container_id=container_id)
                return RuntimeRestoreItem(
                    "agent",
                    str(agent.id),
                    "error",
                    "AgentManager.start_container returned no container",
                )
            except Exception as exc:
                logger.exception("Error restoring agent {}", agent.id)
                return RuntimeRestoreItem("agent", str(agent.id), "error", str(exc))
            finally:
                if manager is not None:
                    manager_client = getattr(manager, "docker_client", None)
                    close = getattr(manager_client, "close", None)
                    if manager_client is not client and close:
                        close()
    except DockerException as exc:
        logger.exception("Docker error restoring agent {}", agent.id)
        return RuntimeRestoreItem("agent", str(agent.id), "error", str(exc))
    finally:
        if client is not None:
            close = getattr(client, "close", None)
            if close:
                close()


async def restore_managed_runtimes() -> RuntimeRestoreResult:
    result = RuntimeRestoreResult()
    async with async_session() as db:
        workspace_rows = await db.execute(
            select(WorkspaceProject).where(
                WorkspaceProject.deploy_type == "container",
                WorkspaceProject.status == "deployed",
            )
        )
        for project in workspace_rows.scalars().all():
            try:
                item = await restore_workspace_project(project)
            except Exception as exc:
                logger.exception("Error restoring workspace {}", project.slug)
                item = RuntimeRestoreItem("workspace", str(project.slug), "error", str(exc))
            result.add(item)
            if item.container_id and item.container_id != project.container_id:
                project.container_id = item.container_id

        agent_rows = await db.execute(select(Agent).where(Agent.status == "running", Agent.agent_type == "native"))
        for agent in agent_rows.scalars().all():
            try:
                item = await restore_agent_runtime(db, agent)
            except Exception as exc:
                logger.exception("Error restoring agent {}", agent.id)
                item = RuntimeRestoreItem("agent", str(agent.id), "error", str(exc))
            result.add(item)

        await db.commit()

    logger.info(
        "Runtime restore completed: {}",
        [{"type": item.runtime_type, "key": item.key, "action": item.action} for item in result.items],
    )
    return result
