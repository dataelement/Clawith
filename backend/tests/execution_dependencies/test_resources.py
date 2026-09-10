import asyncio
import base64
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import create_async_engine

from app import application
from app.execution_dependencies import resources as composition
from app.execution_dependencies.provisioning import provision_builtin_tools
from app.infrastructure.config import Settings
from app.infrastructure.database import DatabaseResources
from app.infrastructure.errors import AccessDenied
from app.infrastructure.http import create_stateless_http_client
from app.modules.agent.public import AgentService
from app.modules.capability_market.public import CatalogSpec
from app.modules.credential.public import Secret
from app.modules.identity_tenant.public import IdentityService, TenantPrincipal
from app.modules.model.public import ModelHardLimits, ModelService
from app.modules.tool.public import CallScope, CredentialBinding, ToolResolutionScope


def configured(tmp_path, **storage):
    return Settings.model_validate({
        "APP_VERSION": "test",
        "EXECUTION": {
            "credential_keys": {"active_version": "v1", "keys": {"v1": base64.b64encode(b"k" * 32).decode()}},
            "continuation_keys": {"active_version": "v1", "keys": {"v1": base64.b64encode(b"c" * 32).decode()}},
            "storage": storage or {"kind": "local", "root": str(tmp_path)},
        },
    })


@pytest.fixture
def composed_database(test_database, monkeypatch):
    resource = DatabaseResources(test_database.engine, test_database.engine,
                                 test_database.sessions, test_database.sessions)
    closed = []
    close = resource.aclose

    async def dispose():
        await close()
        closed.append(True)

    async def create(_settings):
        return resource

    monkeypatch.setattr(DatabaseResources, "aclose", lambda self: dispose())
    monkeypatch.setattr(application.database, "create_database_resources", create)
    return resource, closed


@pytest.fixture
def current_task_connections(test_database):
    """Track borrowers, including when SQLAlchemy returns a connection in a cleanup task."""
    borrowers = {}
    pool = test_database.engine.sync_engine.pool

    def checkout(_connection, record, _proxy):
        borrowers[id(record)] = asyncio.current_task()

    def checkin(_connection, record):
        borrowers.pop(id(record), None)

    def count():
        current = asyncio.current_task()
        return sum(owner is current for owner in borrowers.values())

    event.listen(pool, "checkout", checkout)
    event.listen(pool, "checkin", checkin)
    try:
        yield count
    finally:
        event.remove(pool, "checkout", checkout)
        event.remove(pool, "checkin", checkin)


async def test_current_task_connection_observation_distinguishes_other_workers(test_database, current_task_connections):
    ready, release = asyncio.Event(), asyncio.Event()

    async def background():
        async with test_database.sessions.begin() as session:
            await session.execute(text("SELECT 1"))
            assert current_task_connections() == 1
            ready.set()
            await release.wait()

    worker = asyncio.create_task(background())
    try:
        await ready.wait()
        assert test_database.engine.pool.checkedout() == 1
        assert current_task_connections() == 0
        async with test_database.sessions.begin() as session:
            await session.execute(text("SELECT 1"))
            assert current_task_connections() == 1
            with pytest.raises(AssertionError):
                assert current_task_connections() == 0
        assert current_task_connections() == 0
    finally:
        release.set()
        await worker


async def test_application_services_execute_and_close_with_real_owners(
    test_database, composed_database, tmp_path, monkeypatch, current_task_connections,
):
    observed = []

    def provider(request):
        assert current_task_connections() == 0
        assert request.headers["authorization"] == "Bearer composed-provider-secret"
        observed.append(request)
        return httpx.Response(200, json={"choices": [{"finish_reason": "tool_calls", "message": {
            "tool_calls": [{"id": "probe", "function": {"name": "capability_probe", "arguments": '{"value":"ok"}'}}],
        }}]})

    def client(**kwargs):
        return create_stateless_http_client(transport=httpx.MockTransport(provider), **kwargs)

    monkeypatch.setattr(composition, "create_stateless_http_client", client)
    app = application.create_app(configured(tmp_path))
    async with app.router.lifespan_context(app):
        execution = app.state.execution
        audit = app.state.audit
        async with test_database.sessions.begin() as session:
            from app.infrastructure.transactions import TransactionContext
            tx = TransactionContext(session)
            identity = IdentityService(tx)
            account = await identity.create_account()
            tenant = await identity.create_tenant(name="Composed")
            member = await identity.create_membership(tenant_id=tenant.id, account_id=account.id,
                                                       display_name="Admin", role="tenant_admin")
            principal = TenantPrincipal(account.id, member.id, tenant.id, "tenant_admin")
            credential = await execution.credentials(tx).create(principal, kind="api_key", provider="test",
                label="Model", secret=Secret("composed-provider-secret"), owner_kind="tenant")
            model = await ModelService(tx).create(principal, credential_id=credential.id, provider="test",
                model_name="test", endpoint="https://provider.invalid/v1", context_limit=8192, output_limit=1024,
                capability_source="administrator", capabilities={"supports_tool_calling": True}, settings_version=1,
                settings={"protocol": "openai_chat"}, enabled=False)
        accepted = await execution.model.validate_configuration(tenant_id=tenant.id, credential_id=credential.id,
            provider=model.provider, protocol="openai_chat", model_name=model.model_name, endpoint=model.endpoint,
            administrator_limits=ModelHardLimits(8192, 1024), settings=model.settings, capabilities=model.capabilities)
        async with test_database.sessions.begin() as session:
            tx = TransactionContext(session)
            await ModelService(tx).set_enabled(principal, model_id=model.id, enabled=True, acceptance=accepted)
            agent = await AgentService(tx).create(principal, name="Agent", soul="Useful", timezone="UTC", model_id=model.id)
            await provision_builtin_tools(tx, principal, agent_id=agent.id)
            view = await execution.tools(tx).resolve(ToolResolutionScope(principal, agent.id, "main"))
            assert view.tools
        secret = await execution.resolve_credential(CredentialBinding(credential.id, "tenant", tenant.id),
                                                    CallScope(tenant.id, agent.id, uuid4()))
        assert secret.value == "composed-provider-secret"
        with pytest.raises(AccessDenied):
            await execution.resolve_credential(CredentialBinding(uuid4(), "agent", uuid4()),
                                                CallScope(tenant.id, agent.id, uuid4()))
        scope = await execution.workspace.direct_scope(principal, agent_id=agent.id)
        await execution.workspace.ensure(scope, scope.output)
        revision = await execution.workspace.write(scope, scope.output, "files/test.md", b"composed", expected_revision=None)
        assert (await execution.workspace.read(scope, scope.output, "files/test.md")).revision == revision
        source = await execution.market.register(principal, spec=CatalogSpec("skill", "fixture", "test", "Test", "Test", "1"))
        prepared = await execution.workspace.prepare_skill_package({"SKILL.md": b"Read the test file"})
        await execution.market.install_skill(principal, item_id=source.item.id, agent_id=agent.id,
            skill_name="test", prepared=prepared, shared=False)
        discovery = await execution.workspace.discover_skills(tenant_id=tenant.id, agent_id=agent.id)
        assert discovery.skills == ("test",)
        await execution.market.set_enabled(principal, item_id=source.item.id, enabled=False)
        assert (await execution.workspace.discover_skills(tenant_id=tenant.id, agent_id=agent.id)).skills == ()
        consumers = [task for task in asyncio.all_tasks() if task.get_name() == "audit-observation-consumer"]
        assert consumers and not execution.http.is_closed
    assert execution.http.is_closed and all(task.done() for task in consumers)
    assert audit.statistics.persisted > 0 and composed_database[1] == [True]
    assert not hasattr(app.state, "execution") and not hasattr(app.state, "audit")
    assert len(observed) == 1


@pytest.mark.parametrize("phase", ["LocalStorageBackend", "WorkspaceService", "CapabilityMarketService", "ModelExecutionService"])
async def test_initialization_failures_release_http_audit_and_database(
    composed_database, tmp_path, monkeypatch, phase,
):
    clients = []
    def client(**kwargs):
        result = create_stateless_http_client(**kwargs)
        clients.append(result)
        return result

    def fail(*args, **kwargs):
        raise RuntimeError("construction failed")

    monkeypatch.setattr(composition, "create_stateless_http_client", client)
    monkeypatch.setattr(composition, phase, fail)
    app = application.create_app(configured(tmp_path))
    with pytest.raises(RuntimeError, match="construction failed"):
        async with app.router.lifespan_context(app):
            pytest.fail("Initialization succeeded")
    assert clients and all(client.is_closed for client in clients)
    assert composed_database[1] == [True]
    assert not hasattr(app.state, "execution")
    assert not [task for task in asyncio.all_tasks() if task.get_name() == "audit-observation-consumer"]


async def test_storage_close_failure_does_not_skip_other_resource_cleanup(composed_database, tmp_path, monkeypatch):
    async def fail_close(self):
        raise OSError("storage close failed")
    monkeypatch.setattr(composition.LocalStorageBackend, "aclose", fail_close)
    app = application.create_app(configured(tmp_path))
    with pytest.raises(OSError, match="storage close failed"):
        async with app.router.lifespan_context(app):
            execution = app.state.execution
    assert execution.http.is_closed and composed_database[1] == [True]
    assert not hasattr(app.state, "execution")


async def test_s3_locks_use_a_distinct_pool_and_close_it(test_database, composed_database, tmp_path, monkeypatch, current_task_connections):
    locks = []
    engines = []
    original_lock = composition.PostgresResourceLocks

    def engine(url, **kwargs):
        result = create_async_engine(test_database.engine.url, **kwargs)
        engines.append(result)
        return result

    def lock(engine, **kwargs):
        result = original_lock(engine, **kwargs)
        locks.append(result)
        return result

    monkeypatch.setattr(composition, "create_async_engine", engine)
    monkeypatch.setattr(composition, "PostgresResourceLocks", lock)
    settings = configured(tmp_path, kind="s3", bucket="test-bucket", prefix="target", region="us-east-1",
        authentication="ambient", lock_database_url="postgresql+asyncpg://test:test@localhost:5432/clawith_target",
        lock_pool_size=1, lock_timeout_seconds=1.0)
    app = application.create_app(settings)
    async with app.router.lifespan_context(app):
        execution = app.state.execution
        assert engines[0] is not app.state.database.execution_engine
        assert engines[0].pool is not app.state.database.execution_engine.pool
        async with locks[0]("resource"):
            assert engines[0].pool.checkedout() == 1
            assert current_task_connections() == 0
    assert execution.http.is_closed and engines[0].pool.checkedout() == 0
    assert engines[0].pool.checkedin() == 0
