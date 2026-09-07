"""Opt-in real PostgreSQL fixtures; legacy unit tests never start a database."""

import asyncio
import os
import subprocess
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

import asyncpg
import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import ArgumentError, DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.infrastructure.database import Base
from app.infrastructure.transactions import TransactionContext, transaction

BACKEND_ROOT = Path(__file__).resolve().parents[1]


async def _probe_postgres(url: URL) -> None:
    engine = create_async_engine(url, poolclass=NullPool)
    try:
        async with engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
    finally:
        await engine.dispose()


async def _wait_for_configured_postgres(
    url: URL,
    *,
    timeout_seconds: float = 30,
    retry_interval: float = 0.25,
    probe: Callable[[URL], Awaitable[None]] = _probe_postgres,
) -> None:
    try:
        async with asyncio.timeout(timeout_seconds):
            while True:
                try:
                    await probe(url)
                    return
                except (ConnectionRefusedError, asyncpg.CannotConnectNowError):
                    await asyncio.sleep(retry_interval)
                except DBAPIError as exc:
                    original = exc.orig
                    if not (
                        isinstance(original, asyncpg.CannotConnectNowError)
                        or getattr(original, "sqlstate", None) == "57P03"
                    ):
                        raise
                    await asyncio.sleep(retry_interval)
    except TimeoutError as exc:
        raise RuntimeError(
            f"Configured PostgreSQL did not become ready within {timeout_seconds:g} seconds"
        ) from exc


@pytest.fixture(scope="session")
def postgres_url() -> Iterator[URL]:
    """Use an explicit CI service or own a unique disposable Compose project."""
    configured = os.environ.get("CLAWITH_TEST_POSTGRES_URL")
    if configured:
        try:
            url = make_url(configured)
        except ArgumentError:
            raise RuntimeError("Invalid CLAWITH_TEST_POSTGRES_URL") from None
        if url.drivername != "postgresql+asyncpg" or url.database != "clawith_target":
            raise RuntimeError("Tests require async PostgreSQL and database clawith_target")
        asyncio.run(_wait_for_configured_postgres(url))
        yield url
        return

    project = f"clawith-g003-{uuid4().hex[:12]}"
    compose = ["docker", "compose", "--project-name", project, "--file", str(BACKEND_ROOT / "tests/compose.postgres.yml")]
    try:
        subprocess.run([*compose, "up", "-d", "--wait", "--wait-timeout", "60"], check=True, timeout=100,
                       capture_output=True, text=True)
        address = subprocess.run([*compose, "port", "postgres", "5432"], check=True, timeout=10,
                                 capture_output=True, text=True).stdout.strip()
        host, port = address.rsplit(":", 1)
        if host != "127.0.0.1" or not port.isdigit():
            raise RuntimeError("Test PostgreSQL did not bind a loopback port")
        yield URL.create("postgresql+asyncpg", username="clawith_test", password="isolated-test-only",
                         host=host, port=int(port), database="clawith_target")
    finally:
        subprocess.run([*compose, "down", "--volumes", "--remove-orphans"], check=True, timeout=60,
                       capture_output=True, text=True)


@dataclass(frozen=True)
class TestDatabase:
    engine: AsyncEngine
    sessions: async_sessionmaker[AsyncSession]
    schema: str


@pytest_asyncio.fixture
async def test_database(postgres_url: URL) -> AsyncIterator[TestDatabase]:
    # Every test owns a new schema, even when CI supplies a shared PostgreSQL service.
    from app.infrastructure.schema import register_schema

    register_schema()
    schema = f"clawith_test_{uuid4().hex}"
    engine = create_async_engine(postgres_url, pool_size=4, max_overflow=0)
    scoped = engine.execution_options(schema_translate_map={None: schema})
    created = False
    try:
        async with engine.begin() as connection:
            await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        created = True
        async with scoped.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        yield TestDatabase(scoped, async_sessionmaker(scoped, expire_on_commit=False), schema)
    finally:
        try:
            if created:
                try:
                    async with scoped.begin() as connection:
                        await connection.run_sync(Base.metadata.drop_all)
                finally:
                    # Only this fixture's generated schema is recoverable test data.
                    async with engine.begin() as connection:
                        await connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
                        exists = await connection.scalar(text("SELECT 1 FROM pg_namespace WHERE nspname = :schema"),
                                                         {"schema": schema})
                        assert exists is None, "Test schema cleanup did not complete"
        finally:
            await engine.dispose()


@pytest_asyncio.fixture
async def db_session(test_database: TestDatabase) -> AsyncIterator[AsyncSession]:
    async with test_database.sessions() as session:
        yield session


@pytest.fixture
def transaction_factory(test_database: TestDatabase) -> Callable[[], AbstractAsyncContextManager[TransactionContext]]:
    return lambda: transaction(test_database.sessions)


@pytest.fixture
def model_acceptance(test_database):
    from model_support import validate_draft_model

    async def accept(principal, model, keyring):
        return await validate_draft_model(test_database.sessions, principal, model, keyring)

    return accept
