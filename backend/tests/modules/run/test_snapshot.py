"""Snapshot-only codec/store tests, not full Run admission or lifecycle evidence."""

import hashlib
import json
from dataclasses import dataclass, replace
from uuid import uuid4

import pytest
from modules.run.test_history_repository import seed
from sqlalchemy import event, update

from app.infrastructure.errors import Conflict, NotFound
from app.modules.model.public import ModelContextProfile, PrivateModelPolicy, ResolvedModel
from app.modules.run.models import RunSnapshotRecord
from app.modules.run.snapshot import (
    AgentIdentity,
    InvalidSnapshot,
    PlatformInstructions,
    RunSnapshot,
    SnapshotRepository,
    SourceSection,
    decode_snapshot,
    derive_child,
    encode_snapshot,
    model_visible_prefix,
)
from app.modules.tool.public import AuthorizedToolSet, CredentialBinding, DefinitionSpec, ResolvedTool, ToolDefinition
from app.modules.workspace.public import SkillDiscovery, WorkspaceScope, WorkspaceSubject


def snapshot(tenant=None, agent=None, run=None):
    tenant, agent, run = tenant or uuid4(), agent or uuid4(), run or uuid4()
    model_id, credential_id = uuid4(), uuid4()
    policy = PrivateModelPolicy(tenant, model_id, "provider", "openai_chat", "model", "https://provider.test/v1",
        credential_id, 8192, 2048, '{"supports_tool_calling":true,"supports_images":true,"supports_streaming":true}',
        '{"protocol":"openai_chat","temperature":0.5}')
    profile = ModelContextProfile(model_id, "provider", "model", 8192, 2048, True, True, False)
    tool = ResolvedTool(ToolDefinition(uuid4(), tenant, DefinitionSpec("tool", "Read", '{"type":"object"}', "tool.v1", "product")),
        CredentialBinding(uuid4(), "agent", agent))
    subject = WorkspaceSubject("membership", uuid4())
    return RunSnapshot(tenant, agent, "main", PlatformInstructions("v1", "Follow platform rules"),
        AgentIdentity("Agent", "Be useful ✓", "Asia/Shanghai"), ResolvedModel(policy, profile),
        AuthorizedToolSet(tenant, agent, (tool,)), frozenset({"tool"}), WorkspaceScope(tenant, agent, subject, run),
        SkillDiscovery(tenant, agent, ("research",)), (
            SourceSection("memory_index", subject, "memory/MEMORY.md", "用户记忆索引"),
            SourceSection("skill_index", WorkspaceSubject("agent", agent), "skills/", "research: 调研")))


def recalculate(payload):
    return hashlib.sha256(json.dumps({"kind":"run_snapshot", "version":1, "payload":payload}, sort_keys=True,
        ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


def test_exact_snapshot_roundtrip_canonical_hash_and_detached_payload():
    value = snapshot()
    encoded = encode_snapshot(value)
    stored = json.loads(json.dumps(encoded.payload, ensure_ascii=False))
    assert decode_snapshot(encoded.version, stored, encoded.content_hash) == value
    assert recalculate(stored) == encoded.content_hash
    stored["agent"]["soul"] = "changed"
    assert value.agent.soul == "Be useful ✓"
    with pytest.raises(InvalidSnapshot, match="hash"):
        decode_snapshot(1, stored, encoded.content_hash)


@pytest.mark.parametrize("target", ["snapshot", "model", "policy", "tool", "section"])
def test_extra_fields_rejected_through_every_public_dataclass(target):
    encoded = encode_snapshot(snapshot())
    payload = encoded.payload
    selected = {"snapshot": payload, "model": payload["model"], "policy": payload["model"]["policy"],
        "tool": payload["tools"]["tools"][0], "section": payload["sources"][0]}[target]
    selected["credential_secret"] = "private-value"
    with pytest.raises(InvalidSnapshot) as error:
        decode_snapshot(1, payload, recalculate(payload))
    assert "private-value" not in str(error.value)


def test_model_visible_prefix_excludes_private_endpoints_and_credential_ids():
    value = snapshot()
    rendered = repr(model_visible_prefix(value))
    assert "用户记忆索引" in rendered and "Follow platform rules" in rendered
    assert "membership:" in rendered and "agent:" in rendered
    assert value.model.policy.endpoint not in rendered
    assert str(value.model.policy.credential_id) not in rendered
    assert str(value.tools.tools[0].credential.id) not in rendered
    assert "temperature" not in rendered


def test_child_preserves_captured_authorization_and_has_no_parent_history():
    parent = snapshot()
    child_id = uuid4()
    child = derive_child(parent, run_id=child_id)
    assert child.role == "sub" and not child.workspace.main and child.workspace.run_id == child_id
    assert child == replace(parent, role="sub", workspace=parent.workspace.for_subagent(child_id))
    assert child.tools == parent.tools and child.skills == parent.skills and child.sources == parent.sources
    assert "history" not in encode_snapshot(child).payload and "input" not in encode_snapshot(child).payload
    with pytest.raises(InvalidSnapshot):
        derive_child(child, run_id=uuid4())


@pytest.mark.parametrize("change", ["tenant", "profile", "workspace", "source", "skill_source", "settings", "endpoint"])
def test_invalid_scope_secret_configuration_or_source_fails(change):
    value = snapshot()
    if change == "tenant":
        value = replace(value, tenant_id=uuid4())
    elif change == "profile":
        value = replace(value, model=replace(value.model, profile=replace(value.model.profile, context_limit=100)))
    elif change == "workspace":
        value = replace(value, workspace=replace(value.workspace, output=WorkspaceSubject("agent", uuid4())))
    elif change == "source":
        value = replace(value, sources=(SourceSection("memory_index", WorkspaceSubject("group", uuid4()), "memory/", "text"),))
    elif change == "skill_source":
        value = replace(value, sources=(SourceSection("skill_index", value.workspace.output, "skills/", "text"),))
    elif change == "settings":
        value = replace(value, model=replace(value.model, policy=replace(value.model.policy, settings_json='{"api_key":"private-value"}')))
    else:
        value = replace(value, model=replace(value.model, policy=replace(value.model.policy, endpoint="https://provider.test?token=private-value")))
    with pytest.raises(InvalidSnapshot) as error:
        encode_snapshot(value)
    assert "private-value" not in str(error.value)


def test_unknown_versions_and_json_values_fail_without_fallback():
    encoded = encode_snapshot(snapshot())
    for version in (2, True, "1"):
        with pytest.raises(InvalidSnapshot, match="version"):
            decode_snapshot(version, encoded.payload, encoded.content_hash)
    value = snapshot()
    value = replace(value, model=replace(value.model, policy=replace(value.model.policy, settings_json='{"temperature":NaN}')))
    with pytest.raises(InvalidSnapshot):
        encode_snapshot(value)


async def test_private_store_roundtrip_retry_and_immutable_conflict(transaction_factory):
    tenant, (run,) = await seed(transaction_factory)
    from app.modules.run.models import RunRecord
    async with transaction_factory() as tx:
        row = await tx.session.get(RunRecord, run)
        value = snapshot(tenant, row.agent_id, run)
        repo = SnapshotRepository(tx)
        assert await repo.insert(run_id=run, snapshot=value) == value
        assert await repo.insert(run_id=run, snapshot=value) == value
        with pytest.raises(Conflict):
            await repo.insert(run_id=run, snapshot=replace(value, agent=replace(value.agent, soul="Changed")))
    async with transaction_factory() as tx:
        assert await SnapshotRepository(tx).read(tenant_id=tenant, run_id=run) == value
        with pytest.raises(NotFound):
            await SnapshotRepository(tx).read(tenant_id=uuid4(), run_id=run)


async def test_store_checks_run_scope_and_rolls_back_with_caller(transaction_factory):
    tenant, (run,) = await seed(transaction_factory)
    from app.modules.run.models import RunRecord
    with pytest.raises(RuntimeError, match="rollback"):
        async with transaction_factory() as tx:
            row = await tx.session.get(RunRecord, run)
            repo = SnapshotRepository(tx)
            with pytest.raises(InvalidSnapshot):
                await repo.insert(run_id=run, snapshot=snapshot(tenant, uuid4(), run))
            await repo.insert(run_id=run, snapshot=snapshot(tenant, row.agent_id, run))
            raise RuntimeError("rollback")
    async with transaction_factory() as tx:
        with pytest.raises(NotFound):
            await SnapshotRepository(tx).read(tenant_id=tenant, run_id=run)


async def test_corrupt_hash_and_version_fail_authoritatively(transaction_factory):
    tenant, (run,) = await seed(transaction_factory)
    from app.modules.run.models import RunRecord
    async with transaction_factory() as tx:
        row = await tx.session.get(RunRecord, run)
        repo = SnapshotRepository(tx)
        await repo.insert(run_id=run, snapshot=snapshot(tenant, row.agent_id, run))
        await tx.session.execute(update(RunSnapshotRecord).where(RunSnapshotRecord.run_id == run).values(schema_version=2))
        with pytest.raises(InvalidSnapshot, match="version"):
            await repo.read(tenant_id=tenant, run_id=run)
        await tx.session.execute(update(RunSnapshotRecord).where(RunSnapshotRecord.run_id == run).values(schema_version=1, content_hash="0"*64))
        with pytest.raises(InvalidSnapshot, match="hash"):
            await repo.read(tenant_id=tenant, run_id=run)


async def test_oversized_stored_snapshot_is_rejected_before_materialization(transaction_factory, test_database, monkeypatch):
    from app.modules.run import snapshot as module
    from app.modules.run.models import RunRecord
    tenant, (run,) = await seed(transaction_factory)
    async with transaction_factory() as tx:
        row = await tx.session.get(RunRecord, run)
        value = snapshot(tenant, row.agent_id, run)
        await SnapshotRepository(tx).insert(run_id=run, snapshot=replace(value, agent=replace(value.agent, soul="x"*200000)))
    monkeypatch.setattr(module, "MAX_SNAPSHOT_BYTES", 100)
    statements = []
    def observe(connection, cursor, statement, parameters, context, executemany):
        if statement.lstrip().startswith("SELECT"):
            statements.append(statement)
    event.listen(test_database.engine.sync_engine, "before_cursor_execute", observe)
    try:
        async with transaction_factory() as tx:
            with pytest.raises(InvalidSnapshot, match="byte bound"):
                await SnapshotRepository(tx).read(tenant_id=tenant, run_id=run)
    finally:
        event.remove(test_database.engine.sync_engine, "before_cursor_execute", observe)
    assert any("octet_length" in value for value in statements)
    assert not any("agent_run_snapshots.payload," in value for value in statements)


def test_snapshot_bound_counts_complete_wrapper_and_rejects_oversized_collections(monkeypatch):
    from app.modules.run import snapshot as module
    value = snapshot()
    encoded = encode_snapshot(value)
    size = len(json.dumps({"kind":"run_snapshot", "version":1, "payload":encoded.payload},
        ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode())
    monkeypatch.setattr(module, "MAX_SNAPSHOT_BYTES", size)
    assert encode_snapshot(value).content_hash == encoded.content_hash
    monkeypatch.setattr(module, "MAX_SNAPSHOT_BYTES", size - 1)
    with pytest.raises(InvalidSnapshot):
        encode_snapshot(value)
    with pytest.raises(InvalidSnapshot, match="collection"):
        encode_snapshot(replace(value, sources=value.sources * 33))


def test_read_does_not_silently_normalize_stored_tool_schema_strings():
    encoded = encode_snapshot(snapshot())
    encoded.payload["tools"]["tools"][0]["definition"]["spec"]["input_schema_json"] = '{ "type" : "object" }'
    with pytest.raises(InvalidSnapshot, match="canonical"):
        decode_snapshot(1, encoded.payload, recalculate(encoded.payload))


def test_initial_exposure_is_captured_in_hash_and_cannot_expand_authorization():
    value = snapshot()
    encoded = encode_snapshot(value)
    assert encoded.payload["initial_direct_names"] == ["tool"]
    assert encode_snapshot(replace(value, initial_direct_names=frozenset())).content_hash != encoded.content_hash
    with pytest.raises(InvalidSnapshot, match="exposure"):
        encode_snapshot(replace(value, initial_direct_names=frozenset({"ungranted"})))
    assert derive_child(value, run_id=uuid4()).initial_direct_names == value.initial_direct_names


def test_v1_fixture_shape_is_frozen_independently_of_public_dataclass_fields():
    payload = encode_snapshot(snapshot()).payload
    assert set(payload) == {"tenant_id", "agent_id", "role", "platform", "agent", "model", "tools",
        "initial_direct_names", "workspace", "skills", "sources", "include_current_time"}
    assert set(payload["model"]["policy"]) == {"tenant_id", "model_id", "provider", "protocol", "model_name", "endpoint",
        "credential_id", "context_limit", "output_limit", "capabilities_json", "settings_json"}
    assert set(payload["model"]["profile"]) == {"model_id", "provider", "model_name", "context_limit", "output_limit",
        "supports_images", "supports_streaming", "supports_prompt_cache"}
    tool = payload["tools"]["tools"][0]
    assert set(tool) == {"definition", "credential", "endpoint", "transport"}
    assert set(tool["definition"]) == {"id", "tenant_id", "spec"}
    assert set(tool["definition"]["spec"]) == {"name", "description", "input_schema_json", "executor_key", "source",
        "catalog_item_id", "upstream_name"}
    assert set(tool["credential"]) == {"id", "owner_kind", "owner_id"}
    assert set(payload["workspace"]) == {"tenant_id", "agent_id", "output", "run_id", "main", "preview_only"}
    assert set(payload["skills"]) == {"tenant_id", "agent_id", "skills"}
    assert set(payload["sources"][0]) == {"category", "subject", "reference", "content"}


def test_old_v1_stays_readable_when_public_model_adds_optional_display_field(monkeypatch):
    from app.modules.run import snapshot as module
    encoded = encode_snapshot(snapshot())
    @dataclass(frozen=True, slots=True)
    class FuturePolicy(PrivateModelPolicy):
        display_hint: str = "optional new display field"
    monkeypatch.setattr(module, "PrivateModelPolicy", FuturePolicy)
    decoded = decode_snapshot(encoded.version, encoded.payload, encoded.content_hash)
    assert decoded.model.policy.display_hint == "optional new display field"
    assert encode_snapshot(decoded).payload == encoded.payload
    assert encode_snapshot(decoded).content_hash == encoded.content_hash


@pytest.mark.parametrize("change", ["missing_protocol", "different_protocol", "supports_images", "supports_streaming", "supports_prompt_cache"])
@pytest.mark.parametrize("direction", ["encode", "decode"])
def test_captured_model_protocol_and_every_profile_flag_must_match(change, direction):
    value = snapshot()
    if direction == "decode":
        payload = encode_snapshot(value).payload
        if change == "missing_protocol":
            payload["model"]["policy"]["settings_json"] = '{}'
        elif change == "different_protocol":
            payload["model"]["policy"]["settings_json"] = '{"protocol":"anthropic"}'
        else:
            payload["model"]["profile"][change] = not payload["model"]["profile"][change]
        with pytest.raises(InvalidSnapshot):
            decode_snapshot(1, payload, recalculate(payload))
    else:
        if change in ("missing_protocol", "different_protocol"):
            settings = '{}' if change == "missing_protocol" else '{"protocol":"anthropic"}'
            value = replace(value, model=replace(value.model, policy=replace(value.model.policy, settings_json=settings)))
        else:
            value = replace(value, model=replace(value.model, profile=replace(value.model.profile,
                **{change: not getattr(value.model.profile, change)})))
        with pytest.raises(InvalidSnapshot):
            encode_snapshot(value)


async def test_new_insert_hashes_once_without_postwrite_decode_but_existing_retry_reads(transaction_factory, monkeypatch):
    from app.modules.run import snapshot as module
    from app.modules.run.models import RunRecord
    tenant, (run,) = await seed(transaction_factory)
    counts = {"canonical": 0, "decode": 0}
    original_canonical, original_decode = module._canonical, module.decode_snapshot
    def canonical(value):
        counts["canonical"] += 1
        return original_canonical(value)
    def decode(*args, **kwargs):
        counts["decode"] += 1
        return original_decode(*args, **kwargs)
    monkeypatch.setattr(module, "_canonical", canonical)
    monkeypatch.setattr(module, "decode_snapshot", decode)
    async with transaction_factory() as tx:
        row = await tx.session.get(RunRecord, run)
        value = snapshot(tenant, row.agent_id, run)
        repo = SnapshotRepository(tx)
        assert await repo.insert(run_id=run, snapshot=value) == value
        assert counts == {"canonical": 1, "decode": 0}
        assert await repo.insert(run_id=run, snapshot=value) == value
        assert counts["decode"] == 1


async def test_new_insert_returns_detached_immutable_view_without_changing_normalization(transaction_factory):
    from app.modules.run.models import RunRecord
    tenant, (run,) = await seed(transaction_factory)
    async with transaction_factory() as tx:
        row = await tx.session.get(RunRecord, run)
        value = snapshot(tenant, row.agent_id, run)
        source_list, names = list(value.sources), set(value.initial_direct_names)
        supplied = replace(value, sources=source_list, initial_direct_names=names)
        returned = await SnapshotRepository(tx).insert(run_id=run, snapshot=supplied)
        source_list.clear()
        names.clear()
        assert returned == value
        assert isinstance(returned.sources, tuple)
        assert isinstance(returned.initial_direct_names, frozenset)
    async with transaction_factory() as tx:
        assert await SnapshotRepository(tx).read(tenant_id=tenant, run_id=run) == value


async def test_noncanonical_typed_input_is_rejected_without_leaving_snapshot(transaction_factory):
    from app.modules.run.models import RunRecord
    tenant, (run,) = await seed(transaction_factory)
    with pytest.raises(InvalidSnapshot, match="canonical"):
        async with transaction_factory() as tx:
            row = await tx.session.get(RunRecord, run)
            value = snapshot(tenant, row.agent_id, run)
            object.__setattr__(value.tools.tools[0].definition.spec, "input_schema_json", '{ "type": "object" }')
            await SnapshotRepository(tx).insert(run_id=run, snapshot=value)
    async with transaction_factory() as tx:
        with pytest.raises(NotFound):
            await SnapshotRepository(tx).read(tenant_id=tenant, run_id=run)


async def test_valid_near_limit_insert_keeps_exact_readback_without_postwrite_decode(transaction_factory, monkeypatch):
    from app.modules.run import snapshot as module
    from app.modules.run.models import RunRecord
    tenant, (run,) = await seed(transaction_factory)
    async with transaction_factory() as tx:
        row = await tx.session.get(RunRecord, run)
        value = snapshot(tenant, row.agent_id, run)
        value = replace(value, agent=replace(value.agent, soul="资料" * 10000))
        encoded = encode_snapshot(value)
        size = len(module._canonical(encoded.payload).encode())
        monkeypatch.setattr(module, "MAX_SNAPSHOT_BYTES", size)
        original_decode = module.decode_snapshot
        def unexpected_decode(*args, **kwargs):
            pytest.fail("Fresh write repeated the persisted-read decoder")
        monkeypatch.setattr(module, "decode_snapshot", unexpected_decode)
        assert await SnapshotRepository(tx).insert(run_id=run, snapshot=value) == value
        monkeypatch.setattr(module, "decode_snapshot", original_decode)
        assert await SnapshotRepository(tx).read(tenant_id=tenant, run_id=run) == value


async def test_stored_snapshot_cannot_change_agent_even_with_recomputed_hash(transaction_factory):
    from app.modules.run.models import RunRecord
    tenant, (run,) = await seed(transaction_factory)
    async with transaction_factory() as tx:
        row = await tx.session.get(RunRecord, run)
        repo = SnapshotRepository(tx)
        await repo.insert(run_id=run, snapshot=snapshot(tenant, row.agent_id, run))
        changed = encode_snapshot(snapshot(tenant, uuid4(), run))
        await tx.session.execute(update(RunSnapshotRecord).where(RunSnapshotRecord.run_id == run).values(
            payload=changed.payload, content_hash=changed.content_hash))
        with pytest.raises(InvalidSnapshot, match="identity"):
            await repo.read(tenant_id=tenant, run_id=run)
