"""Configured CI PostgreSQL readiness is bounded and failure-specific."""

import asyncio
from collections.abc import Awaitable, Callable

import asyncpg
import pytest
from conftest import _wait_for_configured_postgres
from sqlalchemy.engine import URL
from sqlalchemy.exc import DBAPIError

TEST_URL = URL.create(
    "postgresql+asyncpg",
    username="clawith_test",
    password="isolated-test-only",
    host="postgres",
    port=5432,
    database="clawith_target",
)


@pytest.mark.asyncio
async def test_configured_postgres_readiness_returns_after_a_successful_probe() -> None:
    calls = 0

    async def probe(_url: URL) -> None:
        nonlocal calls
        calls += 1

    await _wait_for_configured_postgres(TEST_URL, probe=probe)

    assert calls == 1


@pytest.mark.asyncio
async def test_configured_postgres_readiness_retries_transient_startup_and_times_out() -> None:
    attempts = 0

    async def eventually_ready(_url: URL) -> None:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise ConnectionRefusedError
        if attempts == 2:
            raise DBAPIError(None, None, asyncpg.CannotConnectNowError("starting"))

    await _wait_for_configured_postgres(TEST_URL, retry_interval=0, probe=eventually_ready)
    assert attempts == 3

    async def unavailable(_url: URL) -> None:
        raise ConnectionRefusedError

    with pytest.raises(RuntimeError, match="did not become ready within 0 seconds"):
        await _wait_for_configured_postgres(
            TEST_URL,
            timeout_seconds=0,
            retry_interval=0,
            probe=unavailable,
        )


@pytest.mark.asyncio
async def test_configured_postgres_readiness_does_not_hide_configuration_failures() -> None:
    async def invalid_password(_url: URL) -> None:
        raise asyncpg.InvalidPasswordError("invalid password")

    async def unknown_database(_url: URL) -> None:
        raise asyncpg.InvalidCatalogNameError("unknown database")

    async def wrapped_invalid_password(_url: URL) -> None:
        raise DBAPIError(None, None, asyncpg.InvalidPasswordError("invalid password"))

    failures: tuple[Callable[[URL], Awaitable[None]], ...] = (
        invalid_password,
        unknown_database,
    )
    for probe in failures:
        with pytest.raises((asyncpg.InvalidPasswordError, asyncpg.InvalidCatalogNameError)):
            await _wait_for_configured_postgres(TEST_URL, probe=probe)

    with pytest.raises(DBAPIError) as exc_info:
        await _wait_for_configured_postgres(TEST_URL, probe=wrapped_invalid_password)
    assert getattr(exc_info.value.orig, "sqlstate", None) == "28P01"


@pytest.mark.asyncio
async def test_configured_postgres_timeout_bounds_a_blocked_probe() -> None:
    async def blocked(_url: URL) -> None:
        await asyncio.sleep(10)

    with pytest.raises(RuntimeError, match="did not become ready within 0.01 seconds"):
        await _wait_for_configured_postgres(TEST_URL, timeout_seconds=0.01, probe=blocked)
