"""Complete S1 schema graph and trust-boundary constraints."""

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import ForeignKeyConstraint, UniqueConstraint
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.database import Base
from app.modules.agent.models import AgentRecord
from app.modules.audit.models import AuditRecord
from app.modules.auth.models import LoginSessionRecord, LoginVerifierRecord
from app.modules.context.models import ContextProjectionRecord
from app.modules.credential.models import CredentialRecord
from app.modules.identity_tenant.models import AccountRecord, MembershipRecord, TenantRecord
from app.modules.model.models import ModelRecord, ProviderContinuationRecord, TenantModelDefaultRecord
from app.modules.permission.models import AgentVisibilityGrantRecord, AgentVisibilityRecord
from app.modules.run.models import RunHistoryRecord, RunRecord, RunSnapshotRecord

S1_TABLE_OWNERS = {
    "credentials": "credential",
    "llm_models": "model",
    "tenant_model_defaults": "model",
    "provider_continuation_states": "model",
    "agents": "agent",
    "agent_visibilities": "permission",
    "agent_visibility_grants": "permission",
    "login_verifiers": "auth",
    "login_sessions": "auth",
    "audit_records": "audit",
    "agent_runs": "run",
    "agent_run_snapshots": "run",
    "agent_run_history": "run",
    "run_context_projections": "context",
}


def _now() -> datetime:
    return datetime.now(UTC)


async def _seed_to_agent(session: AsyncSession, *, suffix: str = "a") -> dict[str, Any]:
    now = _now()
    account = AccountRecord(enabled=True, platform_role=None, created_at=now, updated_at=now)
    tenant = TenantRecord(name=f"Tenant {suffix}", enabled=True, created_at=now, updated_at=now)
    session.add_all([account, tenant])
    await session.flush()
    membership = MembershipRecord(
        tenant_id=tenant.id,
        account_id=account.id,
        display_name=f"Member {suffix}",
        avatar=None,
        title=None,
        role="tenant_admin",
        enabled=True,
        joined_at=now,
        updated_at=now,
    )
    session.add(membership)
    await session.flush()
    credential = CredentialRecord(
        tenant_id=tenant.id,
        membership_owner_id=None,
        agent_owner_id=None,
        kind="model_api_key",
        provider="example",
        label=f"Key {suffix}",
        encrypted_payload=b"ciphertext",
        payload_version=1,
        key_version="test-key-1",
        expires_at=None,
        revoked_at=None,
        created_at=now,
        updated_at=now,
    )
    session.add(credential)
    await session.flush()
    model = ModelRecord(
        tenant_id=tenant.id,
        credential_id=credential.id,
        credential_owner_kind="tenant",
        provider="example",
        model_name=f"model-{suffix}",
        endpoint="https://example.invalid/v1",
        context_limit=8192,
        output_limit=1024,
        capability_source="administrator",
        capabilities={"tool_calling": True},
        settings_version=1,
        settings={},
        enabled=True,
        archived_at=None,
        created_at=now,
        updated_at=now,
    )
    session.add(model)
    await session.flush()
    agent = AgentRecord(
        tenant_id=tenant.id,
        model_id=model.id,
        created_by_membership_id=membership.id,
        name=f"Agent {suffix}",
        avatar=None,
        description=None,
        greeting=None,
        soul="Be useful.",
        timezone="UTC",
        enabled=True,
        archived_at=None,
        created_at=now,
        updated_at=now,
    )
    session.add(agent)
    await session.flush()
    return {
        "now": now,
        "account": account,
        "tenant": tenant,
        "membership": membership,
        "credential": credential,
        "model": model,
        "agent": agent,
    }


def test_s1_registers_exact_owner_metadata_and_tenant_identity_keys() -> None:
    assert {name: Base.metadata.tables[name].info["owner"] for name in S1_TABLE_OWNERS} == S1_TABLE_OWNERS
    for name in S1_TABLE_OWNERS:
        table = Base.metadata.tables[name]
        if "tenant_id" not in table.c:
            continue
        unique_columns = {
            tuple(column.name for column in constraint.columns)
            for constraint in table.constraints
            if isinstance(constraint, UniqueConstraint)
        }
        if "id" in table.c:
            expected_identity = ("tenant_id", "id")
        elif name == "agent_run_history":
            expected_identity = ("tenant_id", "run_id", "sequence")
        else:
            expected_identity = ("tenant_id", "run_id")
        assert expected_identity in unique_columns, name


def test_s1_cross_owner_foreign_keys_fail_closed_with_restrict() -> None:
    for name in S1_TABLE_OWNERS:
        table = Base.metadata.tables[name]
        for constraint in table.constraints:
            if isinstance(constraint, ForeignKeyConstraint):
                assert all(element.ondelete == "RESTRICT" for element in constraint.elements), constraint.name or name


def test_s1_has_no_live_authorization_generation_or_dependency_projection() -> None:
    assert "run_authorization_dependencies" not in Base.metadata.tables
    assert all("authorization_generation" not in table.c for table in Base.metadata.tables.values())
    continuation = Base.metadata.tables["provider_continuation_states"]
    assert continuation.c.encryption_version.nullable is False
    assert continuation.c.key_version.nullable is False
    credential_unique_keys = {
        tuple(column.name for column in constraint.columns)
        for constraint in Base.metadata.tables["credentials"].constraints
        if isinstance(constraint, UniqueConstraint)
    }
    assert ("tenant_id", "id", "owner_kind", "owner_id") in credential_unique_keys


@pytest.mark.asyncio
async def test_s1_accepts_one_complete_foundation_graph(db_session: AsyncSession) -> None:
    seeded = await _seed_to_agent(db_session)
    now = seeded["now"]
    tenant = seeded["tenant"]
    account = seeded["account"]
    membership = seeded["membership"]
    model = seeded["model"]
    agent = seeded["agent"]
    db_session.add_all(
        [
            TenantModelDefaultRecord(tenant_id=tenant.id, model_id=model.id, created_at=now, updated_at=now),
            AgentVisibilityRecord(
                tenant_id=tenant.id, agent_id=agent.id, visibility="restricted", created_at=now, updated_at=now
            ),
            AgentVisibilityGrantRecord(
                tenant_id=tenant.id,
                agent_id=agent.id,
                membership_id=membership.id,
                source_agent_id=None,
                granted_by_membership_id=membership.id,
                created_at=now,
                revoked_at=None,
                updated_at=now,
            ),
            LoginVerifierRecord(
                account_id=account.id,
                login_name="ada@example.test",
                password_hash="encoded-hash",
                kdf_name="pbkdf2_hmac_sha256",
                kdf_version=1,
                created_at=now,
                updated_at=now,
            ),
            LoginSessionRecord(
                tenant_id=tenant.id,
                account_id=account.id,
                membership_id=membership.id,
                token_hash="token-digest",
                frozen_authorization={"role": "tenant_admin", "agent_ids": [str(agent.id)]},
                authorization_schema_version=1,
                created_at=now,
                expires_at=now + timedelta(hours=24),
                logged_out_at=None,
            ),
        ]
    )
    await db_session.flush()
    run = RunRecord(
        tenant_id=tenant.id,
        agent_id=agent.id,
        parent_run_id=None,
        status="Running",
        initiator_kind="session",
        initiator_owner_id=membership.id,
        source_key="input-1",
        latest_history_sequence=1,
        active_waiting_reference=None,
        created_at=now,
        started_at=now,
        updated_at=now,
        finished_at=None,
    )
    db_session.add(run)
    await db_session.flush()
    db_session.add_all(
        [
            RunSnapshotRecord(
                run_id=run.id,
                tenant_id=tenant.id,
                payload_kind="run_snapshot",
                schema_version=1,
                payload={"model_id": str(model.id)},
                content_hash="a" * 64,
                created_at=now,
            ),
            RunHistoryRecord(
                run_id=run.id,
                sequence=1,
                tenant_id=tenant.id,
                payload_kind="initial_input",
                payload_schema_version=1,
                payload={"text": "hello"},
                source_kind="session_input",
                source_owner_id=membership.id,
                source_key="input-1",
                created_at=now,
            ),
            ContextProjectionRecord(
                run_id=run.id,
                tenant_id=tenant.id,
                payload_kind="compaction_base",
                payload_schema_version=1,
                payload={"summary": "hello"},
                coverage_sequence=1,
                rebuilt_at=now,
                updated_at=now,
            ),
            ProviderContinuationRecord(
                tenant_id=tenant.id,
                run_id=run.id,
                model_id=model.id,
                payload_kind="required_continuation",
                payload_schema_version=1,
                encryption_version=1,
                key_version="test-key-1",
                encrypted_payload=b"opaque-ciphertext",
                created_at=now,
                updated_at=now,
            ),
            AuditRecord(
                tenant_id=tenant.id,
                actor_kind="membership",
                membership_id=membership.id,
                platform_account_id=None,
                agent_id=None,
                run_id=None,
                system_component=None,
                action="agent.created",
                target_kind="agent",
                target_reference=str(agent.id),
                outcome="succeeded",
                metadata_schema_version=1,
                metadata_payload={},
                occurred_at=now,
            ),
        ]
    )
    await db_session.flush()


@pytest.mark.asyncio
async def test_s1_rejects_cross_tenant_model_credential_binding(db_session: AsyncSession) -> None:
    first = await _seed_to_agent(db_session, suffix="a")
    second = await _seed_to_agent(db_session, suffix="b")
    now = _now()
    db_session.add(
        ModelRecord(
            tenant_id=second["tenant"].id,
            credential_id=first["credential"].id,
            credential_owner_kind="tenant",
            provider="example",
            model_name="cross-tenant",
            endpoint="https://example.invalid/v1",
            context_limit=8192,
            output_limit=1024,
            capability_source="administrator",
            capabilities={"tool_calling": True},
            settings_version=1,
            settings={},
            enabled=True,
            archived_at=None,
            created_at=now,
            updated_at=now,
        )
    )
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_s1_rejects_invalid_run_and_login_shapes(db_session: AsyncSession) -> None:
    seeded = await _seed_to_agent(db_session)
    now = seeded["now"]
    db_session.add(
        LoginSessionRecord(
            tenant_id=seeded["tenant"].id,
            account_id=seeded["account"].id,
            membership_id=seeded["membership"].id,
            token_hash="expired-at-creation",
            frozen_authorization={},
            authorization_schema_version=1,
            created_at=now,
            expires_at=now,
            logged_out_at=None,
        )
    )
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_s1_rejects_login_account_membership_mismatch_within_one_tenant(
    db_session: AsyncSession,
) -> None:
    seeded = await _seed_to_agent(db_session)
    now = seeded["now"]
    other_account = AccountRecord(enabled=True, platform_role=None, created_at=now, updated_at=now)
    db_session.add(other_account)
    await db_session.flush()
    other_membership = MembershipRecord(
        tenant_id=seeded["tenant"].id,
        account_id=other_account.id,
        display_name="Other member",
        avatar=None,
        title=None,
        role="member",
        enabled=True,
        joined_at=now,
        updated_at=now,
    )
    db_session.add(other_membership)
    await db_session.flush()
    db_session.add(
        LoginSessionRecord(
            tenant_id=seeded["tenant"].id,
            account_id=seeded["account"].id,
            membership_id=other_membership.id,
            token_hash="mismatched-account-membership",
            frozen_authorization={},
            authorization_schema_version=1,
            created_at=now,
            expires_at=now + timedelta(hours=1),
            logged_out_at=None,
        )
    )
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_s1_rejects_ambiguous_visibility_grantee(db_session: AsyncSession) -> None:
    seeded = await _seed_to_agent(db_session)
    db_session.add(
        AgentVisibilityGrantRecord(
            tenant_id=seeded["tenant"].id,
            agent_id=seeded["agent"].id,
            membership_id=seeded["membership"].id,
            source_agent_id=seeded["agent"].id,
            granted_by_membership_id=seeded["membership"].id,
            created_at=seeded["now"],
            revoked_at=None,
            updated_at=seeded["now"],
        )
    )
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_s1_rejects_membership_credential_as_a_model_binding(db_session: AsyncSession) -> None:
    seeded = await _seed_to_agent(db_session)
    now = seeded["now"]
    credential = CredentialRecord(
        tenant_id=seeded["tenant"].id,
        membership_owner_id=seeded["membership"].id,
        agent_owner_id=None,
        kind="personal_api_key",
        provider="example",
        label="Personal key",
        encrypted_payload=b"ciphertext",
        payload_version=1,
        key_version="test-key-1",
        expires_at=None,
        revoked_at=None,
        created_at=now,
        updated_at=now,
    )
    db_session.add(credential)
    await db_session.flush()
    assert credential.owner_kind == "membership"
    assert credential.owner_id == seeded["membership"].id
    db_session.add(
        ModelRecord(
            tenant_id=seeded["tenant"].id,
            credential_id=credential.id,
            credential_owner_kind="tenant",
            provider="example",
            model_name="invalid-owner",
            endpoint="https://example.invalid/v1",
            context_limit=8192,
            output_limit=1024,
            capability_source="administrator",
            capabilities={"tool_calling": True},
            settings_version=1,
            settings={},
            enabled=True,
            archived_at=None,
            created_at=now,
            updated_at=now,
        )
    )
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid_version", [0, -1])
async def test_s1_rejects_nonpositive_credential_payload_version(
    db_session: AsyncSession, invalid_version: int
) -> None:
    seeded = await _seed_to_agent(db_session)
    db_session.add(
        CredentialRecord(
            tenant_id=seeded["tenant"].id,
            membership_owner_id=seeded["membership"].id,
            agent_owner_id=None,
            kind="personal_api_key",
            provider="example",
            label="Invalid envelope",
            encrypted_payload=b"ciphertext",
            payload_version=invalid_version,
            key_version="test-key-1",
            expires_at=None,
            revoked_at=None,
            created_at=seeded["now"],
            updated_at=seeded["now"],
        )
    )
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid_version", [0, -1])
async def test_s1_rejects_nonpositive_model_settings_version(
    db_session: AsyncSession, invalid_version: int
) -> None:
    seeded = await _seed_to_agent(db_session)
    now = seeded["now"]
    db_session.add(
        ModelRecord(
            tenant_id=seeded["tenant"].id,
            credential_id=seeded["credential"].id,
            credential_owner_kind="tenant",
            provider="example",
            model_name="invalid-settings-version",
            endpoint="https://example.invalid/v1",
            context_limit=8192,
            output_limit=1024,
            capability_source="administrator",
            capabilities={"tool_calling": True},
            settings_version=invalid_version,
            settings={},
            enabled=True,
            archived_at=None,
            created_at=now,
            updated_at=now,
        )
    )
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_s1_rejects_invalid_run_status_shape(db_session: AsyncSession) -> None:
    seeded = await _seed_to_agent(db_session)
    now = seeded["now"]
    db_session.add(
        RunRecord(
            tenant_id=seeded["tenant"].id,
            agent_id=seeded["agent"].id,
            parent_run_id=None,
            status="Running",
            initiator_kind="session",
            initiator_owner_id=seeded["membership"].id,
            source_key="input-1",
            latest_history_sequence=0,
            active_waiting_reference="waiting-while-running",
            created_at=now,
            started_at=now,
            updated_at=now,
            finished_at=None,
        )
    )
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_s1_rejects_a_run_as_its_own_parent(db_session: AsyncSession) -> None:
    seeded = await _seed_to_agent(db_session)
    now = seeded["now"]
    run_id = uuid4()
    db_session.add(
        RunRecord(
            id=run_id,
            tenant_id=seeded["tenant"].id,
            agent_id=seeded["agent"].id,
            parent_run_id=run_id,
            status="Running",
            initiator_kind="session",
            initiator_owner_id=seeded["membership"].id,
            source_key="self-parent",
            latest_history_sequence=0,
            active_waiting_reference=None,
            created_at=now,
            started_at=now,
            updated_at=now,
            finished_at=None,
        )
    )
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_s1_rejects_nonpositive_run_history_sequence(db_session: AsyncSession) -> None:
    seeded = await _seed_to_agent(db_session)
    now = seeded["now"]
    run = RunRecord(
        tenant_id=seeded["tenant"].id,
        agent_id=seeded["agent"].id,
        parent_run_id=None,
        status="Running",
        initiator_kind="session",
        initiator_owner_id=seeded["membership"].id,
        source_key="input-1",
        latest_history_sequence=0,
        active_waiting_reference=None,
        created_at=now,
        started_at=now,
        updated_at=now,
        finished_at=None,
    )
    db_session.add(run)
    await db_session.flush()
    db_session.add(
        RunHistoryRecord(
            run_id=run.id,
            sequence=0,
            tenant_id=seeded["tenant"].id,
            payload_kind="initial_input",
            payload_schema_version=1,
            payload={},
            source_kind="session_input",
            source_owner_id=seeded["membership"].id,
            source_key="input-1",
            created_at=now,
        )
    )
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_s1_rejects_mixed_audit_actor_identity(db_session: AsyncSession) -> None:
    seeded = await _seed_to_agent(db_session)
    db_session.add(
        AuditRecord(
            tenant_id=seeded["tenant"].id,
            actor_kind="membership",
            membership_id=seeded["membership"].id,
            platform_account_id=seeded["account"].id,
            agent_id=None,
            run_id=None,
            system_component=None,
            action="invalid.actor",
            target_kind="agent",
            target_reference=str(seeded["agent"].id),
            outcome="denied",
            metadata_schema_version=1,
            metadata_payload={},
            occurred_at=seeded["now"],
        )
    )
    with pytest.raises(IntegrityError):
        await db_session.flush()
