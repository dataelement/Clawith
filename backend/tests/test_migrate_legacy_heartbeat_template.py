from types import SimpleNamespace
import hashlib
import uuid

import pytest

from app.scripts import migrate_legacy_heartbeat_template as migration
from app.services.storage_runtime.base import ConditionalWriteResult, StorageVersion
from app.services.storage_runtime.fallback import FallbackStorageBackend


LEGACY_FIXTURE = b"# legacy heartbeat fixture\n"
LEGACY_FIXTURE_SHA256 = hashlib.sha256(LEGACY_FIXTURE).hexdigest()


class _Scalars:
    def __init__(self, values):
        self._values = values

    def all(self):
        return self._values


class _Result:
    def __init__(self, values):
        self._values = values

    def scalars(self):
        return _Scalars(self._values)


class _Session:
    def __init__(self, tenant_ids, agents_by_tenant):
        self._results = [_Result(tenant_ids), *(_Result(agents) for agents in agents_by_tenant)]
        self.statements = []

    async def execute(self, statement):
        self.statements.append(statement)
        return self._results.pop(0)


class _Storage:
    def __init__(self, files):
        self.files = dict(files)
        self.writes = []

    async def get_version(self, key):
        data = self.files.get(key)
        if data is None:
            return StorageVersion(key=key, exists=False, is_dir=False)
        return StorageVersion(
            key=key,
            exists=True,
            is_dir=False,
            version_id=f"version:{hash(data)}",
        )

    async def read_bytes(self, key):
        try:
            return self.files[key]
        except KeyError as exc:
            raise FileNotFoundError(key) from exc

    async def write_bytes_if_match(self, key, data, *, condition, content_type=None):
        current = await self.get_version(key)
        if condition.require_absent:
            matches = not current.exists
        else:
            matches = current.token == condition.version_token
        if not matches:
            return ConditionalWriteResult(ok=False, conflict=True, current_version=current)
        self.files[key] = data
        self.writes.append((key, data, content_type))
        return ConditionalWriteResult(ok=True, current_version=await self.get_version(key))


def _agent(tenant_id):
    return SimpleNamespace(id=uuid.uuid4(), tenant_id=tenant_id)


@pytest.mark.asyncio
async def test_dry_run_reports_only_exact_legacy_matches_without_writing():
    tenant_id = uuid.uuid4()
    legacy_agent = _agent(tenant_id)
    current_agent = _agent(tenant_id)
    custom_agent = _agent(tenant_id)
    missing_agent = _agent(tenant_id)
    current = b"# current heartbeat\n"
    storage = _Storage(
        {
            f"{legacy_agent.id}/HEARTBEAT.md": LEGACY_FIXTURE,
            f"{current_agent.id}/HEARTBEAT.md": current,
            f"{custom_agent.id}/HEARTBEAT.md": b"# custom heartbeat\n",
        }
    )
    session = _Session([tenant_id], [[legacy_agent, current_agent, custom_agent, missing_agent]])

    report = await migration.migrate_legacy_heartbeat_templates(
        session,
        storage,
        current_template=current,
        apply=False,
        legacy_sha256=LEGACY_FIXTURE_SHA256,
    )

    assert report.total.agents_scanned == 4
    assert report.total.legacy_matches == 1
    assert report.total.dry_run_matches == 1
    assert report.total.migrated == 0
    assert report.total.skipped_current == 1
    assert report.total.skipped_custom == 1
    assert report.total.skipped_missing == 1
    assert report.total.conflicts == 0
    assert report.total.errors == 0
    assert report.by_tenant[str(tenant_id)] == report.total
    assert storage.writes == []

    tenant_sql = str(session.statements[0])
    agent_sql = str(session.statements[1])
    assert "tenants.is_active IS true" in tenant_sql
    assert "agents.tenant_id" in agent_sql
    assert "agents.status IN" not in agent_sql
    assert "agents.deleted_at IS NULL" in agent_sql


@pytest.mark.asyncio
async def test_apply_replaces_legacy_template_and_second_run_is_idempotent():
    tenant_id = uuid.uuid4()
    agent = _agent(tenant_id)
    key = f"{agent.id}/HEARTBEAT.md"
    current = b"# current heartbeat\n"
    storage = _Storage({key: LEGACY_FIXTURE})

    first = await migration.migrate_legacy_heartbeat_templates(
        _Session([tenant_id], [[agent]]),
        storage,
        current_template=current,
        apply=True,
        legacy_sha256=LEGACY_FIXTURE_SHA256,
    )
    second = await migration.migrate_legacy_heartbeat_templates(
        _Session([tenant_id], [[agent]]),
        storage,
        current_template=current,
        apply=True,
        legacy_sha256=LEGACY_FIXTURE_SHA256,
    )

    assert first.total.migrated == 1
    assert second.total.migrated == 0
    assert second.total.skipped_current == 1
    assert storage.files[key] == current
    assert storage.writes == [(key, current, "text/markdown; charset=utf-8")]


@pytest.mark.asyncio
async def test_apply_counts_compare_and_swap_conflict_without_overwriting():
    tenant_id = uuid.uuid4()
    agent = _agent(tenant_id)
    key = f"{agent.id}/HEARTBEAT.md"
    storage = _Storage({key: LEGACY_FIXTURE})
    original_write = storage.write_bytes_if_match

    async def racing_write(key, data, *, condition, content_type=None):
        storage.files[key] = b"# user changed this during migration\n"
        return await original_write(key, data, condition=condition, content_type=content_type)

    storage.write_bytes_if_match = racing_write

    report = await migration.migrate_legacy_heartbeat_templates(
        _Session([tenant_id], [[agent]]),
        storage,
        current_template=b"# current heartbeat\n",
        apply=True,
        legacy_sha256=LEGACY_FIXTURE_SHA256,
    )

    assert report.total.legacy_matches == 1
    assert report.total.migrated == 0
    assert report.total.conflicts == 1
    assert storage.files[key] == b"# user changed this during migration\n"


@pytest.mark.asyncio
async def test_fallback_storage_dry_run_does_not_materialize_or_write_primary():
    tenant_id = uuid.uuid4()
    agent = _agent(tenant_id)
    key = f"{agent.id}/HEARTBEAT.md"
    primary = _Storage({})
    fallback = _Storage({key: LEGACY_FIXTURE})

    report = await migration.migrate_legacy_heartbeat_templates(
        _Session([tenant_id], [[agent]]),
        FallbackStorageBackend(primary=primary, fallback=fallback),
        current_template=b"# current heartbeat\n",
        apply=False,
        legacy_sha256=LEGACY_FIXTURE_SHA256,
    )

    assert report.total.dry_run_matches == 1
    assert primary.files == {}
    assert primary.writes == []


@pytest.mark.asyncio
async def test_fallback_apply_does_not_cross_backend_cas_after_source_race():
    tenant_id = uuid.uuid4()
    agent = _agent(tenant_id)
    key = f"{agent.id}/HEARTBEAT.md"
    primary = _Storage({})
    fallback = _Storage({key: LEGACY_FIXTURE})
    original_read = fallback.read_bytes

    async def racing_fallback_read(key):
        data = await original_read(key)
        fallback.files[key] = b"# user changed fallback during migration\n"
        return data

    fallback.read_bytes = racing_fallback_read

    report = await migration.migrate_legacy_heartbeat_templates(
        _Session([tenant_id], [[agent]]),
        FallbackStorageBackend(primary=primary, fallback=fallback),
        current_template=b"# current heartbeat\n",
        apply=True,
        legacy_sha256=LEGACY_FIXTURE_SHA256,
    )

    assert report.total.legacy_matches == 1
    assert report.total.migrated == 0
    assert report.total.conflicts == 1
    assert report.total.skipped_fallback_unmaterialized == 1
    assert primary.files == {}
    assert primary.writes == []
    assert fallback.files[key] == b"# user changed fallback during migration\n"


def test_cli_defaults_to_dry_run_and_requires_apply_flag():
    assert migration.LEGACY_HEARTBEAT_SHA256 == "377e8e367d3aaa13d3932335787340363a88105fabe9717f758d90480843a6cd"
    assert hashlib.sha256(migration._current_template_bytes()).hexdigest() == (
        "cb4dfa9c49a226a39cd1befd266f7d43a36685f80485c13f14833b2d330a25cd"
    )
    assert migration.parse_args([]).apply is False
    assert migration.parse_args(["--apply"]).apply is True
