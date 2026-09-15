"""History-only database fixtures; these tests do not exercise Run admission or E2E."""

import asyncio
import json
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from database.test_schema_wave_S1 import _seed_to_agent
from sqlalchemy import Text, cast, event, func, select, update

from app.infrastructure.errors import InvalidInput, NotFound
from app.modules.model.public import ModelStepResult, ModelToolCall, ModelUsage
from app.modules.run.contracts import (
    MAX_RECORD_BYTES,
    InitialInputPayload,
    InputContent,
    InvalidHistory,
    ModelStepPayload,
    RelatedInputPayload,
    TerminalOutcomePayload,
    ToolResultPayload,
    encode_history,
)
from app.modules.run.models import RunHistoryRecord, RunRecord
from app.modules.run.repository import MAX_PAGE_BYTES, RunHistoryRepository, SourceIdentity
from app.modules.tool.public import ToolResult


async def seed(transaction_factory, count=1):
    async with transaction_factory() as tx:
        data = await _seed_to_agent(tx.session)
        tenant_id, agent_id = data["tenant"].id, data["agent"].id
        runs = []
        now = datetime.now(UTC)
        for _ in range(count):
            row = RunRecord(id=uuid4(), tenant_id=tenant_id, agent_id=agent_id, parent_run_id=None,
                status="Running", initiator_kind="fixture", initiator_owner_id=uuid4(), source_key=str(uuid4()),
                latest_history_sequence=0, active_waiting_reference=None, created_at=now, started_at=now,
                updated_at=now, finished_at=None)
            tx.session.add(row)
            runs.append(row.id)
        await tx.session.flush()
    return tenant_id, runs


def source(key):
    return SourceIdentity("fixture", uuid4(), key)


async def test_append_roundtrip_and_duplicate_source_preserve_original_sequence(transaction_factory):
    tenant, (run,) = await seed(transaction_factory)
    identity = source("input-1")
    async with transaction_factory() as tx:
        repo = RunHistoryRepository(tx)
        first = await repo.append(tenant_id=tenant, run_id=run, payload=InitialInputPayload(InputContent("initial")), source=identity)
        duplicate = await repo.append(tenant_id=tenant, run_id=run, payload=InitialInputPayload(InputContent("changed retry")), source=identity)
        assert first.appended and not duplicate.appended
        assert first.entry == duplicate.entry
        assert first.entry.sequence == 1
    async with transaction_factory() as tx:
        page = await RunHistoryRepository(tx).read_page(tenant_id=tenant, run_id=run)
        assert page.entries == (first.entry,)
        assert page.through_sequence == page.next_after_sequence == 1
        assert not page.has_more


async def test_concurrent_appends_are_contiguous_and_duplicate_races_append_once(transaction_factory):
    tenant, (run,) = await seed(transaction_factory)
    async def append(identity):
        async with transaction_factory() as tx:
            return await RunHistoryRepository(tx).append(tenant_id=tenant, run_id=run,
                payload=RelatedInputPayload(InputContent(identity.key)), source=identity)
    results = await asyncio.gather(*(append(source(str(index))) for index in range(8)))
    assert sorted(value.entry.sequence for value in results) == list(range(1, 9))
    identity = source("same")
    repeated = await asyncio.gather(*(append(identity) for _ in range(4)))
    assert sum(value.appended for value in repeated) == 1
    assert {value.entry.sequence for value in repeated} == {9}


async def test_other_run_progress_and_rollback_release_row_lock(transaction_factory):
    tenant, (first, other) = await seed(transaction_factory, 2)
    async with transaction_factory() as tx:
        await tx.session.scalar(select(RunRecord).where(RunRecord.id == first).with_for_update())
        async def append_other():
            async with transaction_factory() as second:
                return await RunHistoryRepository(second).append(tenant_id=tenant, run_id=other,
                    payload=RelatedInputPayload(InputContent("other")), source=source("other"))
        assert (await asyncio.wait_for(append_other(), 1)).entry.sequence == 1
    with pytest.raises(RuntimeError, match="rollback"):
        async with transaction_factory() as tx:
            await RunHistoryRepository(tx).append(tenant_id=tenant, run_id=first,
                payload=RelatedInputPayload(InputContent("rolled back")), source=source("rollback"))
            raise RuntimeError("rollback")
    async with transaction_factory() as tx:
        result = await RunHistoryRepository(tx).append(tenant_id=tenant, run_id=first,
            payload=RelatedInputPayload(InputContent("next")), source=source("next"))
        assert result.entry.sequence == 1


async def test_tenant_scoping_and_input_identity_requirements(transaction_factory):
    tenant, (run,) = await seed(transaction_factory)
    async with transaction_factory() as tx:
        repo = RunHistoryRepository(tx)
        with pytest.raises(InvalidInput):
            await repo.append(tenant_id=tenant, run_id=run, payload=RelatedInputPayload(InputContent("missing source")))
        with pytest.raises(NotFound):
            await repo.append(tenant_id=uuid4(), run_id=run, payload=RelatedInputPayload(InputContent("wrong tenant")), source=source("a"))
        with pytest.raises(NotFound):
            await repo.read_page(tenant_id=uuid4(), run_id=run)
        with pytest.raises(NotFound):
            await repo.has_unseen_related_input(tenant_id=uuid4(), run_id=run, after_read_boundary=0)


async def test_unseen_predicate_only_counts_related_input(transaction_factory):
    tenant, (run,) = await seed(transaction_factory)
    async with transaction_factory() as tx:
        repo = RunHistoryRepository(tx)
        await repo.append(tenant_id=tenant, run_id=run, payload=InitialInputPayload(InputContent("initial")), source=source("initial"))
        await repo.append(tenant_id=tenant, run_id=run, payload=ModelStepPayload("step", 1,
            ModelStepResult("answer", (), "stop", ModelUsage(), "interaction", False)), source=source("model-step"))
        await repo.append(tenant_id=tenant, run_id=run, payload=ToolResultPayload("step", "tool", ToolResult("call", "success", '{}')))
        assert not await repo.has_unseen_related_input(tenant_id=tenant, run_id=run, after_read_boundary=1)
        await repo.append(tenant_id=tenant, run_id=run, payload=RelatedInputPayload(InputContent("new")), source=source("new"))
        assert await repo.has_unseen_related_input(tenant_id=tenant, run_id=run, after_read_boundary=1)
        assert not await repo.has_unseen_related_input(tenant_id=tenant, run_id=run, after_read_boundary=4)


async def test_page_cutoff_is_frozen_across_later_appends(transaction_factory):
    tenant, (run,) = await seed(transaction_factory)
    async with transaction_factory() as tx:
        repo = RunHistoryRepository(tx)
        for index in range(3):
            await repo.append(tenant_id=tenant, run_id=run, payload=RelatedInputPayload(InputContent(str(index))), source=source(str(index)))
        first = await repo.read_page(tenant_id=tenant, run_id=run, limit=2)
        assert [value.sequence for value in first.entries] == [1, 2]
        assert first.has_more
        await repo.append(tenant_id=tenant, run_id=run, payload=RelatedInputPayload(InputContent("later")), source=source("later"))
        final = await repo.read_page(tenant_id=tenant, run_id=run, after_sequence=first.next_after_sequence,
            through_sequence=first.through_sequence, limit=2)
        assert [value.sequence for value in final.entries] == [3]
        assert not final.has_more


async def test_large_payload_is_rejected_by_metadata_before_any_payload_fetch(transaction_factory, test_database):
    tenant, (run,) = await seed(transaction_factory)
    async with transaction_factory() as tx:
        await RunHistoryRepository(tx).append(tenant_id=tenant, run_id=run,
            payload=TerminalOutcomePayload("Completed", "x" * 100000))
    selects = []
    def observe(connection, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith("SELECT"):
            selects.append(statement)
    event.listen(test_database.engine.sync_engine, "before_cursor_execute", observe)
    try:
        async with transaction_factory() as tx:
            with pytest.raises(InvalidInput, match="cannot fit"):
                await RunHistoryRepository(tx).read_page(tenant_id=tenant, run_id=run, max_bytes=1000)
    finally:
        event.remove(test_database.engine.sync_engine, "before_cursor_execute", observe)
    assert any("octet_length(CAST(" in statement and ".payload AS TEXT))" in statement for statement in selects)
    assert not any("agent_run_history.payload," in statement for statement in selects)


async def test_page_byte_budget_returns_fitting_prefix_and_rejects_unknown_payload_version(transaction_factory):
    tenant, (run,) = await seed(transaction_factory)
    async with transaction_factory() as tx:
        repo = RunHistoryRepository(tx)
        for _ in range(2):
            await repo.append(tenant_id=tenant, run_id=run, payload=TerminalOutcomePayload("Completed", "x" * 500))
        page = await repo.read_page(tenant_id=tenant, run_id=run, max_bytes=1200)
        assert len(page.entries) == 1 and page.has_more
        await tx.session.execute(update(RunHistoryRecord).where(RunHistoryRecord.run_id == run,
            RunHistoryRecord.sequence == 2).values(payload_schema_version=2))
        with pytest.raises(InvalidHistory, match="version"):
            await repo.read_page(tenant_id=tenant, run_id=run, after_sequence=1)


async def test_cancelled_append_waiter_does_not_advance_history(transaction_factory):
    tenant, (run,) = await seed(transaction_factory)
    async def append():
        async with transaction_factory() as tx:
            return await RunHistoryRepository(tx).append(tenant_id=tenant, run_id=run,
                payload=RelatedInputPayload(InputContent("input")), source=source("input"))
    async with transaction_factory() as tx:
        await tx.session.scalar(select(RunRecord).where(RunRecord.id == run).with_for_update())
        waiter = asyncio.create_task(append())
        await asyncio.sleep(.03)
        assert not waiter.done()
        waiter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiter
    assert (await append()).entry.sequence == 1


async def test_non_input_commit_source_is_idempotent_without_becoming_unseen_input(transaction_factory):
    tenant, (run,) = await seed(transaction_factory)
    identity = source("model-step")
    payload = ModelStepPayload("step", 0, ModelStepResult("answer", (), "stop", ModelUsage(), "interaction", False))
    async with transaction_factory() as tx:
        repo = RunHistoryRepository(tx)
        first = await repo.append(tenant_id=tenant, run_id=run, payload=payload, source=identity)
        again = await repo.append(tenant_id=tenant, run_id=run, payload=payload, source=identity)
        assert first.appended and not again.appended
        assert first.entry == again.entry
        assert not await repo.has_unseen_related_input(tenant_id=tenant, run_id=run, after_read_boundary=0)


async def test_payload_growing_between_metadata_and_fetch_is_not_materialized(transaction_factory, monkeypatch):
    tenant, (run,) = await seed(transaction_factory)
    async with transaction_factory() as tx:
        await RunHistoryRepository(tx).append(tenant_id=tenant, run_id=run, payload=TerminalOutcomePayload("Completed", "small"))
    async with transaction_factory() as tx:
        repo = RunHistoryRepository(tx)
        original = repo._fetch
        async def enlarge_before_fetch(tenant_id, run_id, metadata):
            async with transaction_factory() as other:
                await other.session.execute(update(RunHistoryRecord).where(RunHistoryRecord.run_id == run).values(
                    payload={"status": "Completed", "output": "x" * 100000, "reason": None}))
            return await original(tenant_id, run_id, metadata)
        monkeypatch.setattr(repo, "_fetch", enlarge_before_fetch)
        with pytest.raises(InvalidHistory, match="changed"):
            await repo.read_page(tenant_id=tenant, run_id=run, max_bytes=1000)


async def test_missing_history_sequence_fails_instead_of_returning_empty_has_more_page(transaction_factory):
    tenant, (run,) = await seed(transaction_factory)
    async with transaction_factory() as tx:
        await tx.session.execute(update(RunRecord).where(RunRecord.id == run).values(latest_history_sequence=1))
        with pytest.raises(InvalidHistory, match="contiguous"):
            await RunHistoryRepository(tx).read_page(tenant_id=tenant, run_id=run)


async def test_page_limits_and_sequence_boundaries_fail_explicitly(transaction_factory):
    tenant, (run,) = await seed(transaction_factory)
    async with transaction_factory() as tx:
        repo = RunHistoryRepository(tx)
        await repo.append(tenant_id=tenant, run_id=run, payload=InitialInputPayload(InputContent("initial")), source=source("initial"))
        for options in ({"limit": 0}, {"limit": 101}, {"limit": True}, {"max_bytes": 0},
            {"max_bytes": -1}, {"max_bytes": MAX_PAGE_BYTES + 1}, {"max_bytes": True}, {"max_bytes": 1},
            {"after_sequence": -1}, {"after_sequence": True}, {"after_sequence": 1.5},
            {"after_sequence": 2}, {"through_sequence": -1}, {"through_sequence": True},
            {"through_sequence": 2}, {"after_sequence": 1, "through_sequence": 0}):
            with pytest.raises(InvalidInput):
                await repo.read_page(tenant_id=tenant, run_id=run, **options)
        empty = await repo.read_page(tenant_id=tenant, run_id=run, through_sequence=0)
        assert not empty.entries and not empty.has_more
        for boundary in (-1, True, 2):
            with pytest.raises(InvalidInput):
                await repo.has_unseen_related_input(tenant_id=tenant, run_id=run, after_read_boundary=boundary)


def test_source_identity_utf8_length_boundaries_and_redaction():
    owner = uuid4()
    SourceIdentity("a" * 64, owner, "x" * 512)
    SourceIdentity("中" * 21 + "a", owner, "中" * 170 + "aa")
    for kind, key in (("a" * 65, "key"), ("kind", "x" * 513), ("中" * 22, "key"),
        ("kind", "中" * 171), (" ", "key"), ("kind", ""), ("kind", "private\ud800")):
        with pytest.raises(InvalidInput) as error:
            SourceIdentity(kind, owner, key)
        assert "private" not in str(error.value)


async def test_near_codec_maximum_roundtrips_despite_jsonb_whitespace_expansion(transaction_factory):
    tenant, (run,) = await seed(transaction_factory)
    calls = tuple(ModelToolCall(str(index), "tool", '{}') for index in range(128))
    empty = ModelStepPayload("step", 0, ModelStepResult("", calls, "tool_calls", ModelUsage(), "interaction", False))
    encoded = encode_history(empty)
    overhead = len(json.dumps({"kind": encoded.kind, "version": encoded.version, "payload": encoded.payload},
        ensure_ascii=False, separators=(",", ":")).encode())
    value = ModelStepPayload("step", 0, ModelStepResult("x" * (MAX_RECORD_BYTES - overhead), calls,
        "tool_calls", ModelUsage(), "interaction", False))
    async with transaction_factory() as tx:
        await RunHistoryRepository(tx).append(tenant_id=tenant, run_id=run, payload=value)
    async with transaction_factory() as tx:
        stored_size = await tx.session.scalar(select(func.octet_length(cast(RunHistoryRecord.payload, Text))).where(
            RunHistoryRecord.run_id == run))
        assert stored_size > MAX_RECORD_BYTES
        page = await RunHistoryRepository(tx).read_page(tenant_id=tenant, run_id=run)
        assert page.entries[0].payload == value


async def test_corrupt_persisted_source_identity_is_history_error_not_caller_input_error(transaction_factory):
    tenant, runs = await seed(transaction_factory, 4)
    async with transaction_factory() as tx:
        repo = RunHistoryRepository(tx)
        for run, changes in zip(runs, ({"source_kind": " "}, {"source_key": ""},
            {"source_kind": "中" * 22}, {"source_key": "中" * 171}), strict=True):
            await repo.append(tenant_id=tenant, run_id=run, payload=RelatedInputPayload(InputContent("input")), source=source("input"))
            await tx.session.execute(update(RunHistoryRecord).where(RunHistoryRecord.run_id == run).values(**changes))
            with pytest.raises(InvalidHistory, match="source identity"):
                await repo.read_page(tenant_id=tenant, run_id=run)
