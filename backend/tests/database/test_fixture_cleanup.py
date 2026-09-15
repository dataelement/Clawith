"""Failure-path evidence for disposable PostgreSQL fixture ownership."""

from collections.abc import AsyncGenerator, Callable
from typing import Any, cast

import pytest
from conftest import TestDatabase as DatabaseFixture
from conftest import test_database as test_database_fixture
from sqlalchemy import text
from sqlalchemy.engine import URL, Connection
from sqlalchemy.ext.asyncio import create_async_engine

from app.infrastructure.database import Base


@pytest.mark.asyncio
async def test_fixture_drops_owned_schema_when_metadata_cleanup_fails(
    postgres_url: URL,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture_factory = cast(
        Callable[[URL], AsyncGenerator[DatabaseFixture, None]],
        cast(Any, test_database_fixture).__wrapped__,
    )
    fixture = fixture_factory(postgres_url)
    database = await anext(fixture)

    def fail_metadata_drop(_connection: Connection) -> None:
        raise RuntimeError("injected metadata cleanup failure")

    monkeypatch.setattr(Base.metadata, "drop_all", fail_metadata_drop)

    with pytest.raises(RuntimeError, match="injected metadata cleanup failure"):
        await fixture.aclose()

    verification_engine = create_async_engine(postgres_url)
    try:
        async with verification_engine.connect() as connection:
            exists = await connection.scalar(
                text("SELECT 1 FROM pg_namespace WHERE nspname = :schema"),
                {"schema": database.schema},
            )
    finally:
        await verification_engine.dispose()

    assert exists is None
