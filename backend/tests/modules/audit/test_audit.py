from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from sqlalchemy.exc import IntegrityError

from app.infrastructure.errors import AccessDenied, InvalidInput
from app.modules.agent.public import AgentService
from app.modules.audit.models import AuditRecord
from app.modules.audit.public import (
    MAX_METADATA_BYTES,
    METADATA_SCHEMA_VERSION,
    AgentActor,
    AuditService,
    MembershipActor,
    PlatformAccountActor,
    SystemActor,
)
from app.modules.credential.public import CredentialKeyring, CredentialService, Secret
from app.modules.identity_tenant.public import IdentityService, TenantPrincipal
from app.modules.model.public import ModelService

# Run has schema only in G003, so this constraint fixture has no public service.
from app.modules.run.models import RunRecord


async def _provision_tenant(
    transaction,
    *,
    tenant_id: UUID,
    account_id: UUID,
    role: str = "tenant_admin",
) -> TenantPrincipal:
    identity = IdentityService(transaction)
    await identity.create_account(account_id=account_id)
    await identity.create_tenant(name=f"Tenant {tenant_id.hex[:6]}", tenant_id=tenant_id)
    membership = await identity.create_membership(
        tenant_id=tenant_id,
        account_id=account_id,
        display_name="Audit tester",
        role=role,  # type: ignore[arg-type]
    )
    return TenantPrincipal(
        account_id=account_id,
        membership_id=membership.id,
        tenant_id=tenant_id,
        role=membership.role,
    )


async def _insert_agent(transaction, principal: TenantPrincipal) -> UUID:
    credential = await CredentialService(
        transaction,
        CredentialKeyring(active_key_version="test", keys={"test": b"0" * 32}),
    ).create(
        principal,
        kind="api_key",
        provider="test",
        label="Audit fixture",
        secret=Secret("test-only"),
        owner_kind="tenant",
    )
    model = await ModelService(transaction).create(
        principal,
        credential_id=credential.id,
        provider="test",
        model_name="audit-fixture",
        endpoint="https://example.invalid",
        context_limit=1024,
        output_limit=256,
        capability_source="administrator",
        capabilities={"supports_tool_calling": True},
        settings_version=1,
        settings={},
    )
    agent = await AgentService(transaction).create(
        principal,
        name="Audit fixture",
        soul="Test only",
        timezone="UTC",
        model_id=model.id,
    )
    return agent.id


@pytest.mark.asyncio
async def test_all_actor_kinds_append_and_listing_is_tenant_scoped(
    transaction_factory,
) -> None:
    tenant_id = uuid4()
    other_tenant_id = uuid4()
    account_id = uuid4()
    other_account_id = uuid4()
    async with transaction_factory() as transaction:
        principal = await _provision_tenant(
            transaction, tenant_id=tenant_id, account_id=account_id
        )
        other_principal = await _provision_tenant(
            transaction, tenant_id=other_tenant_id, account_id=other_account_id
        )
        agent_id = await _insert_agent(transaction, principal)
        audit = AuditService(transaction)
        actors = (
            MembershipActor(principal.membership_id),
            PlatformAccountActor(principal.account_id),
            AgentActor(agent_id),
            SystemActor("test-suite"),
        )
        for index, actor in enumerate(actors):
            await audit.append(
                tenant_id=tenant_id,
                actor=actor,
                action=f"audit.action.{index}",
                target_kind="fixture",
                target_reference=str(index),
                outcome="succeeded",
                metadata_schema_version=1,
                metadata={"index": index},
            )
        await audit.append(
            tenant_id=other_tenant_id,
            actor=MembershipActor(other_principal.membership_id),
            action="other.action",
            target_kind="fixture",
            target_reference="other",
            outcome="succeeded",
            metadata_schema_version=1,
            metadata={},
        )

    async with transaction_factory() as transaction:
        records = await AuditService(transaction).list(principal, limit=10, offset=0)

    assert len(records) == 4
    assert {record.tenant_id for record in records} == {tenant_id}
    assert {type(record.actor) for record in records} == {
        MembershipActor,
        PlatformAccountActor,
        AgentActor,
        SystemActor,
    }


@pytest.mark.asyncio
async def test_audit_actor_check_rejects_mixed_actor_fields(transaction_factory) -> None:
    tenant_id = uuid4()
    account_id = uuid4()
    async with transaction_factory() as transaction:
        principal = await _provision_tenant(
            transaction, tenant_id=tenant_id, account_id=account_id
        )

    with pytest.raises(IntegrityError):
        async with transaction_factory() as transaction:
            transaction.session.add(
                AuditRecord(
                    id=uuid4(),
                    tenant_id=tenant_id,
                    actor_kind="membership",
                    membership_id=principal.membership_id,
                    platform_account_id=account_id,
                    agent_id=None,
                    run_id=None,
                    system_component=None,
                    action="mixed.actor",
                    target_kind="fixture",
                    target_reference="mixed",
                    outcome="denied",
                    metadata_schema_version=1,
                    metadata_payload={},
                    occurred_at=datetime.now(UTC),
                )
            )
            await transaction.session.flush()


@pytest.mark.asyncio
async def test_membership_actor_cannot_cross_tenants(transaction_factory) -> None:
    first_tenant_id = uuid4()
    second_tenant_id = uuid4()
    async with transaction_factory() as transaction:
        first = await _provision_tenant(
            transaction, tenant_id=first_tenant_id, account_id=uuid4()
        )
        await _provision_tenant(
            transaction, tenant_id=second_tenant_id, account_id=uuid4()
        )

    with pytest.raises(InvalidInput, match="target Tenant"):
        async with transaction_factory() as transaction:
            await AuditService(transaction).append(
                tenant_id=second_tenant_id,
                actor=MembershipActor(first.membership_id),
                action="cross.tenant",
                target_kind="fixture",
                target_reference="cross",
                outcome="denied",
                metadata_schema_version=1,
                metadata={},
            )


@pytest.mark.asyncio
async def test_agent_run_must_correspond_to_actor_agent_and_tenant(
    transaction_factory,
) -> None:
    tenant_id = uuid4()
    async with transaction_factory() as transaction:
        principal = await _provision_tenant(
            transaction, tenant_id=tenant_id, account_id=uuid4()
        )
        first_agent_id = await _insert_agent(transaction, principal)
        second_agent_id = await _insert_agent(transaction, principal)
        run_id = uuid4()
        now = datetime.now(UTC)
        transaction.session.add(
            RunRecord(
                id=run_id,
                tenant_id=tenant_id,
                agent_id=first_agent_id,
                parent_run_id=None,
                status="Running",
                initiator_kind="membership",
                initiator_owner_id=principal.membership_id,
                source_key="audit-fixture",
                latest_history_sequence=0,
                active_waiting_reference=None,
                created_at=now,
                started_at=now,
                updated_at=now,
                finished_at=None,
            )
        )
        await transaction.session.flush()

    with pytest.raises(InvalidInput, match="target Tenant or Run"):
        async with transaction_factory() as transaction:
            await AuditService(transaction).append(
                tenant_id=tenant_id,
                actor=AgentActor(second_agent_id, run_id),
                action="wrong.run",
                target_kind="fixture",
                target_reference="run",
                outcome="failed",
                metadata_schema_version=1,
                metadata={},
            )


@pytest.mark.asyncio
async def test_metadata_rejects_secret_fields_and_complete_encoded_size(
    transaction_factory,
) -> None:
    tenant_id = uuid4()
    async with transaction_factory() as transaction:
        await IdentityService(transaction).create_tenant(
            name="Metadata", tenant_id=tenant_id
        )
        audit = AuditService(transaction)
        with pytest.raises(InvalidInput, match="Secret fields"):
            await audit.append(
                tenant_id=tenant_id,
                actor=SystemActor("test-suite"),
                action="secret.rejected",
                target_kind="fixture",
                target_reference="secret",
                outcome="denied",
                metadata_schema_version=1,
                metadata={"nested": {"password": "must-not-persist"}},
            )
        with pytest.raises(InvalidInput, match="bytes"):
            await audit.append(
                tenant_id=tenant_id,
                actor=SystemActor("test-suite"),
                action="oversize.rejected",
                target_kind="fixture",
                target_reference="oversize",
                outcome="denied",
                metadata_schema_version=1,
                metadata={"message": "界" * MAX_METADATA_BYTES},
            )
        with pytest.raises(InvalidInput, match="unsupported Audit metadata schema"):
            await audit.append(
                tenant_id=tenant_id,
                actor=SystemActor("test-suite"),
                action="version.rejected",
                target_kind="fixture",
                target_reference="version",
                outcome="denied",
                metadata_schema_version=METADATA_SCHEMA_VERSION + 1,
                metadata={},
            )


@pytest.mark.asyncio
async def test_listing_rejects_unknown_stored_metadata_schema_version(
    transaction_factory,
) -> None:
    tenant_id = uuid4()
    async with transaction_factory() as transaction:
        principal = await _provision_tenant(
            transaction, tenant_id=tenant_id, account_id=uuid4()
        )
        transaction.session.add(
            AuditRecord(
                id=uuid4(),
                tenant_id=tenant_id,
                actor_kind="system",
                membership_id=None,
                platform_account_id=None,
                agent_id=None,
                run_id=None,
                system_component="future-writer",
                action="future.version",
                target_kind="fixture",
                target_reference="future",
                outcome="succeeded",
                metadata_schema_version=METADATA_SCHEMA_VERSION + 1,
                metadata_payload={},
                occurred_at=datetime.now(UTC),
            )
        )
        await transaction.session.flush()

    async with transaction_factory() as transaction:
        with pytest.raises(InvalidInput, match="unsupported Audit metadata schema"):
            await AuditService(transaction).list(principal, limit=10, offset=0)


@pytest.mark.asyncio
async def test_listing_requires_captured_tenant_admin(transaction_factory) -> None:
    tenant_id = uuid4()
    async with transaction_factory() as transaction:
        member = await _provision_tenant(
            transaction, tenant_id=tenant_id, account_id=uuid4(), role="member"
        )
        await AuditService(transaction).append(
            tenant_id=tenant_id,
            actor=MembershipActor(member.membership_id),
            action="member.action",
            target_kind="fixture",
            target_reference="member",
            outcome="succeeded",
            metadata_schema_version=1,
            metadata={},
        )

    async with transaction_factory() as transaction:
        with pytest.raises(AccessDenied):
            await AuditService(transaction).list(member, limit=10, offset=0)


@pytest.mark.asyncio
async def test_required_audit_failure_rolls_back_cross_owner_mutation(
    transaction_factory,
) -> None:
    account_id = uuid4()
    tenant_id = uuid4()
    with pytest.raises(InvalidInput):
        async with transaction_factory() as transaction:
            identity = IdentityService(transaction)
            await identity.create_account(account_id=account_id)
            await identity.create_tenant(name="Must roll back", tenant_id=tenant_id)
            await AuditService(transaction).append(
                tenant_id=tenant_id,
                actor=MembershipActor(uuid4()),
                action="provision.failed",
                target_kind="tenant",
                target_reference=str(tenant_id),
                outcome="failed",
                metadata_schema_version=1,
                metadata={},
            )

    async with transaction_factory() as transaction:
        identity = IdentityService(transaction)
        account = await identity.create_account(account_id=account_id)
        tenant = await identity.create_tenant(name="Retried", tenant_id=tenant_id)

    assert account.id == account_id
    assert tenant.id == tenant_id
