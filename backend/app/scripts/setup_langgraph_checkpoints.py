"""Install or upgrade tables owned by the pinned LangGraph checkpointer."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from psycopg import AsyncConnection

from app.config import Settings
from app.services.agent_runtime.checkpointer import (
    checkpoint_database_url,
    create_checkpointer,
)


_SETUP_LOCK_NAME = "clawith:langgraph_checkpoint:setup"


@asynccontextmanager
async def checkpoint_setup_lock(
    settings: Settings | None = None,
) -> AsyncIterator[None]:
    """Serialize saver DDL across concurrently starting bootstrap processes.

    ``AsyncPostgresSaver.setup()`` maintains its own migration ledger, but the
    initial ledger read and write are not one atomic operation. A PostgreSQL
    session advisory lock keeps the explicit deployment step idempotent when
    more than one bootstrap process starts at the same time. PostgreSQL releases
    the lock automatically if the setup process exits unexpectedly.
    """

    connection = await AsyncConnection.connect(
        checkpoint_database_url(settings),
        autocommit=True,
    )
    try:
        async with connection.cursor() as cursor:
            await cursor.execute(
                "SELECT pg_advisory_lock(hashtextextended(%s, 0))",
                (_SETUP_LOCK_NAME,),
            )
        try:
            yield
        finally:
            async with connection.cursor() as cursor:
                await cursor.execute(
                    "SELECT pg_advisory_unlock(hashtextextended(%s, 0))",
                    (_SETUP_LOCK_NAME,),
                )
    finally:
        await connection.close()


async def setup_checkpoint_tables(settings: Settings | None = None) -> None:
    """Run the upstream idempotent migration ledger inside its isolated schema.

    Alembic creates ``langgraph_checkpoint`` first. This explicit bootstrap
    step then lets the pinned saver version create or upgrade only its own
    tables. FastAPI runtime startup intentionally does not run checkpoint DDL.
    """
    async with checkpoint_setup_lock(settings):
        async with create_checkpointer(settings) as checkpointer:
            await checkpointer.setup()


def main() -> None:
    asyncio.run(setup_checkpoint_tables())


if __name__ == "__main__":
    main()
