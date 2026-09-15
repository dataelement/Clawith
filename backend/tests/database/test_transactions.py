"""Observe committed rows and returned connections, not rollback callbacks."""

import asyncio

import pytest
from conftest import TestDatabase as DatabaseFixture
from sqlalchemy import func, select

from app.infrastructure.transactions import transaction
from app.modules.identity_tenant.models import AccountRecord
from app.modules.identity_tenant.public import IdentityService


async def account_count(database: DatabaseFixture) -> int:
    async with database.sessions() as session:
        return (await session.scalar(select(func.count()).select_from(AccountRecord))) or 0


@pytest.mark.asyncio
async def test_transaction_publishes_only_after_outer_commit(test_database: DatabaseFixture) -> None:
    async with transaction(test_database.sessions) as tx:
        await IdentityService(tx).create_account()
        assert await account_count(test_database) == 0
    assert await account_count(test_database) == 1
    assert test_database.engine.pool.checkedout() == 0


@pytest.mark.asyncio
async def test_transaction_rolls_back_flushed_rows_on_failure(test_database: DatabaseFixture) -> None:
    with pytest.raises(ValueError, match="operation failed"):
        async with transaction(test_database.sessions) as tx:
            await IdentityService(tx).create_account()
            raise ValueError("operation failed")
    assert await account_count(test_database) == 0
    assert test_database.engine.pool.checkedout() == 0


@pytest.mark.asyncio
async def test_cancelled_operation_rolls_back_and_returns_connection(test_database: DatabaseFixture) -> None:
    flushed = asyncio.Event()

    async def operation() -> None:
        async with transaction(test_database.sessions) as tx:
            await IdentityService(tx).create_account()
            flushed.set()
            await asyncio.Event().wait()

    task = asyncio.create_task(operation())
    try:
        await asyncio.wait_for(flushed.wait(), timeout=5)
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert await account_count(test_database) == 0
    assert test_database.engine.pool.checkedout() == 0
