import json
import os
from dataclasses import replace
from uuid import uuid4

import pytest

from app.execution_dependencies.workspace_tools import WORKSPACE_DEFINITIONS, workspace_bindings
from app.infrastructure.errors import AccessDenied, InvalidInput, NotFound
from app.infrastructure.object_storage.local import LocalStorageBackend
from app.modules.agent.public import AgentService
from app.modules.credential.public import CredentialKeyring, CredentialService, Secret
from app.modules.identity_tenant.public import IdentityService, TenantPrincipal
from app.modules.model.public import ModelService
from app.modules.tool.public import (
    AvailableToolSet,
    CallScope,
    ResolvedTool,
    ToolCall,
    ToolDefinition,
    ToolRegistry,
    ToolScheduler,
)
from app.modules.workspace.public import SkillDiscovery, WorkspaceService, WorkspaceSubject


class Harness:
    def __init__(self, workspace, scope, discovery):
        self.scope = scope
        self.scheduler = ToolScheduler(
            ToolRegistry(workspace_bindings(workspace, scope=scope, skills=discovery)),
            max_parallel=2,
            timeout_seconds=10,
        )
        self.available = AvailableToolSet(
            scope.tenant_id,
            scope.agent_id,
            tuple(
                ResolvedTool(ToolDefinition(uuid4(), scope.tenant_id, definition), None)
                for definition in WORKSPACE_DEFINITIONS
            ),
            frozenset(definition.name for definition in WORKSPACE_DEFINITIONS),
        )

    async def call(self, tool_name, *, call_scope=None, **arguments):
        call = ToolCall(uuid4().hex, tool_name, json.dumps(arguments))
        (result,) = await self.scheduler.execute(
            self.available,
            (call,),
            call_scope or CallScope(self.scope.tenant_id, self.scope.agent_id, self.scope.run_id),
        )
        assert result.call_id == call.id
        assert len(result.content_json.encode()) < 250000
        return result.status, json.loads(result.content_json)


@pytest.fixture
async def prepared(test_database, transaction_factory, tmp_path, model_acceptance):
    keyring = CredentialKeyring(active_key_version="v1", keys={"v1": os.urandom(32)})
    async with transaction_factory() as tx:
        identities = IdentityService(tx)
        account = await identities.create_account()
        tenant = await identities.create_tenant(name="Tools")
        member = await identities.create_membership(
            tenant_id=tenant.id, account_id=account.id, display_name="Admin", role="tenant_admin"
        )
        principal = TenantPrincipal(account.id, member.id, tenant.id, "tenant_admin")
        credential = await CredentialService(tx, keyring).create(
            principal,
            kind="api_key",
            provider="openai",
            label="Provider",
            secret=Secret("test-only"),
            owner_kind="tenant",
        )
        model = await ModelService(tx).create(
            principal,
            credential_id=credential.id,
            provider="openai",
            model_name="test",
            endpoint="https://provider.invalid/v1",
            context_limit=8192,
            output_limit=1024,
            capability_source="administrator",
            capabilities={"supports_tool_calling": True},
            settings_version=1,
            settings={"protocol": "openai_chat"},
            enabled=False,
        )
    accepted = await model_acceptance(principal, model, keyring)
    async with transaction_factory() as tx:
        await ModelService(tx).set_enabled(principal, model_id=model.id, enabled=True, acceptance=accepted)
        agent = await AgentService(tx).create(principal, name="A", soul="Useful", timezone="UTC", model_id=model.id)
        other = await AgentService(tx).create(principal, name="B", soul="Useful", timezone="UTC", model_id=model.id)

    class Observations:
        def emit(self, observation):
            pass

    audit = Observations()
    storage = LocalStorageBackend(str(tmp_path))
    workspace = WorkspaceService(test_database.sessions, storage, audit)
    scope = await workspace.direct_scope(principal, agent_id=agent.id)
    await workspace.ensure(scope, scope.output)
    await workspace.ensure(scope, WorkspaceSubject("agent", agent.id))
    scope = replace(scope, run_id=uuid4())
    discovery = await workspace.discover_skills(tenant_id=scope.tenant_id, agent_id=scope.agent_id)
    return Harness(workspace, scope, discovery), workspace, scope, principal, other, storage, audit


@pytest.mark.asyncio
async def test_real_file_tool_chain_keeps_revision_and_scoped_operations(prepared):
    tools, workspace, scope, _, _, _, _ = prepared
    assert (await tools.call("make_directory", workspace="current", path="files/reports"))[0] == "success"
    status, written = await tools.call(
        "write_file", workspace="current", path="files/reports/a.md", content="one\ntwo", expected_revision=None
    )
    assert status == "success"
    status, read = await tools.call("read_file", workspace="current", path="files/reports/a.md")
    assert status == "success" and read["content"] == "one\ntwo" and read["revision"] == written["revision"]
    status, edited = await tools.call(
        "edit_file",
        workspace="current",
        path="files/reports/a.md",
        old_string="two",
        new_string="three",
        expected_revision=read["revision"],
    )
    assert status == "success"
    assert (await tools.call("list_files", workspace="current", path="files/reports"))[1]["entries"][0][
        "path"
    ] == "files/reports/a.md"
    assert (
        len((await tools.call("find_files", workspace="current", path="files/reports", query="a.md"))[1]["entries"])
        == 1
    )
    status, matches = await tools.call("search_files", workspace="current", path="files/reports/a.md", query="three")
    assert status == "success" and matches["matches"] == [[1, "three"]]
    status, copied = await tools.call(
        "copy_file",
        workspace="current",
        path="files/reports/a.md",
        destination_path="files/copy.md",
        source_revision=edited["revision"],
        destination_revision=None,
    )
    assert status == "success"
    status, moved = await tools.call(
        "move_file",
        workspace="current",
        path="files/copy.md",
        destination_path="files/moved.md",
        source_revision=copied["revision"],
        destination_revision=None,
    )
    assert status == "success" and moved["source_deleted"]
    status, _ = await tools.call(
        "delete_file", workspace="current", path="files/moved.md", expected_revision=moved["destination_revision"]
    )
    assert status == "success"
    assert (await workspace.read(scope, scope.output, "files/reports/a.md")).content == b"one\nthree"


@pytest.mark.asyncio
async def test_conflict_and_partial_move_preserve_actual_facts(prepared, monkeypatch):
    tools, workspace, scope, _, _, storage, _ = prepared
    revision = await workspace.write(scope, scope.output, "files/a.md", b"initial", expected_revision=None)
    current = await workspace.write(scope, scope.output, "files/a.md", b"updated", expected_revision=revision)
    status, conflict = await tools.call(
        "write_file", workspace="current", path="files/a.md", content="overwrite", expected_revision=revision
    )
    assert status == "error" and conflict["current_revision"] == current
    original = storage.delete_if_match

    async def fail_delete(key, *, condition):
        raise OSError("controlled failure")

    monkeypatch.setattr(storage, "delete_if_match", fail_delete)
    status, result = await tools.call(
        "move_file",
        workspace="current",
        path="files/a.md",
        destination_path="files/b.md",
        source_revision=current,
        destination_revision=None,
    )
    assert status == "uncertain" and not result["source_deleted"] and result["destination_revision"]
    assert (await workspace.read(scope, scope.output, "files/a.md")).content == b"updated"
    assert (await workspace.read(scope, scope.output, "files/b.md")).content == b"updated"
    monkeypatch.setattr(storage, "delete_if_match", original)


@pytest.mark.asyncio
async def test_model_cannot_fabricate_scope_or_modify_agent_skill_area(prepared):
    tools, workspace, scope, _, _, _, _ = prepared
    for arguments in (
        {"workspace": "agent", "path": "files/a.md", "content": "bad", "expected_revision": None},
        {"workspace": "current", "path": "skills/evil/SKILL.md", "content": "bad", "expected_revision": None},
        {
            "workspace": "current",
            "path": "files/a.md",
            "content": "bad",
            "expected_revision": None,
            "tenant_id": str(uuid4()),
        },
        {"workspace": "current", "path": "files/../outside", "content": "bad", "expected_revision": None},
    ):
        assert (await tools.call("write_file", **arguments))[0] == "error"
    assert (
        await tools.call(
            "list_files",
            workspace="current",
            path="files",
            call_scope=CallScope(scope.tenant_id, scope.agent_id, uuid4()),
        )
    )[0] == "error"
    with pytest.raises(NotFound):
        await workspace.read(scope, WorkspaceSubject("agent", scope.agent_id), "files/a.md")


@pytest.mark.asyncio
async def test_skill_load_uses_fixed_discovery_and_bounded_current_content(prepared):
    old, workspace, scope, principal, _, _, _ = prepared
    package = await workspace.prepare_skill_package({"SKILL.md": ("界" * 20000).encode(), "refs/guide.md": b"Guide"})
    await workspace.publish_skill(
        principal, agent_id=scope.agent_id, skill_name="code-review", prepared=package, shared=False
    )
    assert (await old.call("load_skill", name="code-review"))[0] == "error"
    discovery = await workspace.discover_skills(tenant_id=scope.tenant_id, agent_id=scope.agent_id)
    tools = Harness(workspace, scope, discovery)
    status, loaded = await tools.call("load_skill", name="code-review")
    assert status == "success" and loaded["truncated"] and loaded["next_offset"] == 16000
    assert loaded["content"] == "界" * 16000
    assert (await tools.call("load_skill", name="code-review", member="refs/guide.md"))[1]["content"] == "Guide"
    assert (await tools.call("load_skill", name="code-review", member="../../secret"))[0] == "error"
    with pytest.raises(InvalidInput):
        workspace_bindings(workspace, scope=scope, skills=SkillDiscovery(scope.tenant_id, uuid4(), ()))


@pytest.mark.asyncio
async def test_read_paging_and_edit_expansion_are_bounded(prepared):
    tools, workspace, scope, _, _, _, _ = prepared
    revision = await workspace.write(scope, scope.output, "files/large.txt", b"x" * 1000000, expected_revision=None)
    status, page = await tools.call("read_file", workspace="current", path="files/large.txt", limit=16000)
    assert status == "success" and len(page["content"]) == 16000 and page["next_offset"] == 16000
    assert (await tools.call("read_file", workspace="current", path="files/large.txt", limit=16001))[0] == "error"
    assert (await tools.call("read_file", workspace="current", path="files/large.txt", offset=True))[0] == "error"
    assert (
        await tools.call(
            "edit_file",
            workspace="current",
            path="files/large.txt",
            old_string="x",
            new_string="many",
            replace_all=True,
            expected_revision=revision,
        )
    )[0] == "success"
    changed = await workspace.read(scope, scope.output, "files/large.txt")
    assert (
        await tools.call(
            "edit_file",
            workspace="current",
            path="files/large.txt",
            old_string="m",
            new_string="overflow",
            replace_all=True,
            expected_revision=changed.revision,
        )
    )[0] == "error"
    assert (await workspace.read(scope, scope.output, "files/large.txt")).revision == changed.revision


@pytest.mark.asyncio
async def test_preview_scope_denies_real_mutation_and_binary_read_is_explicit(prepared):
    _, workspace, scope, _, _, _, _ = prepared
    discovery = await workspace.discover_skills(tenant_id=scope.tenant_id, agent_id=scope.agent_id)
    tools = Harness(workspace, replace(scope, preview_only=True), discovery)
    assert (await tools.call("write_file", workspace="current", path="files/a", content="bad", expected_revision=None))[
        0
    ] == "error"
    await workspace.write(scope, scope.output, "files/binary", b"\xff\x00", expected_revision=None)
    status, error = await tools.call("read_file", workspace="current", path="files/binary")
    assert status == "error" and "UTF-8" in error["message"]


@pytest.mark.asyncio
async def test_all_skill_member_names_are_reachable_in_bounded_pages(prepared):
    _, workspace, scope, principal, _, _, _ = prepared
    members = {"SKILL.md": b"Instructions", **{f"references/member-{index:03}.md": b"Member" for index in range(127)}}
    await workspace.publish_skill(
        principal,
        agent_id=scope.agent_id,
        skill_name="many-members",
        prepared=await workspace.prepare_skill_package(members),
        shared=False,
    )
    tools = Harness(
        workspace, scope, await workspace.discover_skills(tenant_id=scope.tenant_id, agent_id=scope.agent_id)
    )
    found = []
    offset = 0
    while offset is not None:
        status, page = await tools.call("load_skill", name="many-members", member_offset=offset)
        assert status == "success" and len(page["members"]) <= 32
        found.extend(page["members"])
        offset = page["next_member_offset"]
    assert found == sorted(members)
    assert (await tools.call("load_skill", name="many-members", member=found[-1]))[1]["content"] == "Member"


@pytest.mark.asyncio
async def test_directory_move_and_partial_delete_preserve_observed_facts(prepared, monkeypatch):
    tools, workspace, scope, _, _, storage, _ = prepared
    await workspace.write(scope, scope.output, "files/source/a.md", b"a", expected_revision=None)
    await workspace.write(scope, scope.output, "files/source/b.md", b"b", expected_revision=None)
    status, snapshot = await tools.call("inspect_directory", workspace="current", path="files/source")
    assert status == "success" and snapshot["member_count"] == 2
    status, moved = await tools.call(
        "move_directory",
        workspace="current",
        path="files/source",
        destination_path="files/destination",
        expected_revision=snapshot["revision"],
    )
    assert status == "success" and moved["completed"] and moved["copied_paths_count"] == 2
    snapshot = (await tools.call("inspect_directory", workspace="current", path="files/destination"))[1]
    original = storage.delete_if_match

    async def fail_second(key, *, condition):
        if key.endswith("/b.md"):
            raise OSError("controlled deletion failure")
        return await original(key, condition=condition)

    monkeypatch.setattr(storage, "delete_if_match", fail_second)
    status, partial = await tools.call(
        "delete_directory", workspace="current", path="files/destination", expected_revision=snapshot["revision"]
    )
    assert status == "uncertain" and not partial["completed"]
    assert partial["deleted_paths"] == ["a.md"] and partial["remaining_paths"] == ["b.md"]
    with pytest.raises(NotFound):
        await workspace.read(scope, scope.output, "files/destination/a.md")
    assert (await workspace.read(scope, scope.output, "files/destination/b.md")).content == b"b"


@pytest.mark.asyncio
async def test_distillation_is_main_only_without_user_selected_target(prepared):
    tools, workspace, scope, _, _, _, _ = prepared
    status, distilled = await tools.call("distill_memory", content="General knowledge", expected_revision=None)
    assert status == "success" and distilled["revision"]
    assert (
        await workspace.read(scope, WorkspaceSubject("agent", scope.agent_id), "memory/MEMORY.md")
    ).content == b"General knowledge"
    sub = scope.for_subagent(uuid4())
    discovery = await workspace.discover_skills(tenant_id=scope.tenant_id, agent_id=scope.agent_id)
    assert all(
        binding.builtin.name != "distill_memory"
        for binding in workspace_bindings(workspace, scope=sub, skills=discovery)
    )
    with pytest.raises(AccessDenied):
        await workspace.distill_memory(sub, b"Forbidden", expected_revision=distilled["revision"])
    sub_tools = Harness(workspace, sub, discovery)
    assert (await sub_tools.call("distill_memory", content="Forbidden", expected_revision=distilled["revision"]))[
        0
    ] == "error"
