import asyncio
import os
from dataclasses import replace
from uuid import uuid4

import pytest

from app.infrastructure.errors import AccessDenied, Conflict, InvalidInput, NotFound
from app.infrastructure.object_storage.local import LocalStorageBackend
from app.modules.agent.public import AgentService
from app.modules.credential.crypto import CredentialKeyring, Secret
from app.modules.credential.public import CredentialService
from app.modules.identity_tenant.public import IdentityService, TenantPrincipal
from app.modules.model.public import ModelService
from app.modules.workspace.public import (
    FileConflict,
    FileMutationUncertain,
    SkillInstallScope,
    WorkspaceScope,
    WorkspaceService,
    WorkspaceSubject,
    WorkspaceUnavailable,
)


class Observations:
    def __init__(self):
        self.items = []

    def emit(self, observation):
        self.items.append(observation)


@pytest.fixture
async def setup_workspace(test_database, transaction_factory, tmp_path, model_acceptance):
    keyring = CredentialKeyring(active_key_version="v1", keys={"v1": os.urandom(32)})
    async with transaction_factory() as tx:
        identities = IdentityService(tx)
        account = await identities.create_account()
        tenant = await identities.create_tenant(name="Workspace tenant")
        member = await identities.create_membership(
            tenant_id=tenant.id, account_id=account.id, display_name="Admin", role="tenant_admin"
        )
        principal = TenantPrincipal(account.id, member.id, tenant.id, "tenant_admin")
        credential = await CredentialService(
            tx, keyring
        ).create(
            principal, kind="api_key", provider="openai", label="Provider", secret=Secret("test"), owner_kind="tenant"
        )
        models = ModelService(tx)
        model = await models.create(
            principal,
            credential_id=credential.id,
            provider="openai",
            model_name="model",
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
        models = ModelService(tx)
        await models.set_enabled(principal, model_id=model.id, enabled=True, acceptance=accepted)
        await models.set_default(principal, model_id=model.id)
        agent = await AgentService(tx).create(principal, name="A", soul="Useful", timezone="UTC")
        other = await AgentService(tx).create(principal, name="B", soul="Useful", timezone="UTC")
    audit = Observations()
    storage = LocalStorageBackend(str(tmp_path))
    service = WorkspaceService(test_database.sessions, storage, audit)
    scope = await service.direct_scope(principal, agent_id=agent.id)
    await service.ensure(scope, scope.output)
    await service.ensure(scope, WorkspaceSubject("agent", agent.id))
    return service, scope, principal, other, storage, audit


@pytest.mark.asyncio
async def test_conditional_files_and_direction(setup_workspace):
    service, scope, _, _, _, audit = setup_workspace
    subject = scope.output
    revision = await service.write(scope, subject, "files/report.md", b"one", expected_revision=None)
    read = await service.read(scope, subject, "files/report.md")
    assert (read.content, read.revision) == (b"one", revision)
    with pytest.raises(FileConflict) as conflict:
        await service.write(scope, subject, "files/report.md", b"lost", expected_revision=None)
    assert conflict.value.current_revision == revision
    assert len(audit.items) == 1
    with pytest.raises(AccessDenied):
        await service.write(
            scope, WorkspaceSubject("agent", scope.agent_id), "files/report.md", b"no", expected_revision=None
        )
    with pytest.raises(AccessDenied):
        await service.write(
            replace(scope, preview_only=True), subject, "files/report.md", b"no", expected_revision=revision
        )
    with pytest.raises(AccessDenied):
        await service.read(scope, WorkspaceSubject("membership", uuid4()), "files/report.md")
    with pytest.raises(AccessDenied):
        await service.write(scope, subject, "skills/a/SKILL.md", b"no", expected_revision=None)
    with pytest.raises(InvalidInput):
        await service.read(scope, subject, "files/../memory/MEMORY.md")
    await service.delete(scope, subject, "files/report.md", expected_revision=revision)
    with pytest.raises(NotFound):
        await service.read(scope, subject, "files/report.md")


@pytest.mark.asyncio
async def test_memory_explicit_distillation_and_subagent_denial(setup_workspace):
    service, scope, _, _, _, _ = setup_workspace
    assert await service.memory_index(scope, scope.output) is None
    await service.write(scope, scope.output, "memory/MEMORY.md", b"Guide\n" + b"x" * 9000, expected_revision=None)
    index = await service.memory_index(scope, scope.output)
    assert index.truncated and len(index.guide.encode()) == 8192
    assert index.source == scope.output
    await service.distill_memory(scope, b"General knowledge", expected_revision=None)
    with pytest.raises(AccessDenied):
        await service.distill_memory(scope.for_subagent(uuid4()), b"no", expected_revision=None)
    found = await service.search_content(scope, scope.output, "memory/MEMORY.md", query="Guide")
    assert found.matches == ((0, "Guide"),)


@pytest.mark.asyncio
async def test_copy_direction_and_move_conflict_retains_new_source(setup_workspace, monkeypatch):
    service, scope, _, _, _, _ = setup_workspace
    agent = WorkspaceSubject("agent", scope.agent_id)
    agent_scope = WorkspaceScope(scope.tenant_id, scope.agent_id, agent)
    original = await service.write(agent_scope, agent, "files/template", b"copy", expected_revision=None)
    await service.copy(
        scope,
        agent,
        "files/template",
        scope.output,
        "files/result",
        source_revision=original,
        destination_revision=None,
    )
    with pytest.raises(AccessDenied):
        await service.copy(
            scope,
            scope.output,
            "files/result",
            agent,
            "files/leak",
            source_revision=original,
            destination_revision=None,
        )
    source = await service.read(scope, scope.output, "files/result")
    original_delete = service.delete

    async def concurrent_delete(*args, **kwargs):
        await service.write(scope, scope.output, "files/result", b"new", expected_revision=source.revision)
        return await original_delete(*args, **kwargs)

    monkeypatch.setattr(service, "delete", concurrent_delete)
    result = await service.move(
        scope, scope.output, "files/result", "files/moved", source_revision=source.revision, destination_revision=None
    )
    assert not result.source_deleted
    assert (await service.read(scope, scope.output, "files/result")).content == b"new"
    assert (await service.read(scope, scope.output, "files/moved")).content == b"copy"


@pytest.mark.asyncio
async def test_skill_shared_update_private_fork_and_discovery(setup_workspace):
    service, scope, principal, other, _, _ = setup_workspace
    old_discovery = await service.discover_skills(tenant_id=scope.tenant_id, agent_id=scope.agent_id)
    first = await service.prepare_skill_package({"SKILL.md": b"one", "scripts/run.py": b"pass"})
    bound = await service.publish_skill(
        principal, agent_id=scope.agent_id, skill_name="research", prepared=first, shared=True
    )
    await service.bind_skill(principal, agent_id=other.id, skill_name="research", package_id=bound.package_id)
    discovery = await service.discover_skills(tenant_id=scope.tenant_id, agent_id=scope.agent_id)
    other_discovery = await service.discover_skills(tenant_id=scope.tenant_id, agent_id=other.id)
    with pytest.raises(AccessDenied):
        await service.load_skill(old_discovery, "research")
    second = await service.prepare_skill_package({"SKILL.md": b"two"})
    updated = await service.publish_skill(
        principal,
        agent_id=scope.agent_id,
        skill_name="research",
        prepared=second,
        shared=True,
        package_id=bound.package_id,
        expected_revision=bound.revision,
    )
    assert (await service.load_skill(discovery, "research")).members["SKILL.md"] == b"two"
    assert (await service.load_skill(other_discovery, "research")).members["SKILL.md"] == b"two"
    third = await service.prepare_skill_package({"SKILL.md": b"private"})
    fork = await service.publish_skill(
        principal,
        agent_id=scope.agent_id,
        skill_name="research",
        prepared=third,
        shared=False,
        expected_revision=updated.revision,
    )
    assert fork.package_id != updated.package_id
    assert (await service.load_skill(discovery, "research")).members["SKILL.md"] == b"private"
    assert (await service.load_skill(other_discovery, "research")).members["SKILL.md"] == b"two"
    with pytest.raises(AccessDenied):
        await service.bind_skill(principal, agent_id=other.id, skill_name="stolen", package_id=fork.package_id)
    with pytest.raises(Conflict):
        await service.discard_prepared_skill(third)
    refreshed = await service.prepare_skill_package({"SKILL.md": b"shared-three"})
    await service.refresh_shared_skill(
        principal, package_id=updated.package_id, prepared=refreshed, expected_revision=updated.revision
    )
    assert (await service.load_skill(discovery, "research")).members["SKILL.md"] == b"private"
    assert (await service.load_skill(other_discovery, "research")).members["SKILL.md"] == b"shared-three"
    await service.remove_skill(principal, agent_id=scope.agent_id, skill_name="research")
    with pytest.raises(NotFound):
        await service.load_skill(discovery, "research")


@pytest.mark.asyncio
async def test_package_preparation_validation_and_cleanup(setup_workspace):
    service, _, _, _, storage, _ = setup_workspace
    for members in (
        {"other": b"none"},
        {"SKILL.md": b""},
        {"SKILL.md": b"ok", "../escape": b"bad"},
        {"SKILL.md": b"ok", "scripts": b"file", "scripts/a": b"bad"},
    ):
        with pytest.raises(InvalidInput):
            await service.prepare_skill_package(members)
    prepared = await service.prepare_skill_package({"SKILL.md": b"ready"})
    assert await storage.exists(prepared.storage_key)
    await service.discard_prepared_skill(prepared)
    assert not await storage.exists(prepared.storage_key)


@pytest.mark.asyncio
async def test_skill_hyphen_name_and_path_rejection(setup_workspace):
    service, scope, principal, _, _, _ = setup_workspace
    prepared = await service.prepare_skill_package({"SKILL.md": b"Review code"})
    await service.publish_skill(
        principal, agent_id=scope.agent_id, skill_name="code-review", prepared=prepared, shared=False
    )
    discovery = await service.discover_skills(tenant_id=scope.tenant_id, agent_id=scope.agent_id)
    assert (await service.load_skill(discovery, "code-review")).members["SKILL.md"] == b"Review code"
    for invalid in ("../review", "code/review"):
        with pytest.raises(InvalidInput):
            await service.publish_skill(
                principal, agent_id=scope.agent_id, skill_name=invalid, prepared=prepared, shared=False
            )


@pytest.mark.asyncio
async def test_package_reader_blocks_replacement_cleanup_not_unrelated_files(setup_workspace, monkeypatch):
    service, scope, principal, _, storage, _ = setup_workspace
    first = await service.prepare_skill_package({"SKILL.md": b"old", "references/a": b"old-reference"})
    bound = await service.publish_skill(
        principal, agent_id=scope.agent_id, skill_name="research", prepared=first, shared=True
    )
    discovery = await service.discover_skills(tenant_id=scope.tenant_id, agent_id=scope.agent_id)
    second = await service.prepare_skill_package({"SKILL.md": b"new", "references/a": b"new-reference"})
    started, release = asyncio.Event(), asyncio.Event()
    read = storage.read_versioned

    async def paused_read(key, **kwargs):
        if key == f"{first.storage_key}/SKILL.md":
            started.set()
            await asyncio.wait_for(release.wait(), 5)
        return await read(key, **kwargs)

    monkeypatch.setattr(storage, "read_versioned", paused_read)
    loading = asyncio.create_task(service.load_skill(discovery, "research"))
    await asyncio.wait_for(started.wait(), 5)
    publishing = asyncio.create_task(
        service.publish_skill(
            principal,
            agent_id=scope.agent_id,
            skill_name="research",
            prepared=second,
            shared=True,
            expected_revision=bound.revision,
        )
    )
    await asyncio.wait_for(
        service.write(scope, scope.output, "files/unrelated", b"progress", expected_revision=None), 5
    )
    assert not publishing.done()
    assert await storage.exists(first.storage_key)
    release.set()
    loaded = await asyncio.wait_for(loading, 5)
    await asyncio.wait_for(publishing, 5)
    assert loaded.members == {"SKILL.md": b"old", "references/a": b"old-reference"}
    assert not await storage.exists(first.storage_key)
    assert (await service.load_skill(discovery, "research")).members["SKILL.md"] == b"new"


@pytest.mark.asyncio
async def test_remove_waits_for_reader_and_cleans_private_content(setup_workspace, monkeypatch):
    service, scope, principal, _, storage, _ = setup_workspace
    prepared = await service.prepare_skill_package({"SKILL.md": b"private"})
    await service.publish_skill(
        principal, agent_id=scope.agent_id, skill_name="private", prepared=prepared, shared=False
    )
    discovery = await service.discover_skills(tenant_id=scope.tenant_id, agent_id=scope.agent_id)
    started, release = asyncio.Event(), asyncio.Event()
    read = storage.read_versioned

    async def paused_read(key, **kwargs):
        if key == f"{prepared.storage_key}/SKILL.md":
            started.set()
            await asyncio.wait_for(release.wait(), 5)
        return await read(key, **kwargs)

    monkeypatch.setattr(storage, "read_versioned", paused_read)
    loading = asyncio.create_task(service.load_skill(discovery, "private"))
    await asyncio.wait_for(started.wait(), 5)
    removing = asyncio.create_task(service.remove_skill(principal, agent_id=scope.agent_id, skill_name="private"))
    release.set()
    assert (await asyncio.wait_for(loading, 5)).members["SKILL.md"] == b"private"
    await asyncio.wait_for(removing, 5)
    assert not await storage.exists(prepared.storage_key)
    with pytest.raises(NotFound):
        await service.load_skill(discovery, "private")


@pytest.mark.asyncio
async def test_self_install_cannot_modify_other_agent_or_refresh_shared(setup_workspace):
    service, scope, _, other, _, _ = setup_workspace
    trusted = SkillInstallScope(scope.tenant_id, scope.agent_id)
    first = await service.prepare_skill_package({"SKILL.md": b"one"})
    bound = await service.publish_skill(
        trusted, agent_id=scope.agent_id, skill_name="research", prepared=first, shared=True
    )
    with pytest.raises(AccessDenied):
        await service.bind_skill(trusted, agent_id=other.id, skill_name="research", package_id=bound.package_id)
    second = await service.prepare_skill_package({"SKILL.md": b"two"})
    with pytest.raises(AccessDenied):
        await service.publish_skill(
            trusted,
            agent_id=scope.agent_id,
            skill_name="research",
            prepared=second,
            shared=True,
            expected_revision=bound.revision,
        )
    discovery = await service.discover_skills(tenant_id=scope.tenant_id, agent_id=scope.agent_id)
    assert (await service.load_skill(discovery, "research")).members["SKILL.md"] == b"one"
    fork = await service.publish_skill(
        trusted,
        agent_id=scope.agent_id,
        skill_name="research",
        prepared=second,
        shared=False,
        expected_revision=bound.revision,
    )
    assert not fork.shared


@pytest.mark.asyncio
async def test_pagination_bounds_and_file_read_write_limits(setup_workspace):
    service, scope, _, _, _, _ = setup_workspace
    for name in ("a", "b", "c"):
        await service.write(scope, scope.output, f"files/{name}", b"data", expected_revision=None)
    first = await service.list(scope, scope.output, "files", limit=2)
    second = await service.list(scope, scope.output, "files", limit=2, cursor=first.cursor)
    assert len(first.entries) == 2 and first.cursor
    assert len(second.entries) == 1 and second.cursor is None
    assert {entry.key for entry in first.entries + second.entries} == {"files/a", "files/b", "files/c"}
    for bad in (0, 101, True):
        with pytest.raises(InvalidInput):
            await service.list(scope, scope.output, "files", limit=bad)
    exact = b"x" * (4 * 1024 * 1024)
    await service.write(scope, scope.output, "files/exact", exact, expected_revision=None)
    assert (await service.read(scope, scope.output, "files/exact")).content == exact
    with pytest.raises(InvalidInput):
        await service.write(scope, scope.output, "files/over", exact + b"x", expected_revision=None)


@pytest.mark.asyncio
async def test_partial_preparation_is_removed_and_publication_failure_keeps_old(setup_workspace, monkeypatch):
    service, scope, principal, _, storage, _ = setup_workspace
    write = storage.write_bytes_if_match
    attempted = []

    async def failing_write(key, data, **kwargs):
        attempted.append(key)
        if key.endswith("references/a"):
            raise OSError("controlled storage failure")
        return await write(key, data, **kwargs)

    monkeypatch.setattr(storage, "write_bytes_if_match", failing_write)
    with pytest.raises(OSError):
        await service.prepare_skill_package({"SKILL.md": b"ready", "references/a": b"fail"})
    assert not await storage.exists(attempted[0].rsplit("/", 1)[0])
    monkeypatch.setattr(storage, "write_bytes_if_match", write)
    first = await service.prepare_skill_package({"SKILL.md": b"old"})
    bound = await service.publish_skill(
        principal, agent_id=scope.agent_id, skill_name="research", prepared=first, shared=True
    )
    second = await service.prepare_skill_package({"SKILL.md": b"new"})
    with pytest.raises(Conflict):
        await service.publish_skill(
            principal,
            agent_id=scope.agent_id,
            skill_name="research",
            prepared=second,
            shared=True,
            package_id=bound.package_id,
            expected_revision="stale",
        )
    discovery = await service.discover_skills(tenant_id=scope.tenant_id, agent_id=scope.agent_id)
    assert (await service.load_skill(discovery, "research")).members["SKILL.md"] == b"old"
    await service.discard_prepared_skill(second)
    assert not await storage.exists(second.storage_key)


@pytest.mark.asyncio
async def test_storage_io_has_no_business_database_transaction(setup_workspace, test_database, monkeypatch):
    service, scope, principal, _, storage, _ = setup_workspace
    read = storage.read_versioned
    write = storage.write_bytes_if_match

    async def checked_read(*args, **kwargs):
        assert test_database.engine.pool.checkedout() == 0
        return await read(*args, **kwargs)

    async def checked_write(*args, **kwargs):
        assert test_database.engine.pool.checkedout() == 0
        return await write(*args, **kwargs)

    monkeypatch.setattr(storage, "read_versioned", checked_read)
    monkeypatch.setattr(storage, "write_bytes_if_match", checked_write)
    await service.write(scope, scope.output, "files/report", b"done", expected_revision=None)
    await service.read(scope, scope.output, "files/report")
    prepared = await service.prepare_skill_package({"SKILL.md": b"ready"})
    await service.publish_skill(
        principal, agent_id=scope.agent_id, skill_name="research", prepared=prepared, shared=True
    )
    discovery = await service.discover_skills(tenant_id=scope.tenant_id, agent_id=scope.agent_id)
    await service.load_skill(discovery, "research")


@pytest.mark.asyncio
async def test_storage_failure_after_visible_write_is_uncertain_not_retried(setup_workspace, monkeypatch):
    service, scope, _, _, storage, audit = setup_workspace
    write = storage.write_bytes_if_match
    calls = 0

    async def write_then_disconnect(*args, **kwargs):
        nonlocal calls
        calls += 1
        await write(*args, **kwargs)
        raise OSError("private transport details")

    monkeypatch.setattr(storage, "write_bytes_if_match", write_then_disconnect)
    with pytest.raises(FileMutationUncertain) as error:
        await service.write(scope, scope.output, "files/report", b"committed", expected_revision=None)
    assert "private transport" not in str(error.value)
    assert calls == 1 and not audit.items
    assert (await service.read(scope, scope.output, "files/report")).content == b"committed"


@pytest.mark.asyncio
async def test_read_and_list_storage_errors_are_bounded_named_failures(setup_workspace, monkeypatch):
    service, scope, _, _, storage, _ = setup_workspace

    async def broken_read(*args, **kwargs):
        raise OSError("private endpoint secret")

    async def oversized_list(*args, **kwargs):
        raise ValueError("physical directory scan bound")

    monkeypatch.setattr(storage, "read_versioned", broken_read)
    with pytest.raises(WorkspaceUnavailable) as error:
        await service.read(scope, scope.output, "files/report")
    assert "secret" not in str(error.value)
    monkeypatch.setattr(storage, "list_dir_page", oversized_list)
    with pytest.raises(InvalidInput):
        await service.list(scope, scope.output, "files")


@pytest.mark.asyncio
async def test_shared_publish_requires_admin_even_when_agent_use_is_allowed(setup_workspace):
    service, scope, principal, _, _, _ = setup_workspace
    first = await service.prepare_skill_package({"SKILL.md": b"shared"})
    binding = await service.publish_skill(
        principal, agent_id=scope.agent_id, skill_name="review", prepared=first, shared=True
    )
    member = replace(principal, role="member", allowed_agent_ids=frozenset({scope.agent_id}))
    second = await service.prepare_skill_package({"SKILL.md": b"unauthorized refresh"})
    with pytest.raises(AccessDenied):
        await service.publish_skill(
            member,
            agent_id=scope.agent_id,
            skill_name="review",
            prepared=second,
            shared=True,
            expected_revision=binding.revision,
        )
    discovery = await service.discover_skills(tenant_id=scope.tenant_id, agent_id=scope.agent_id)
    assert (await service.load_skill(discovery, "review")).members["SKILL.md"] == b"shared"
    await service.discard_prepared_skill(second)


@pytest.mark.asyncio
async def test_move_preserves_committed_destination_when_source_disappears(setup_workspace, monkeypatch):
    service, scope, _, _, _, _ = setup_workspace
    revision = await service.write(scope, scope.output, "files/source", b"content", expected_revision=None)
    remove = service.delete

    async def already_removed(*args, **kwargs):
        await remove(*args, **kwargs)
        return await remove(*args, **kwargs)

    monkeypatch.setattr(service, "delete", already_removed)
    result = await service.move(
        scope, scope.output, "files/source", "files/destination", source_revision=revision, destination_revision=None
    )
    assert result.source_deleted
    assert (await service.read(scope, scope.output, "files/destination")).content == b"content"


@pytest.mark.asyncio
async def test_directory_move_delete_and_stale_manifest(setup_workspace):
    service, scope, _, _, _, _ = setup_workspace
    await service.mkdir(scope, scope.output, "files/source/empty")
    await service.write(scope, scope.output, "files/source/nested/a", b"A", expected_revision=None)
    await service.write(scope, scope.output, "files/source/b", b"B", expected_revision=None)
    snapshot = await service.inspect_directory(scope, scope.output, "files/source")
    with pytest.raises(FileConflict):
        await service.delete_directory(scope, scope.output, "files/source", expected_revision="stale")
    with pytest.raises(InvalidInput):
        await service.move_directory(
            scope, scope.output, "files/source", "files/source/child", expected_revision=snapshot.revision
        )
    moved = await service.move_directory(
        scope, scope.output, "files/source", "files/destination", expected_revision=snapshot.revision
    )
    assert moved.completed and set(moved.copied_paths) == {"nested/a", "b"}
    with pytest.raises(NotFound):
        await service.inspect_directory(scope, scope.output, "files/source")
    destination = await service.inspect_directory(scope, scope.output, "files/destination")
    assert {member.path for member in destination.members} == {"empty", "nested", "nested/a", "b"}
    deleted = await service.delete_directory(
        scope, scope.output, "files/destination", expected_revision=destination.revision
    )
    assert deleted.completed
    with pytest.raises(NotFound):
        await service.inspect_directory(scope, scope.output, "files/destination")


@pytest.mark.asyncio
async def test_directory_delete_preserves_concurrent_new_and_changed_files(setup_workspace, monkeypatch):
    service, scope, _, _, storage, _ = setup_workspace
    await service.write(scope, scope.output, "files/source/a", b"A", expected_revision=None)
    b = await service.write(scope, scope.output, "files/source/b", b"B", expected_revision=None)
    snapshot = await service.inspect_directory(scope, scope.output, "files/source")
    delete = storage.delete_if_match
    changed = False

    async def concurrent_delete(*args, **kwargs):
        nonlocal changed
        if not changed:
            changed = True
            await service.write(scope, scope.output, "files/source/b", b"new B", expected_revision=b)
            await service.write(scope, scope.output, "files/source/new", b"new file", expected_revision=None)
        return await delete(*args, **kwargs)

    monkeypatch.setattr(storage, "delete_if_match", concurrent_delete)
    result = await service.delete_directory(scope, scope.output, "files/source", expected_revision=snapshot.revision)
    assert not result.completed and result.deleted_paths == ("a",)
    assert "b" in result.remaining_paths
    assert (await service.read(scope, scope.output, "files/source/b")).content == b"new B"
    assert (await service.read(scope, scope.output, "files/source/new")).content == b"new file"


@pytest.mark.asyncio
async def test_directory_move_partial_copy_keeps_all_sources(setup_workspace, monkeypatch):
    service, scope, _, _, storage, _ = setup_workspace
    await service.write(scope, scope.output, "files/source/a", b"A", expected_revision=None)
    await service.write(scope, scope.output, "files/source/b", b"B", expected_revision=None)
    snapshot = await service.inspect_directory(scope, scope.output, "files/source")
    write = storage.write_bytes_if_match

    async def fail_second(key, *args, **kwargs):
        if key.endswith("destination/b"):
            raise OSError("controlled failure")
        return await write(key, *args, **kwargs)

    monkeypatch.setattr(storage, "write_bytes_if_match", fail_second)
    result = await service.move_directory(
        scope, scope.output, "files/source", "files/destination", expected_revision=snapshot.revision
    )
    assert not result.completed and result.copied_paths == ("a",) and result.deleted_paths == ()
    assert (await service.read(scope, scope.output, "files/source/a")).content == b"A"
    assert (await service.read(scope, scope.output, "files/source/b")).content == b"B"


@pytest.mark.asyncio
async def test_directory_manifest_bound_prevents_any_delete(setup_workspace):
    service, scope, _, _, storage, _ = setup_workspace
    workspace = await service.ensure(scope, scope.output)
    key = f"workspaces/{scope.tenant_id}/{workspace.id}/files/source"
    for index in range(129):
        await storage.write_bytes(f"{key}/{index}", b"x")
    with pytest.raises(InvalidInput):
        await service.delete_directory(scope, scope.output, "files/source", expected_revision="unavailable")
    assert await storage.exists(f"{key}/0") and await storage.exists(f"{key}/128")
