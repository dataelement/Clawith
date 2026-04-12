from types import SimpleNamespace

import pytest

from app.services import runtime_restore


class FakeImages:
    def __init__(self, existing):
        self.existing = set(existing)

    def get(self, image):
        if image not in self.existing:
            raise runtime_restore.DockerImageMissing(image)
        return SimpleNamespace(tags=[image])


class FakeContainers:
    def __init__(self, by_name=None, by_id=None):
        self.by_name = by_name or {}
        self.by_id = by_id or {}
        self.runs = []

    def get(self, key):
        if key in self.by_id:
            return self.by_id[key]
        if key in self.by_name:
            return self.by_name[key]
        raise runtime_restore.DockerContainerMissing(key)

    def run(self, **kwargs):
        self.runs.append(kwargs)
        container = SimpleNamespace(id="new-container-id", status="running")
        self.by_id[container.id] = container
        self.by_name[kwargs["name"]] = container
        return container


class FakeDocker:
    def __init__(self, images, containers):
        self.images = images
        self.containers = containers
        self.closed = False

    def close(self):
        self.closed = True


def project(**overrides):
    values = {
        "slug": "node-demo",
        "container_id": "old-container-id",
        "container_image": "ws-node-demo:latest",
        "container_port": 3000,
        "resource_limits": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


@pytest.mark.asyncio
async def test_restore_workspace_recreates_missing_container(monkeypatch, tmp_path):
    fake_docker = FakeDocker(FakeImages({"ws-node-demo:latest"}), FakeContainers())
    monkeypatch.setattr(runtime_restore, "_get_docker_client", lambda: fake_docker)
    monkeypatch.setattr(runtime_restore.settings, "WORKSPACE_CONF_DIR", str(tmp_path))
    reloads = []

    async def fake_reload_gateway():
        reloads.append(True)

    monkeypatch.setattr(runtime_restore, "_reload_gateway", fake_reload_gateway)

    result = await runtime_restore.restore_workspace_project(project())

    assert result.action == "created"
    assert result.container_id == "new-container-id"
    assert fake_docker.containers.runs[0]["name"] == "ws-node-demo"
    assert fake_docker.containers.runs[0]["network"] == "workspace"
    assert (tmp_path / "node-demo.conf").read_text(encoding="utf-8").count("ws-node-demo:3000") == 1
    assert reloads == [True]
    assert fake_docker.closed is True


@pytest.mark.asyncio
async def test_restore_workspace_starts_stopped_container(monkeypatch, tmp_path):
    starts = []

    def fake_start():
        starts.append(True)

    stopped = SimpleNamespace(id="old-container-id", status="exited", start=fake_start)
    fake_docker = FakeDocker(
        FakeImages({"ws-node-demo:latest"}),
        FakeContainers(by_id={"old-container-id": stopped}, by_name={"ws-node-demo": stopped}),
    )
    monkeypatch.setattr(runtime_restore, "_get_docker_client", lambda: fake_docker)
    monkeypatch.setattr(runtime_restore.settings, "WORKSPACE_CONF_DIR", str(tmp_path))

    async def fake_reload_gateway():
        return None

    monkeypatch.setattr(runtime_restore, "_reload_gateway", fake_reload_gateway)

    result = await runtime_restore.restore_workspace_project(project())

    assert result.action == "started"
    assert result.container_id == "old-container-id"
    assert starts == [True]
    assert fake_docker.containers.runs == []
    assert fake_docker.closed is True


@pytest.mark.asyncio
async def test_restore_workspace_running_with_current_nginx_conf_is_unchanged(monkeypatch, tmp_path):
    running = SimpleNamespace(id="old-container-id", status="running")
    fake_docker = FakeDocker(
        FakeImages({"ws-node-demo:latest"}),
        FakeContainers(by_id={"old-container-id": running}, by_name={"ws-node-demo": running}),
    )
    monkeypatch.setattr(runtime_restore, "_get_docker_client", lambda: fake_docker)
    monkeypatch.setattr(runtime_restore.settings, "WORKSPACE_CONF_DIR", str(tmp_path))
    (tmp_path / "node-demo.conf").write_text(
        runtime_restore.workspace_nginx_conf_content("node-demo", 3000),
        encoding="utf-8",
    )
    reloads = []

    async def fake_reload_gateway():
        reloads.append(True)

    monkeypatch.setattr(runtime_restore, "_reload_gateway", fake_reload_gateway)

    result = await runtime_restore.restore_workspace_project(project())

    assert result.action == "unchanged"
    assert result.container_id == "old-container-id"
    assert reloads == []
    assert fake_docker.closed is True


@pytest.mark.asyncio
async def test_restore_workspace_does_not_rebuild_missing_image(monkeypatch, tmp_path):
    fake_docker = FakeDocker(FakeImages(set()), FakeContainers())
    monkeypatch.setattr(runtime_restore, "_get_docker_client", lambda: fake_docker)
    monkeypatch.setattr(runtime_restore.settings, "WORKSPACE_CONF_DIR", str(tmp_path))

    result = await runtime_restore.restore_workspace_project(project())

    assert result.action == "unrestorable"
    assert "image missing" in result.message
    assert fake_docker.containers.runs == []
    assert fake_docker.closed is True


@pytest.mark.asyncio
async def test_restore_workspace_reports_docker_client_error(monkeypatch):
    def raise_docker_error():
        raise runtime_restore.DockerException("socket unavailable")

    monkeypatch.setattr(runtime_restore, "_get_docker_client", raise_docker_error)

    result = await runtime_restore.restore_workspace_project(project())

    assert result.action == "error"
    assert result.message == "socket unavailable"
