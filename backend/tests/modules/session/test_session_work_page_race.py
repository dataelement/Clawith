"""READ COMMITTED work pages use their bounded metadata selection under concurrent writes."""

import asyncio

import pytest
from modules.session.test_session import accept, setup
from sqlalchemy import update

from app.infrastructure.errors import InvalidInput
from app.modules.session.models import SessionRunLinkRecord
from app.modules.session.public import SessionService


@pytest.mark.parametrize("initial_entry", [False, True])
async def test_concurrent_accepted_input_does_not_expand_selected_work_page(transaction_factory, monkeypatch, initial_entry):
    principal, session = await setup(transaction_factory)
    original = await accept(transaction_factory, principal, session, key="original") if initial_entry else None
    selected, release = asyncio.Event(), asyncio.Event()
    async with transaction_factory() as tx:
        execute = tx.session.execute
        paused = False

        async def pause_after_metadata(statement, *args, **kwargs):
            nonlocal paused
            result = await execute(statement, *args, **kwargs)
            sql = str(statement)
            if not paused and "session_run_links" in sql and "octet_length" in sql:
                paused = True
                selected.set()
                await release.wait()
            return result

        monkeypatch.setattr(tx.session, "execute", pause_after_metadata)
        reader = asyncio.create_task(SessionService(tx).list_work(principal, session_id=session.id))
        try:
            await asyncio.wait_for(selected.wait(), 3)
            added = await accept(transaction_factory, principal, session, key="concurrent")
            release.set()
            page = await asyncio.wait_for(reader, 3)
            assert [item.id for item in page.work] == ([original.link.id] if original is not None else [])
            refreshed = await SessionService(tx).list_work(principal, session_id=session.id)
            assert added.link.id in {item.id for item in refreshed.work}
        finally:
            release.set()
            reader.cancel()
            await asyncio.gather(reader, return_exceptions=True)


async def test_selected_result_growing_past_bound_between_queries_still_fails_closed(transaction_factory, monkeypatch):
    principal, session = await setup(transaction_factory)
    accepted = await accept(transaction_factory, principal, session)
    selected, release = asyncio.Event(), asyncio.Event()
    async with transaction_factory() as tx:
        execute = tx.session.execute
        paused = False

        async def pause_after_metadata(statement, *args, **kwargs):
            nonlocal paused
            result = await execute(statement, *args, **kwargs)
            if not paused and "session_run_links" in str(statement) and "octet_length" in str(statement):
                paused = True
                selected.set()
                await release.wait()
            return result

        monkeypatch.setattr(tx.session, "execute", pause_after_metadata)
        reader = asyncio.create_task(SessionService(tx).list_work(principal, session_id=session.id))
        try:
            await asyncio.wait_for(selected.wait(), 3)
            async with transaction_factory() as writer:
                # Inject invalid persisted data; public mutation rejects oversized result indexes.
                await writer.session.execute(update(SessionRunLinkRecord).where(SessionRunLinkRecord.id == accepted.link.id)
                    .values(result={"run_id": str(accepted.link.id), "status": "Failed", "reason": "x" * 9000}))
            release.set()
            with pytest.raises(InvalidInput, match="changed while reading"):
                await asyncio.wait_for(reader, 3)
        finally:
            release.set()
            reader.cancel()
            await asyncio.gather(reader, return_exceptions=True)
