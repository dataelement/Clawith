"""Restore Docker runtimes managed by consultant_backend."""

from dataclasses import dataclass, field
from pathlib import Path

from docker.errors import DockerException, ImageNotFound, NotFound
from loguru import logger

from app.config import get_settings
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
