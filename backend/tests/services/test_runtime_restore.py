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


class FakeRows:
    def __init__(self, rows):
        self.rows = rows

    def scalars(self):
        return self

    def all(self):
        return self.rows


class FakeSession:
    def __init__(self, workspaces=None, agents=None):
        self.workspaces = workspaces or []
        self.agents = agents or []
        self.commits = 0
        self.statements = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return None

    async def execute(self, statement):
        self.statements.append(statement)
        entity = statement.column_descriptions[0]["entity"]
        params = statement.compile().params
        if entity is runtime_restore.WorkspaceProject:
            rows = self.workspaces
            if "deploy_type_1" in params:
                rows = [row for row in rows if getattr(row, "deploy_type", None) == params["deploy_type_1"]]
            if "status_1" in params:
                rows = [row for row in rows if getattr(row, "status", None) == params["status_1"]]
            return FakeRows(rows)
        if entity is runtime_restore.Agent:
            rows = self.agents
            if "status_1" in params:
                rows = [row for row in rows if getattr(row, "status", None) == params["status_1"]]
            if "agent_type_1" in params:
                rows = [row for row in rows if getattr(row, "agent_type", None) == params["agent_type_1"]]
            return FakeRows(rows)
        return FakeRows([])

    async def commit(self):
        self.commits += 1


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


@pytest.mark.asyncio
async def test_restore_agent_missing_container_calls_agent_manager(monkeypatch):
    agent = SimpleNamespace(id="agent-id", container_id="missing", status="running")
    fake_docker = FakeDocker(FakeImages(set()), FakeContainers())
    monkeypatch.setattr(runtime_restore, "_get_docker_client", lambda: fake_docker)

    calls = []
    manager_docker = FakeDocker(FakeImages(set()), FakeContainers())

    class FakeAgentManager:
        def __init__(self):
            self.docker_client = manager_docker

        async def start_container(self, db, agent_arg):
            calls.append((db, agent_arg))
            agent_arg.container_id = "new-agent-container"
            return "new-agent-container"

    monkeypatch.setattr(runtime_restore, "AgentManager", FakeAgentManager)

    result = await runtime_restore.restore_agent_runtime(object(), agent)

    assert result.action == "created"
    assert result.container_id == "new-agent-container"
    assert calls[0][1] is agent
    assert manager_docker.closed is True


@pytest.mark.asyncio
async def test_restore_agent_stopped_container_starts_existing(monkeypatch):
    started = []
    container = SimpleNamespace(id="agent-container", status="exited", start=lambda: started.append(True))
    fake_docker = FakeDocker(FakeImages(set()), FakeContainers(by_id={"agent-container": container}))
    monkeypatch.setattr(runtime_restore, "_get_docker_client", lambda: fake_docker)

    agent = SimpleNamespace(id="agent-id", container_id="agent-container", status="running")
    result = await runtime_restore.restore_agent_runtime(object(), agent)

    assert result.action == "started"
    assert result.container_id == "agent-container"
    assert started == [True]


@pytest.mark.asyncio
async def test_restore_agent_reattaches_existing_deterministic_container(monkeypatch):
    agent = SimpleNamespace(id="12345678-0000-0000-0000-000000000000", container_id=None, status="running")
    container = SimpleNamespace(id="actual-agent-container", status="running")
    fake_docker = FakeDocker(
        FakeImages(set()),
        FakeContainers(by_name={"clawith-agent-12345678": container}),
    )
    monkeypatch.setattr(runtime_restore, "_get_docker_client", lambda: fake_docker)

    calls = []

    class FakeAgentManager:
        def __init__(self):
            self.docker_client = None

        async def start_container(self, db, agent_arg):
            calls.append((db, agent_arg))
            raise AssertionError("existing deterministic container should be reused")

    monkeypatch.setattr(runtime_restore, "AgentManager", FakeAgentManager)

    result = await runtime_restore.restore_agent_runtime(object(), agent)

    assert result.action == "unchanged"
    assert result.container_id == "actual-agent-container"
    assert agent.container_id == "actual-agent-container"
    assert calls == []


@pytest.mark.asyncio
async def test_restore_agent_missing_container_reports_manager_error_and_closes(monkeypatch):
    agent = SimpleNamespace(id="agent-id", container_id="missing", status="running")
    fake_docker = FakeDocker(FakeImages(set()), FakeContainers())
    manager_docker = FakeDocker(FakeImages(set()), FakeContainers())
    monkeypatch.setattr(runtime_restore, "_get_docker_client", lambda: fake_docker)

    class FakeAgentManager:
        def __init__(self):
            self.docker_client = manager_docker

        async def start_container(self, db, agent_arg):
            raise RuntimeError("agent files unavailable")

    monkeypatch.setattr(runtime_restore, "AgentManager", FakeAgentManager)

    result = await runtime_restore.restore_agent_runtime(object(), agent)

    assert result.action == "error"
    assert result.message == "agent files unavailable"
    assert fake_docker.closed is True
    assert manager_docker.closed is True


@pytest.mark.asyncio
async def test_restore_managed_runtimes_updates_workspace_container_id_and_commits(monkeypatch):
    workspace = SimpleNamespace(container_id="old-workspace-container")
    agent = SimpleNamespace(id="agent-id")

    class FakeRows:
        def __init__(self, rows):
            self.rows = rows

        def scalars(self):
            return self

        def all(self):
            return self.rows

    class FakeSession:
        def __init__(self):
            self.commits = 0

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback):
            return None

        async def execute(self, statement):
            entity = statement.column_descriptions[0]["entity"]
            if entity is runtime_restore.WorkspaceProject:
                return FakeRows([workspace])
            if entity is runtime_restore.Agent:
                return FakeRows([agent])
            return FakeRows([])

        async def commit(self):
            self.commits += 1

    fake_session = FakeSession()
    monkeypatch.setattr(runtime_restore, "async_session", lambda: fake_session)

    async def fake_restore_workspace_project(project_arg):
        assert project_arg is workspace
        return runtime_restore.RuntimeRestoreItem(
            "workspace",
            "node-demo",
            "created",
            container_id="new-workspace-container",
        )

    async def fake_restore_agent_runtime(db, agent_arg):
        assert db is fake_session
        assert agent_arg is agent
        return runtime_restore.RuntimeRestoreItem("agent", "agent-id", "unchanged", container_id="agent-container")

    monkeypatch.setattr(runtime_restore, "restore_workspace_project", fake_restore_workspace_project)
    monkeypatch.setattr(runtime_restore, "restore_agent_runtime", fake_restore_agent_runtime)

    result = await runtime_restore.restore_managed_runtimes()

    assert workspace.container_id == "new-workspace-container"
    assert fake_session.commits == 1
    assert [(item.runtime_type, item.key, item.action) for item in result.items] == [
        ("workspace", "node-demo", "created"),
        ("agent", "agent-id", "unchanged"),
    ]


@pytest.mark.asyncio
async def test_restore_managed_runtimes_skips_running_openclaw_agents(monkeypatch):
    native_agent = SimpleNamespace(id="native-agent-id", status="running", agent_type="native")
    openclaw_agent = SimpleNamespace(id="openclaw-agent-id", status="running", agent_type="openclaw")
    fake_session = FakeSession(agents=[native_agent, openclaw_agent])
    monkeypatch.setattr(runtime_restore, "async_session", lambda: fake_session)

    calls = []

    async def fake_restore_agent_runtime(db, agent_arg):
        calls.append(agent_arg)
        return runtime_restore.RuntimeRestoreItem("agent", str(agent_arg.id), "unchanged", container_id="agent-container")

    monkeypatch.setattr(runtime_restore, "restore_agent_runtime", fake_restore_agent_runtime)

    result = await runtime_restore.restore_managed_runtimes()

    assert calls == [native_agent]
    assert fake_session.commits == 1
    assert [(item.runtime_type, item.key, item.action) for item in result.items] == [
        ("agent", "native-agent-id", "unchanged"),
    ]


@pytest.mark.asyncio
async def test_restore_managed_runtimes_isolates_per_item_failures_and_still_commits(monkeypatch):
    bad_workspace = SimpleNamespace(
        slug="bad-workspace",
        container_id=None,
        deploy_type="container",
        status="deployed",
    )
    later_agent = SimpleNamespace(id="later-agent-id", status="running", agent_type="native")
    fake_session = FakeSession(workspaces=[bad_workspace], agents=[later_agent])
    monkeypatch.setattr(runtime_restore, "async_session", lambda: fake_session)

    async def fake_restore_workspace_project(project_arg):
        assert project_arg is bad_workspace
        raise RuntimeError("workspace conf denied")

    async def fake_restore_agent_runtime(db, agent_arg):
        assert db is fake_session
        assert agent_arg is later_agent
        return runtime_restore.RuntimeRestoreItem("agent", "later-agent-id", "unchanged", container_id="agent-container")

    monkeypatch.setattr(runtime_restore, "restore_workspace_project", fake_restore_workspace_project)
    monkeypatch.setattr(runtime_restore, "restore_agent_runtime", fake_restore_agent_runtime)

    result = await runtime_restore.restore_managed_runtimes()

    assert fake_session.commits == 1
    assert [(item.runtime_type, item.key, item.action, item.message) for item in result.items] == [
        ("workspace", "bad-workspace", "error", "workspace conf denied"),
        ("agent", "later-agent-id", "unchanged", ""),
    ]
