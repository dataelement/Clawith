import asyncio
from dataclasses import replace
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.infrastructure.errors import AccessDenied, InvalidInput
from app.modules.audit.models import AuditRecord
from app.modules.audit.public import (
    MAX_METADATA_BYTES,
    AgentActor,
    AsyncAuditSink,
    AuditObservation,
    AuditService,
    MembershipActor,
    PlatformAccountActor,
    SystemActor,
)
from app.modules.identity_tenant.public import IdentityService, TenantPrincipal


def observation(tenant_id, **changes):
    return replace(
        AuditObservation(
            tenant_id=tenant_id,
            actor=SystemActor("tests"),
            action="test.action",
            target_kind="fixture",
            target_reference="fixture",
            outcome="succeeded",
            metadata_schema_version=1,
            metadata={},
            occurred_at=datetime.now(UTC),
        ),
        **changes,
    )


async def provision(transaction_factory, role="tenant_admin"):
    async with transaction_factory() as tx:
        identity = IdentityService(tx)
        account = await identity.create_account()
        tenant = await identity.create_tenant(name="Audit tests")
        member = await identity.create_membership(
            tenant_id=tenant.id,
            account_id=account.id,
            display_name="Tester",
            role=role,
        )
    return TenantPrincipal(account_id=account.id, tenant_id=tenant.id, membership_id=member.id, role=role)


def sink_for(test_database, *, capacity=10, shutdown_timeout=2):
    return AsyncAuditSink(test_database.sessions, capacity=capacity, shutdown_timeout=shutdown_timeout)


@pytest.mark.asyncio
async def test_real_persistence_scoped_copied_reads_and_all_actor_kinds(
    transaction_factory,
    test_database,
    model_acceptance,
):
    from app.modules.agent.public import AgentService
    from app.modules.credential.public import CredentialKeyring, CredentialService, Secret
    from app.modules.model.public import ModelService
    from app.modules.run.models import RunRecord  # Run is schema-only at this stage.

    principal = await provision(transaction_factory)
    other = await provision(transaction_factory)
    keyring = CredentialKeyring(active_key_version="test", keys={"test": b"0" * 32})
    async with transaction_factory() as tx:
        credential = await CredentialService(
            tx,
            keyring,
        ).create(
            principal, kind="api_key", provider="test", label="test", secret=Secret("test-only"), owner_kind="tenant"
        )
        model = await ModelService(tx).create(
            principal,
            credential_id=credential.id,
            provider="test",
            model_name="test",
            endpoint="https://example.invalid",
            context_limit=1024,
            output_limit=256,
            capability_source="administrator",
            capabilities={"supports_tool_calling": True},
            settings_version=1,
            settings={"protocol": "openai_chat"},
            enabled=False,
        )
    accepted = await model_acceptance(principal, model, keyring)
    async with transaction_factory() as tx:
        await ModelService(tx).set_enabled(principal, model_id=model.id, enabled=True, acceptance=accepted)
        agent = await AgentService(tx).create(
            principal,
            name="test",
            soul="test",
            timezone="UTC",
            model_id=model.id,
        )
        second = await AgentService(tx).create(
            principal,
            name="second",
            soul="test",
            timezone="UTC",
            model_id=model.id,
        )
        run_id = uuid4()
        now = datetime.now(UTC)
        tx.session.add(
            RunRecord(
                id=run_id,
                tenant_id=principal.tenant_id,
                agent_id=agent.id,
                status="Running",
                initiator_kind="membership",
                initiator_owner_id=principal.membership_id,
                source_key="audit-tests",
                latest_history_sequence=0,
                created_at=now,
                started_at=now,
                updated_at=now,
            )
        )
    metadata = {"nested": {"items": ["original"]}}
    sink = sink_for(test_database)
    sink.start()
    for actor in (
        MembershipActor(principal.membership_id),
        PlatformAccountActor(principal.account_id),
        AgentActor(agent.id),
        AgentActor(agent.id, run_id),
        SystemActor("tests"),
    ):
        sink.emit(observation(principal.tenant_id, actor=actor, metadata=metadata))
    sink.emit(observation(other.tenant_id))
    sink.emit(observation(principal.tenant_id, actor=AgentActor(second.id, run_id)))
    metadata["nested"]["items"].append("changed")
    await sink.close()
    assert sink.statistics.persisted == 6
    assert sink.statistics.write_failed == 1
    async with transaction_factory() as tx:
        rows = await AuditService(tx).list(principal)
        assert len(rows) == 5
        assert {type(row.actor) for row in rows} == {
            MembershipActor,
            PlatformAccountActor,
            AgentActor,
            SystemActor,
        }
        assert all(row.metadata == {"nested": {"items": ["original"]}} for row in rows)
        rows[0].metadata["nested"]["items"].append("read mutation")
        again = await AuditService(tx).list(principal)
        assert again[0].metadata == {"nested": {"items": ["original"]}}
        assert len(await AuditService(tx).list(principal, limit=1, offset=3)) == 1
        with pytest.raises(InvalidInput):
            await AuditService(tx).list(principal, limit=101)
        with pytest.raises(InvalidInput):
            await AuditService(tx).list(principal, offset=-1)


@pytest.mark.asyncio
async def test_cross_tenant_failed_write_does_not_undo_business_and_consumer_continues(
    transaction_factory,
    test_database,
):
    principal = await provision(transaction_factory)
    other = await provision(transaction_factory)
    sink = sink_for(test_database)
    sink.start()
    sink.emit(observation(principal.tenant_id, actor=MembershipActor(other.membership_id)))
    sink.emit(observation(principal.tenant_id))
    await sink.close()
    assert sink.statistics.write_failed == 1
    assert sink.statistics.persisted == 1
    async with transaction_factory() as tx:
        identity = await IdentityService(tx).resolve_identity(
            account_id=principal.account_id,
            tenant_id=principal.tenant_id,
        )
        assert identity.principal.membership_id == principal.membership_id
        assert len(await AuditService(tx).list(principal)) == 1


@pytest.mark.asyncio
async def test_actor_run_must_match_agent_and_tenant(transaction_factory, test_database):
    principal = await provision(transaction_factory)
    sink = sink_for(test_database)
    sink.start()
    sink.emit(observation(principal.tenant_id, actor=AgentActor(uuid4(), uuid4())))
    await sink.close()
    assert sink.statistics.write_failed == 1


@pytest.mark.asyncio
async def test_audit_actor_check_rejects_mixed_fields(transaction_factory):
    principal = await provision(transaction_factory)
    with pytest.raises(IntegrityError):
        async with transaction_factory() as tx:
            tx.session.add(
                AuditRecord(
                    id=uuid4(),
                    tenant_id=principal.tenant_id,
                    actor_kind="membership",
                    membership_id=principal.membership_id,
                    platform_account_id=principal.account_id,
                    action="test",
                    target_kind="fixture",
                    target_reference="fixture",
                    outcome="denied",
                    metadata_schema_version=1,
                    metadata_payload={},
                    occurred_at=datetime.now(UTC),
                )
            )
            await tx.session.flush()


@pytest.mark.asyncio
async def test_read_requires_admin_and_rejects_unknown_stored_version(transaction_factory):
    member = await provision(transaction_factory, role="member")
    principal = await provision(transaction_factory)
    async with transaction_factory() as tx:
        with pytest.raises(AccessDenied):
            await AuditService(tx).list(member)
        tx.session.add(
            AuditRecord(
                id=uuid4(),
                tenant_id=principal.tenant_id,
                actor_kind="system",
                system_component="tests",
                action="test",
                target_kind="fixture",
                target_reference="fixture",
                outcome="succeeded",
                metadata_schema_version=2,
                metadata_payload={},
                occurred_at=datetime.now(UTC),
            )
        )
    async with transaction_factory() as tx:
        with pytest.raises(InvalidInput, match="unsupported Audit metadata schema"):
            await AuditService(tx).list(principal)


@pytest.mark.asyncio
async def test_invalid_metadata_bounds_and_secret_free_counters(test_database, caplog):
    sink = sink_for(test_database, capacity=20)
    sink.start()
    for changes in (
        {"metadata": {"nested": {"password": "never-log-this"}}},
        {"metadata": {"text": "界" * MAX_METADATA_BYTES}},
        {"metadata": {"value": float("nan")}},
        {"metadata": {"value": list(range(101))}},
        {"metadata_schema_version": 2},
        {"occurred_at": datetime.now(UTC).replace(tzinfo=None)},
        {"action": ""},
        {"outcome": "unknown"},
    ):
        sink.emit(observation(uuid4(), **changes))
    assert sink.statistics.dropped_invalid == 8
    assert sink.statistics.accepted == 0
    await sink.close()
    assert "never-log-this" not in caplog.text
    assert "never-log-this" not in repr(sink.statistics)


@pytest.mark.asyncio
async def test_metadata_complete_encoded_byte_boundary(transaction_factory, test_database):
    principal = await provision(transaction_factory)
    sink = sink_for(test_database)
    sink.start()
    # Compact JSON encoding of {"x":"..."} has eight framing bytes.
    for size in (MAX_METADATA_BYTES - 9, MAX_METADATA_BYTES - 8, MAX_METADATA_BYTES - 7):
        sink.emit(observation(principal.tenant_id, metadata={"x": "a" * size}))
    await sink.close()
    assert sink.statistics.persisted == 2
    assert sink.statistics.dropped_invalid == 1


@pytest.mark.asyncio
async def test_submission_never_waits_for_storage_and_full_closed_are_bounded(
    monkeypatch,
    test_database,
):
    sink = sink_for(test_database, capacity=1)
    entered, release = asyncio.Event(), asyncio.Event()

    async def blocked_write(pending):
        entered.set()
        await release.wait()

    monkeypatch.setattr(sink, "_persist", blocked_write)
    sink.emit(observation(uuid4()))
    assert sink.statistics.dropped_closed == 1
    sink.start()
    sink.emit(observation(uuid4()))
    await asyncio.wait_for(entered.wait(), 1)
    sink.emit(observation(uuid4()))
    sink.emit(observation(uuid4()))
    assert sink.statistics.accepted == 2
    assert sink.statistics.dropped_full == 1
    release.set()
    await sink.close()
    sink.emit(observation(uuid4()))
    assert sink.statistics.persisted == 2
    assert sink.statistics.dropped_closed == 2
    with pytest.raises(RuntimeError):
        sink.start()
    assert sink._task.done()


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel_close", [False, True])
async def test_shutdown_cancels_real_db_wait_and_releases_connection(
    monkeypatch,
    test_database,
    cancel_close,
):
    sink = sink_for(test_database, capacity=2, shutdown_timeout=0.05)
    entered = asyncio.Event()

    async def slow_write(pending):
        async with test_database.sessions() as session, session.begin():
            await session.execute(text("SELECT 1"))
            entered.set()
            await session.execute(text("SELECT pg_sleep(30)"))

    monkeypatch.setattr(sink, "_persist", slow_write)
    sink.start()
    sink.emit(observation(uuid4()))
    await asyncio.wait_for(entered.wait(), 2)
    sink.emit(observation(uuid4()))
    close = asyncio.create_task(sink.close())
    if cancel_close:
        await asyncio.sleep(0)
        close.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(close, 2)
    else:
        await asyncio.wait_for(close, 2)
    assert sink._task.done()
    assert sink.statistics.dropped_shutdown == 2
    assert test_database.engine.pool.checkedout() == 0


def test_public_write_port_removed_and_explicit_configuration_required():
    assert not hasattr(AuditService, "append")
    for capacity in (0, -1, True):
        with pytest.raises(ValueError):
            AsyncAuditSink(None, capacity=capacity, shutdown_timeout=1)
    for timeout in (0, -1, float("inf"), float("nan")):
        with pytest.raises(ValueError):
            AsyncAuditSink(None, capacity=1, shutdown_timeout=timeout)


@pytest.mark.asyncio
async def test_concurrent_close_owns_one_cleanup_and_empty_close_is_idempotent(test_database):
    sink = sink_for(test_database)
    sink.start()
    await asyncio.gather(sink.close(), sink.close())
    await sink.close()
    assert sink._task.done()
    assert sink._closing.done()
    assert sink.statistics.accepted == 0
    unopened = sink_for(test_database)
    await unopened.close()
    unopened.emit(observation(uuid4()))
    assert unopened.statistics.dropped_closed == 1
