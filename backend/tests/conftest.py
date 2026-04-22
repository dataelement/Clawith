import asyncio
import importlib
import os
from contextlib import asynccontextmanager

import pytest
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import get_settings
from app.core.security import create_access_token, hash_password
from app.database import Base, get_db
from app.main import app
from app.models.participant import Participant
from app.models.tenant import Tenant
from app.models.user import User


MODEL_MODULES = [
    "app.models.user",
    "app.models.agent",
    "app.models.task",
    "app.models.llm",
    "app.models.tool",
    "app.models.audit",
    "app.models.skill",
    "app.models.channel_config",
    "app.models.schedule",
    "app.models.plaza",
    "app.models.activity_log",
    "app.models.org",
    "app.models.system_settings",
    "app.models.invitation_code",
    "app.models.tenant",
    "app.models.tenant_setting",
    "app.models.participant",
    "app.models.chat_session",
    "app.models.trigger",
    "app.models.notification",
    "app.models.gateway_message",
]


def _import_model_modules() -> None:
    for module in MODEL_MODULES:
        importlib.import_module(module)


def _derive_test_database_url() -> str:
    explicit_url = os.getenv("TEST_DATABASE_URL")
    if explicit_url:
        return explicit_url

    database_url = get_settings().DATABASE_URL
    if database_url.startswith("sqlite"):
        return database_url

    prefix, _, remainder = database_url.rpartition("/")
    database_name, _, query = remainder.partition("?")
    if not prefix or not database_name:
        raise RuntimeError("Unable to derive TEST_DATABASE_URL from DATABASE_URL")

    test_name = database_name if database_name.startswith("test_") else f"test_{database_name}"
    return f"{prefix}/{test_name}" + (f"?{query}" if query else "")


async def _create_schema(engine) -> None:
    _import_model_modules()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def _clear_database(engine) -> None:
    async with engine.begin() as conn:
        for table in reversed(Base.metadata.sorted_tables):
            await conn.execute(table.delete())


async def _insert_user(
    session: AsyncSession,
    *,
    username: str = "test-user",
    email: str = "test@example.com",
    password: str = "password123",
    display_name: str = "Test User",
    role: str = "member",
    with_tenant: bool = True,
    is_active: bool = True,
) -> User:
    tenant_id = None
    if with_tenant:
        tenant = Tenant(
            name="Test Tenant",
            slug=f"tenant-{username}",
            im_provider="web_only",
            is_active=True,
        )
        session.add(tenant)
        await session.flush()
        tenant_id = tenant.id

    user = User(
        username=username,
        email=email,
        password_hash=hash_password(password),
        display_name=display_name,
        role=role,
        tenant_id=tenant_id,
        is_active=is_active,
    )
    session.add(user)
    await session.flush()

    session.add(
        Participant(
            type="user",
            ref_id=user.id,
            display_name=user.display_name,
            avatar_url=user.avatar_url,
        )
    )
    await session.commit()
    await session.refresh(user)
    return user


@pytest.fixture(scope="session")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture(scope="session")
def test_database_url() -> str:
    return _derive_test_database_url()


@pytest.fixture(scope="session")
def test_engine(test_database_url, event_loop):
    engine = create_async_engine(test_database_url)
    event_loop.run_until_complete(_create_schema(engine))
    yield engine
    event_loop.run_until_complete(engine.dispose())


@pytest.fixture(scope="session")
def test_session_maker(test_engine):
    return async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)


@pytest.fixture(autouse=True)
def reset_test_database(test_engine, event_loop):
    event_loop.run_until_complete(_clear_database(test_engine))


@pytest.fixture
def db_session(test_session_maker, event_loop):
    session = test_session_maker()
    yield session
    event_loop.run_until_complete(session.close())


@pytest.fixture
def test_app(test_session_maker):
    original_lifespan = app.router.lifespan_context
    original_overrides = dict(app.dependency_overrides)

    @asynccontextmanager
    async def _noop_lifespan(_app):
        yield

    async def _override_get_db():
        async with test_session_maker() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    app.router.lifespan_context = _noop_lifespan
    app.dependency_overrides[get_db] = _override_get_db
    yield app
    app.dependency_overrides = original_overrides
    app.router.lifespan_context = original_lifespan


@pytest.fixture
def client(test_app):
    with TestClient(test_app) as test_client:
        yield test_client


@pytest.fixture
async def async_client(test_app):
    transport = ASGITransport(app=test_app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        yield ac


@pytest.fixture
def user_factory(db_session, event_loop):
    def _factory(**kwargs):
        return event_loop.run_until_complete(_insert_user(db_session, **kwargs))

    return _factory


@pytest.fixture
async def async_user_factory(db_session):
    async def _factory(**kwargs):
        return await _insert_user(db_session, **kwargs)

    return _factory


@pytest.fixture
def authenticated_user(user_factory):
    return user_factory()


@pytest.fixture
def auth_headers(authenticated_user):
    token = create_access_token(str(authenticated_user.id), authenticated_user.role)
    return {"Authorization": f"Bearer {token}"}
