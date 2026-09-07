"""Real PostgreSQL coverage for the complete S2 owner graph."""

from importlib import import_module
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import JSON, ForeignKeyConstraint, insert, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from test_schema_wave_S1 import _seed_to_agent

from app.infrastructure.database import Base
from app.modules.agent.models import AgentRecord
from app.modules.run.models import RunRecord

S2_TABLE_OWNERS = {
    "workspaces": "workspace",
    "skill_packages": "workspace",
    "agent_skill_bindings": "workspace",
    "capability_catalog_items": "capability_market",
    "tool_definitions": "tool",
    "agent_mcp_connections": "tool",
    "agent_tool_grants": "tool",
    "membership_agent_tool_connections": "tool",
    "sessions": "session",
    "session_entries": "session",
    "session_run_links": "session",
    "a2a_requests": "a2a",
    "groups": "group",
    "group_memberships": "group",
    "group_events": "group",
    "group_run_links": "group",
    "agent_triggers": "trigger",
    "trigger_occurrences": "trigger",
    "agent_heartbeats": "heartbeat",
    "heartbeat_occurrences": "heartbeat",
    "agent_channel_configurations": "channel",
    "channel_deliveries": "channel",
}
for _owner in set(S2_TABLE_OWNERS.values()):
    import_module(f"app.modules.{_owner}.models")


def test_s2_graph_has_exact_owners_and_resolved_restrict_foreign_keys() -> None:
    actual = {
        name: table.info["owner"]
        for name, table in Base.metadata.tables.items()
        if table.info.get("owner") in set(S2_TABLE_OWNERS.values())
    }
    assert actual == S2_TABLE_OWNERS
    for name in actual:
        for constraint in Base.metadata.tables[name].constraints:
            if isinstance(constraint, ForeignKeyConstraint):
                for element in constraint.elements:
                    assert element.column.table.name in Base.metadata.tables
                    assert element.ondelete == "RESTRICT"
    assert not {"tasks", "goals", "skill_revisions", "tool_executions"} & set(Base.metadata.tables)


async def _put(session: AsyncSession, table: str, seed: dict[str, Any], **values: Any) -> Any:
    record_id = values.pop("id", uuid4())
    await session.execute(
        insert(Base.metadata.tables[table]).values(
            id=record_id,
            tenant_id=values.pop("tenant_id", seed["tenant"].id),
            created_at=seed["now"],
            updated_at=seed["now"],
            **values,
        )
    )
    return record_id


async def _second_agent(session: AsyncSession, seed: dict[str, Any]) -> Any:
    agent = AgentRecord(
        tenant_id=seed["tenant"].id,
        model_id=seed["model"].id,
        created_by_membership_id=seed["membership"].id,
        name="second",
        soul="Helpful",
        timezone="UTC",
        enabled=True,
        created_at=seed["now"],
        updated_at=seed["now"],
    )
    session.add(agent)
    await session.flush()
    return agent


async def _run(session: AsyncSession, seed: dict[str, Any], agent_id: Any) -> Any:
    run = RunRecord(
        tenant_id=seed["tenant"].id,
        agent_id=agent_id,
        status="Running",
        initiator_kind="session",
        initiator_owner_id=uuid4(),
        source_key=str(uuid4()),
        latest_history_sequence=0,
        created_at=seed["now"],
        started_at=seed["now"],
        updated_at=seed["now"],
    )
    session.add(run)
    await session.flush()
    return run.id


async def _catalog(session: AsyncSession, seed: dict[str, Any], **values: Any) -> Any:
    return await _put(
        session,
        "capability_catalog_items",
        seed,
        kind="mcp",
        source="https",
        source_key=str(uuid4()),
        name="server",
        description="tools",
        version="1",
        manifest_schema_version=1,
        manifest={},
        definition_revision=1,
        enabled=True,
        **values,
    )


async def _mcp(session: AsyncSession, seed: dict[str, Any]) -> dict[str, Any]:
    catalog = await _catalog(session, seed)
    definition = await _put(
        session,
        "tool_definitions",
        seed,
        catalog_item_id=catalog,
        source="mcp",
        name="fetch_item",
        upstream_name="fetch",
        description="Fetch",
        input_schema={"type": "object"},
        schema_version=1,
        executor_key="mcp.v1",
        configuration_version=1,
        non_secret_config={},
        enabled=True,
    )
    connection = await _put(
        session,
        "agent_mcp_connections",
        seed,
        agent_id=seed["agent"].id,
        catalog_item_id=catalog,
        auth_required=False,
        configuration_version=1,
        non_secret_config={},
        enabled=True,
        discovery_version=1,
        discovered_tools=[],
    )
    return {"catalog": catalog, "definition": definition, "connection": connection}


@pytest.mark.asyncio
async def test_s2_accepts_all_tables_and_shared_or_private_packages(db_session: AsyncSession) -> None:
    seed = await _seed_to_agent(db_session)
    agent_id, member = seed["agent"].id, seed["membership"].id
    second = await _second_agent(db_session, seed)
    group = await _put(db_session, "groups", seed, name="group", announcement="", next_position=1, enabled=True)
    await _put(db_session, "group_memberships", seed, group_id=group, membership_id=member, enabled=True)
    for owner, identity in (("membership_id", member), ("agent_id", agent_id), ("group_id", group)):
        await _put(db_session, "workspaces", seed, **{owner: identity})
    for owner, scope in ((None, "shared"), (agent_id, "private")):
        package = await _put(
            db_session,
            "skill_packages",
            seed,
            owner_agent_id=owner,
            storage_key="package/key",
            content_hash="a" * 64,
            format_version=1,
            revision=str(uuid4()),
        )
        await _put(
            db_session,
            "agent_skill_bindings",
            seed,
            agent_id=agent_id,
            skill_name=scope,
            package_id=package,
            package_scope=scope,
        )
        if scope == "shared":
            await _put(
                db_session,
                "agent_skill_bindings",
                seed,
                agent_id=second.id,
                skill_name=scope,
                package_id=package,
                package_scope=scope,
            )
    mcp = await _mcp(db_session, seed)
    await _put(
        db_session,
        "agent_tool_grants",
        seed,
        agent_id=agent_id,
        tool_definition_id=mcp["definition"],
        tool_source="mcp",
        catalog_item_id=mcp["catalog"],
        mcp_connection_id=mcp["connection"],
        configuration_version=1,
        non_secret_config={},
        granted_by_membership_id=member,
    )
    for _ in range(2):
        credential = await _put(
            db_session,
            "credentials",
            seed,
            membership_owner_id=member,
            kind="api_key",
            provider="example",
            label="personal",
            encrypted_payload=b"cipher",
            payload_version=1,
            key_version="key",
        )
        await _put(
            db_session,
            "membership_agent_tool_connections",
            seed,
            membership_id=member,
            agent_id=agent_id,
            tool_definition_id=mcp["definition"],
            credential_id=credential,
            credential_owner_kind="membership",
            credential_owner_id=member,
            label="account",
            configuration_version=1,
            non_secret_config={},
            enabled=True,
            discovery_version=1,
            discovered_tools=[],
        )
    session_id = await _put(
        db_session,
        "sessions",
        seed,
        membership_id=member,
        agent_id=agent_id,
        next_position=3,
        goal_enabled=False,
        goal_configuration_version=1,
        goal_configuration={},
    )
    input_id = await _put(
        db_session,
        "session_entries",
        seed,
        session_id=session_id,
        agent_id=agent_id,
        position=1,
        kind="input",
        source_key="message",
        payload_version=1,
        payload={},
    )
    reply = await _put(
        db_session,
        "session_entries",
        seed,
        session_id=session_id,
        agent_id=agent_id,
        position=2,
        kind="reply",
        origin_input_id=input_id,
        payload_version=1,
        payload={},
    )
    run_id = await _run(db_session, seed, agent_id)
    await db_session.execute(
        update(Base.metadata.tables["sessions"])
        .where(Base.metadata.tables["sessions"].c.id == session_id)
        .values(goal_enabled=True, goal_input_id=input_id)
    )
    for iteration in range(2):
        await _put(
            db_session,
            "session_run_links",
            seed,
            session_id=session_id,
            agent_id=agent_id,
            input_id=input_id,
            source_key=f"iteration-{iteration}",
            history_cutoff=iteration + 1,
            run_id=run_id if iteration == 0 else await _run(db_session, seed, agent_id),
            admission="started",
            result_version=1,
        )
    event = await _put(
        db_session,
        "group_events",
        seed,
        group_id=group,
        position=1,
        kind="input",
        source_key="event",
        membership_id=member,
        payload_version=1,
        payload={},
    )
    for current in (agent_id, second.id):
        await _put(
            db_session,
            "group_run_links",
            seed,
            group_id=group,
            event_id=event,
            agent_id=current,
            run_id=await _run(db_session, seed, current),
            admission="started",
            result_version=1,
        )
    for owner in ("trigger", "heartbeat"):
        config = await _put(
            db_session,
            f"agent_{owner}s",
            seed,
            agent_id=agent_id,
            configuration_version=1,
            configuration={},
            delegation_version=1,
            delegated_connections=[],
            enabled=True,
        )
        await _put(
            db_session,
            f"{owner}_occurrences",
            seed,
            **{f"{owner}_id": config},
            agent_id=agent_id,
            source_key="due-1",
            due_at=seed["now"],
            payload_version=1,
            payload={},
            admission="pending",
            result_version=1,
        )
    await _put(
        db_session,
        "a2a_requests",
        seed,
        source_agent_id=agent_id,
        source_run_id=run_id,
        source_call_id="call-1",
        target_agent_id=second.id,
        intent="consult",
        payload_version=1,
        payload={},
        delegation_version=1,
        delegated_connections=[],
        admission="pending",
        result_version=1,
        source_delivery="awaiting_result",
    )
    channel = await _put(
        db_session,
        "agent_channel_configurations",
        seed,
        agent_id=agent_id,
        provider="example",
        external_identity="bot",
        configuration_version=1,
        non_secret_config={},
        enabled=True,
    )
    await _put(
        db_session,
        "channel_deliveries",
        seed,
        agent_id=agent_id,
        channel_configuration_id=channel,
        session_reply_id=reply,
        destination="user",
        delivery_key="reply-1",
        attempt_count=0,
        delivery_status="pending",
    )
    for table in S2_TABLE_OWNERS:
        assert await db_session.scalar(select(Base.metadata.tables[table].c.id).limit(1)) is not None, table


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case",
    [
        "cross_tenant_workspace",
        "duplicate_workspace",
        "two_workspace_owners",
        "private_skill_wrong_agent",
        "private_skill_claimed_shared",
        "mcp_wrong_agent",
        "mcp_wrong_catalog",
        "mcp_membership_credential",
        "personal_wrong_owner",
        "tool_version",
        "catalog_platform_duplicate",
    ],
)
async def test_s2_rejects_invalid_execution_bindings(db_session: AsyncSession, case: str) -> None:
    seed = await _seed_to_agent(db_session)
    agent, member = seed["agent"].id, seed["membership"].id
    second = await _second_agent(db_session, seed)
    mcp = await _mcp(db_session, seed)
    other_catalog = await _catalog(db_session, seed)
    other_tenant = await _seed_to_agent(db_session, suffix="other")
    private = await _put(
        db_session,
        "skill_packages",
        seed,
        owner_agent_id=agent,
        storage_key="private",
        content_hash="a" * 64,
        format_version=1,
        revision="1",
    )
    await _put(db_session, "workspaces", seed, agent_id=agent)
    platform = {
        "tenant_id": None,
        "kind": "skill",
        "source": "registry",
        "source_key": "skill",
        "name": "skill",
        "description": "",
        "version": "1",
        "manifest_schema_version": 1,
        "manifest": {},
        "definition_revision": 1,
        "enabled": True,
    }
    await _put(db_session, "capability_catalog_items", seed, **platform)
    with pytest.raises(IntegrityError):
        if case == "cross_tenant_workspace":
            await _put(db_session, "workspaces", seed, membership_id=other_tenant["membership"].id)
        elif case == "duplicate_workspace":
            await _put(db_session, "workspaces", seed, agent_id=agent)
        elif case == "two_workspace_owners":
            await _put(db_session, "workspaces", seed, agent_id=second.id, membership_id=member)
        elif case.startswith("private_skill"):
            await _put(
                db_session,
                "agent_skill_bindings",
                seed,
                agent_id=second.id,
                skill_name="private",
                package_id=private,
                package_scope="shared" if case.endswith("shared") else "private",
            )
        elif case in ("mcp_wrong_agent", "mcp_wrong_catalog"):
            await _put(
                db_session,
                "agent_tool_grants",
                seed,
                agent_id=second.id if case.endswith("agent") else agent,
                tool_definition_id=mcp["definition"],
                tool_source="mcp",
                catalog_item_id=other_catalog if case.endswith("catalog") else mcp["catalog"],
                mcp_connection_id=mcp["connection"],
                configuration_version=1,
                non_secret_config={},
                granted_by_membership_id=member,
            )
        elif case == "mcp_membership_credential":
            await _put(
                db_session,
                "agent_mcp_connections",
                seed,
                agent_id=second.id,
                catalog_item_id=mcp["catalog"],
                credential_id=seed["credential"].id,
                credential_owner_kind="tenant",
                credential_owner_id=seed["tenant"].id,
                auth_required=True,
                configuration_version=1,
                non_secret_config={},
                enabled=True,
                discovery_version=1,
                discovered_tools=[],
            )
        elif case == "personal_wrong_owner":
            await _put(
                db_session,
                "membership_agent_tool_connections",
                seed,
                membership_id=member,
                agent_id=agent,
                tool_definition_id=mcp["definition"],
                credential_id=seed["credential"].id,
                credential_owner_kind="tenant",
                credential_owner_id=seed["tenant"].id,
                label="bad",
                configuration_version=1,
                non_secret_config={},
                enabled=True,
                discovery_version=1,
                discovered_tools=[],
            )
        elif case == "tool_version":
            await _put(
                db_session,
                "tool_definitions",
                seed,
                source="builtin",
                name="bad_version",
                description="",
                input_schema={},
                schema_version=0,
                executor_key="builtin.v1",
                configuration_version=1,
                non_secret_config={},
                enabled=True,
            )
        else:
            await _put(db_session, "capability_catalog_items", seed, **platform)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case",
    [
        "session_wrong_agent",
        "reply_to_reply",
        "channel_input_as_reply",
        "channel_wrong_agent",
        "trigger_wrong_agent",
        "heartbeat_wrong_agent",
        "a2a_wrong_source_agent",
        "a2a_wrong_target_agent",
        "group_wrong_agent",
    ],
)
async def test_s2_rejects_invalid_product_correlations(db_session: AsyncSession, case: str) -> None:
    seed = await _seed_to_agent(db_session)
    agent, member = seed["agent"].id, seed["membership"].id
    other = await _second_agent(db_session, seed)
    run = await _run(db_session, seed, agent)
    session_id = await _put(
        db_session,
        "sessions",
        seed,
        membership_id=member,
        agent_id=agent,
        next_position=3,
        goal_enabled=False,
        goal_configuration_version=1,
        goal_configuration={},
    )
    entry = await _put(
        db_session,
        "session_entries",
        seed,
        session_id=session_id,
        agent_id=agent,
        position=1,
        kind="input",
        source_key="input",
        payload_version=1,
        payload={},
    )
    reply = await _put(
        db_session,
        "session_entries",
        seed,
        session_id=session_id,
        agent_id=agent,
        position=2,
        kind="reply",
        origin_input_id=entry,
        payload_version=1,
        payload={},
    )
    channel = await _put(
        db_session,
        "agent_channel_configurations",
        seed,
        agent_id=agent,
        provider="example",
        external_identity="bot",
        configuration_version=1,
        non_secret_config={},
        enabled=True,
    )
    configs = {}
    for owner in ("trigger", "heartbeat"):
        configs[owner] = await _put(
            db_session,
            f"agent_{owner}s",
            seed,
            agent_id=agent,
            configuration_version=1,
            configuration={},
            delegation_version=1,
            delegated_connections=[],
            enabled=True,
        )
    group = await _put(db_session, "groups", seed, name="group", announcement="", next_position=2, enabled=True)
    event = await _put(
        db_session,
        "group_events",
        seed,
        group_id=group,
        position=1,
        kind="input",
        source_key="event",
        payload_version=1,
        payload={},
    )
    with pytest.raises(IntegrityError):
        if case == "session_wrong_agent":
            await _put(
                db_session,
                "session_run_links",
                seed,
                session_id=session_id,
                agent_id=other.id,
                input_id=entry,
                source_key="start",
                history_cutoff=1,
                run_id=run,
                admission="started",
                result_version=1,
            )
        elif case == "reply_to_reply":
            await _put(
                db_session,
                "session_entries",
                seed,
                session_id=session_id,
                agent_id=agent,
                position=3,
                kind="reply",
                origin_input_id=reply,
                payload_version=1,
                payload={},
            )
        elif case.startswith("channel"):
            await _put(
                db_session,
                "channel_deliveries",
                seed,
                agent_id=other.id if case.endswith("agent") else agent,
                channel_configuration_id=channel,
                session_reply_id=entry if case.endswith("reply") else reply,
                destination="user",
                delivery_key="delivery",
                attempt_count=0,
                delivery_status="pending",
            )
        elif case.startswith(("trigger", "heartbeat")):
            owner = case.split("_")[0]
            await _put(
                db_session,
                f"{owner}_occurrences",
                seed,
                **{f"{owner}_id": configs[owner]},
                agent_id=other.id,
                source_key="due",
                due_at=seed["now"],
                payload_version=1,
                payload={},
                run_id=run,
                admission="started",
                result_version=1,
            )
        elif case.startswith("a2a"):
            await _put(
                db_session,
                "a2a_requests",
                seed,
                source_agent_id=other.id if "source" in case else agent,
                source_run_id=run,
                source_call_id="call",
                target_agent_id=other.id,
                target_run_id=run if "target" in case else None,
                intent="consult",
                payload_version=1,
                payload={},
                delegation_version=1,
                delegated_connections=[],
                admission="started" if "target" in case else "pending",
                result_version=1,
                source_delivery="awaiting_result",
            )
        else:
            await _put(
                db_session,
                "group_run_links",
                seed,
                group_id=group,
                event_id=event,
                agent_id=other.id,
                run_id=run,
                admission="started",
                result_version=1,
            )


@pytest.mark.asyncio
async def test_s2_rejects_private_package_shared_scope_even_with_uuid_collision(db_session: AsyncSession) -> None:
    seed = await _seed_to_agent(db_session)
    colliding = await _second_agent(db_session, seed)
    colliding.id = seed["tenant"].id
    await db_session.flush()
    package = await _put(
        db_session,
        "skill_packages",
        seed,
        owner_agent_id=colliding.id,
        storage_key="private",
        content_hash="a" * 64,
        format_version=1,
        revision="1",
    )
    await _put(
        db_session,
        "agent_skill_bindings",
        seed,
        agent_id=colliding.id,
        skill_name="private",
        package_id=package,
        package_scope="private",
    )
    with pytest.raises(IntegrityError):
        await _put(
            db_session,
            "agent_skill_bindings",
            seed,
            agent_id=seed["agent"].id,
            skill_name="shared",
            package_id=package,
            package_scope="shared",
        )


@pytest.mark.asyncio
async def test_s2_catalog_origin_must_be_platform_template(db_session: AsyncSession) -> None:
    seed = await _seed_to_agent(db_session)
    other = await _seed_to_agent(db_session, suffix="other")
    platform = await _catalog(db_session, seed, tenant_id=None)
    await _catalog(db_session, seed, origin_platform_item_id=platform)
    tenant_item = await _catalog(db_session, other)
    with pytest.raises(IntegrityError):
        await _catalog(db_session, seed, origin_platform_item_id=tenant_item)


@pytest.mark.asyncio
@pytest.mark.parametrize("result", [None, JSON.NULL, [], "not an outcome"])
async def test_s2_a2a_pending_delivery_requires_result_object(db_session: AsyncSession, result: Any) -> None:
    seed = await _seed_to_agent(db_session)
    run = await _run(db_session, seed, seed["agent"].id)
    with pytest.raises(IntegrityError):
        await _put(
            db_session,
            "a2a_requests",
            seed,
            source_agent_id=seed["agent"].id,
            source_run_id=run,
            source_call_id="call",
            target_agent_id=seed["agent"].id,
            intent="consult",
            payload_version=1,
            payload={},
            delegation_version=1,
            delegated_connections=[],
            admission="pending",
            result_version=1,
            result=result,
            source_delivery="pending",
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["goal_reply", "nonpositive_cutoff", "waiting_other_session"])
async def test_s2_session_input_relations_reject_drift(db_session: AsyncSession, case: str) -> None:
    seed = await _seed_to_agent(db_session)
    agent = seed["agent"].id
    session_id = await _put(
        db_session,
        "sessions",
        seed,
        agent_id=agent,
        membership_id=seed["membership"].id,
        next_position=3,
        goal_enabled=False,
        goal_configuration_version=1,
        goal_configuration={},
    )
    input_id = await _put(
        db_session,
        "session_entries",
        seed,
        agent_id=agent,
        session_id=session_id,
        position=1,
        kind="input",
        source_key="input",
        payload_version=1,
        payload={},
    )
    reply = await _put(
        db_session,
        "session_entries",
        seed,
        agent_id=agent,
        session_id=session_id,
        position=2,
        kind="reply",
        origin_input_id=input_id,
        payload_version=1,
        payload={},
    )
    run = await _run(db_session, seed, agent)
    await _put(
        db_session,
        "session_run_links",
        seed,
        session_id=session_id,
        agent_id=agent,
        input_id=input_id,
        source_key="start",
        history_cutoff=1,
        run_id=run,
        admission="started",
        result_version=1,
    )
    await db_session.execute(
        update(Base.metadata.tables["sessions"])
        .where(Base.metadata.tables["sessions"].c.id == session_id)
        .values(goal_enabled=True, goal_input_id=input_id)
    )
    await _put(
        db_session,
        "session_entries",
        seed,
        agent_id=agent,
        session_id=session_id,
        position=3,
        kind="input",
        source_key="wait-answer",
        related_waiting_run_id=run,
        waiting_reference="wait-1",
        payload_version=1,
        payload={},
    )
    other_session = await _put(
        db_session,
        "sessions",
        seed,
        agent_id=agent,
        membership_id=seed["membership"].id,
        next_position=1,
        goal_enabled=False,
        goal_configuration_version=1,
        goal_configuration={},
    )
    with pytest.raises(IntegrityError):
        if case == "goal_reply":
            await db_session.execute(
                update(Base.metadata.tables["sessions"])
                .where(Base.metadata.tables["sessions"].c.id == session_id)
                .values(goal_input_id=reply)
            )
        elif case == "nonpositive_cutoff":
            await _put(
                db_session,
                "session_run_links",
                seed,
                session_id=session_id,
                agent_id=agent,
                input_id=input_id,
                source_key="later",
                history_cutoff=0,
                admission="pending",
                result_version=1,
            )
        else:
            await _put(
                db_session,
                "session_entries",
                seed,
                session_id=other_session,
                agent_id=agent,
                position=1,
                kind="input",
                source_key="foreign-wait",
                related_waiting_run_id=run,
                waiting_reference="wait-1",
                payload_version=1,
                payload={},
            )


@pytest.mark.asyncio
@pytest.mark.parametrize("target", ["mcp", "skill"])
async def test_s2_rejects_catalog_kind_mismatch(db_session: AsyncSession, target: str) -> None:
    seed = await _seed_to_agent(db_session)
    kind = "skill" if target == "mcp" else "mcp"
    catalog = await _put(
        db_session,
        "capability_catalog_items",
        seed,
        kind=kind,
        source="registry",
        source_key="source",
        name="source",
        description="",
        version="1",
        manifest_schema_version=1,
        manifest={},
        definition_revision=1,
        enabled=True,
    )
    if target == "mcp":
        valid_catalog = await _catalog(db_session, seed)
        await _put(
            db_session,
            "agent_mcp_connections",
            seed,
            agent_id=seed["agent"].id,
            catalog_item_id=valid_catalog,
            auth_required=False,
            configuration_version=1,
            non_secret_config={},
            enabled=True,
            discovery_version=1,
            discovered_tools=[],
        )
    else:
        valid_catalog = await _put(
            db_session,
            "capability_catalog_items",
            seed,
            kind="skill",
            source="registry",
            source_key="valid",
            name="source",
            description="",
            version="1",
            manifest_schema_version=1,
            manifest={},
            definition_revision=1,
            enabled=True,
        )
        await _put(
            db_session,
            "skill_packages",
            seed,
            catalog_item_id=valid_catalog,
            storage_key="valid",
            content_hash="a" * 64,
            format_version=1,
            revision="1",
        )
    with pytest.raises(IntegrityError):
        if target == "mcp":
            await _put(
                db_session,
                "agent_mcp_connections",
                seed,
                agent_id=seed["agent"].id,
                catalog_item_id=catalog,
                auth_required=False,
                configuration_version=1,
                non_secret_config={},
                enabled=True,
                discovery_version=1,
                discovered_tools=[],
            )
        else:
            await _put(
                db_session,
                "skill_packages",
                seed,
                catalog_item_id=catalog,
                storage_key="invalid",
                content_hash="a" * 64,
                format_version=1,
                revision="1",
            )


@pytest.mark.asyncio
@pytest.mark.parametrize("cross_tenant_attribution", [False, True])
async def test_s2_agent_self_install_and_membership_attribution(
    db_session: AsyncSession, cross_tenant_attribution: bool
) -> None:
    seed = await _seed_to_agent(db_session)
    mcp = await _mcp(db_session, seed)
    values = {
        "agent_id": seed["agent"].id,
        "tool_definition_id": mcp["definition"],
        "tool_source": "mcp",
        "catalog_item_id": mcp["catalog"],
        "mcp_connection_id": mcp["connection"],
        "configuration_version": 1,
        "non_secret_config": {},
        "granted_by_membership_id": None,
    }
    if cross_tenant_attribution:
        other = await _seed_to_agent(db_session, suffix="other")
        values["granted_by_membership_id"] = other["membership"].id
        with pytest.raises(IntegrityError):
            await _put(db_session, "agent_tool_grants", seed, **values)
    else:
        grant = await _put(db_session, "agent_tool_grants", seed, **values)
        table = Base.metadata.tables["agent_tool_grants"]
        stored = (await db_session.execute(select(table).where(table.c.id == grant))).mappings().one()
        assert stored["agent_id"] == seed["agent"].id
        assert stored["granted_by_membership_id"] is None


@pytest.mark.asyncio
async def test_s2_shared_catalog_has_one_package_but_private_copies_are_independent(
    db_session: AsyncSession,
) -> None:
    seed = await _seed_to_agent(db_session)
    other = await _second_agent(db_session, seed)
    catalog = await _put(
        db_session,
        "capability_catalog_items",
        seed,
        kind="skill",
        source="registry",
        source_key="one-skill",
        name="skill",
        description="",
        version="1",
        manifest_schema_version=1,
        manifest={},
        definition_revision=1,
        enabled=True,
    )
    for owner in (None, seed["agent"].id, other.id):
        await _put(
            db_session,
            "skill_packages",
            seed,
            catalog_item_id=catalog,
            owner_agent_id=owner,
            storage_key=str(uuid4()),
            content_hash="a" * 64,
            format_version=1,
            revision="1",
        )
    with pytest.raises(IntegrityError):
        await _put(
            db_session,
            "skill_packages",
            seed,
            catalog_item_id=catalog,
            storage_key="duplicate",
            content_hash="a" * 64,
            format_version=1,
            revision="1",
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("skill_name", ["code-review", "code_review", "../review", "code/review"])
async def test_s2_skill_names_are_not_tool_names_or_paths(db_session: AsyncSession, skill_name: str) -> None:
    seed = await _seed_to_agent(db_session)
    package = await _put(
        db_session,
        "skill_packages",
        seed,
        storage_key="skill",
        content_hash="a" * 64,
        format_version=1,
        revision="1",
    )
    values = {"agent_id": seed["agent"].id, "skill_name": skill_name, "package_id": package, "package_scope": "shared"}
    if "/" in skill_name:
        with pytest.raises(IntegrityError):
            await _put(db_session, "agent_skill_bindings", seed, **values)
    else:
        await _put(db_session, "agent_skill_bindings", seed, **values)
