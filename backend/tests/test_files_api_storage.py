import uuid
from types import SimpleNamespace

import pytest

from app.api import files
from app.services.agent_manager import AgentManager
from app.services.storage_runtime.base import StorageBackend, StorageEntry, StorageVersion


class PrefixOnlyStorage(StorageBackend):
    def __init__(self, objects: dict[str, bytes] | None = None):
        self.objects = dict(objects or {})

    async def exists(self, key: str) -> bool:
        return key in self.objects

    async def is_file(self, key: str) -> bool:
        return key in self.objects

    async def is_dir(self, key: str) -> bool:
        prefix = key.rstrip("/") + "/"
        return any(existing.startswith(prefix) for existing in self.objects)

    async def list_dir(self, key: str) -> list[StorageEntry]:
        prefix = key.rstrip("/") + "/"
        entries_by_name: dict[str, StorageEntry] = {}
        for existing, data in self.objects.items():
            if not existing.startswith(prefix):
                continue
            rest = existing.removeprefix(prefix)
            name, _, tail = rest.partition("/")
            entries_by_name[name] = StorageEntry(
                name=name,
                key=f"{prefix}{name}",
                is_dir=bool(tail),
                size=0 if tail else len(data),
            )
        return sorted(entries_by_name.values(), key=lambda entry: (not entry.is_dir, entry.name))

    async def read_bytes(self, key: str) -> bytes:
        return self.objects[key]

    async def write_bytes(self, key: str, data: bytes, content_type: str | None = None) -> None:
        self.objects[key] = data

    async def delete(self, key: str) -> None:
        self.objects.pop(key, None)

    async def delete_tree(self, key: str) -> None:
        prefix = key.rstrip("/") + "/"
        for existing in list(self.objects):
            if existing.startswith(prefix):
                self.objects.pop(existing, None)

    async def stat(self, key: str) -> StorageEntry:
        if key not in self.objects:
            raise FileNotFoundError(key)
        return StorageEntry(name=key.rsplit("/", 1)[-1], key=key, is_dir=False, size=len(self.objects[key]))

    async def get_version(self, key: str) -> StorageVersion:
        if key not in self.objects:
            return StorageVersion(key=key, exists=False, is_dir=False)
        token = f"v:{len(self.objects[key])}"
        return StorageVersion(
            key=key,
            exists=True,
            is_dir=False,
            size=len(self.objects[key]),
            version_id=token,
            etag=token,
            content_hash=token,
        )


@pytest.mark.asyncio
async def test_list_files_hides_legacy_focus_file_from_s3_prefix_directory(monkeypatch):
    agent_id = uuid.uuid4()
    storage = PrefixOnlyStorage({f"{agent_id}/focus.md": b"# Focus\n"})
    monkeypatch.setattr(files, "get_storage_backend", lambda: storage)

    async def allow_access(*args, **kwargs):
        return None

    monkeypatch.setattr(files, "check_agent_access", allow_access)
    user = SimpleNamespace(tenant_id=None)

    result = await files.list_files(agent_id, path="", current_user=user, db=None)

    assert result == []


@pytest.mark.asyncio
async def test_list_files_allows_empty_agent_root(monkeypatch):
    agent_id = uuid.uuid4()
    monkeypatch.setattr(files, "get_storage_backend", lambda: PrefixOnlyStorage())

    async def allow_access(*args, **kwargs):
        return None

    monkeypatch.setattr(files, "check_agent_access", allow_access)
    user = SimpleNamespace(tenant_id=None)

    assert await files.list_files(agent_id, path="", current_user=user, db=None) == []


@pytest.mark.asyncio
async def test_list_files_allows_empty_workspace_root(monkeypatch):
    agent_id = uuid.uuid4()
    monkeypatch.setattr(files, "get_storage_backend", lambda: PrefixOnlyStorage())

    async def allow_access(*args, **kwargs):
        return None

    monkeypatch.setattr(files, "check_agent_access", allow_access)
    user = SimpleNamespace(tenant_id=None)

    assert await files.list_files(agent_id, path="workspace", current_user=user, db=None) == []


@pytest.mark.asyncio
async def test_list_files_reports_recursive_directory_total_size(monkeypatch):
    agent_id = uuid.uuid4()
    storage = PrefixOnlyStorage({
        f"{agent_id}/skills/web-research/SKILL.md": b"skill-body",
        f"{agent_id}/skills/web-research/scripts/run.py": b"print('ok')",
        f"{agent_id}/skills/web-research/references/guide.md": b"guide",
    })
    monkeypatch.setattr(files, "get_storage_backend", lambda: storage)

    async def allow_access(*args, **kwargs):
        return None

    monkeypatch.setattr(files, "check_agent_access", allow_access)
    user = SimpleNamespace(tenant_id=None)

    result = await files.list_files(agent_id, path="skills", current_user=user, db=None)

    assert len(result) == 1
    assert result[0].name == "web-research"
    assert result[0].is_dir is True
    assert result[0].size == len(b"skill-body") + len(b"print('ok')") + len(b"guide")


@pytest.mark.asyncio
async def test_read_file_returns_version_token(monkeypatch):
    agent_id = uuid.uuid4()
    storage = PrefixOnlyStorage({f"{agent_id}/workspace/note.md": b"# Note\n"})
    monkeypatch.setattr(files, "get_storage_backend", lambda: storage)

    async def allow_access(*args, **kwargs):
        return None

    monkeypatch.setattr(files, "check_agent_access", allow_access)
    user = SimpleNamespace(tenant_id=None)

    result = await files.read_file(
        agent_id,
        path="workspace/note.md",
        current_user=user,
        db=None,
    )

    assert result.version_token == "v:7"


@pytest.mark.asyncio
async def test_read_file_rejects_legacy_focus_file(monkeypatch):
    agent_id = uuid.uuid4()
    storage = PrefixOnlyStorage({f"{agent_id}/focus.md": b"# Focus\n"})
    monkeypatch.setattr(files, "get_storage_backend", lambda: storage)

    async def allow_access(*args, **kwargs):
        return None

    monkeypatch.setattr(files, "check_agent_access", allow_access)
    user = SimpleNamespace(tenant_id=None)

    with pytest.raises(files.HTTPException) as exc:
        await files.read_file(
            agent_id,
            path="focus.md",
            current_user=user,
            db=None,
        )

    assert exc.value.status_code == 410


@pytest.mark.asyncio
async def test_agent_manager_does_not_reinitialize_s3_prefix_directory(monkeypatch, tmp_path):
    agent_id = uuid.uuid4()
    storage = PrefixOnlyStorage({f"{agent_id}/soul.md": b"existing"})
    monkeypatch.setattr("app.services.agent_manager.get_storage_backend", lambda: storage)
    monkeypatch.setattr("app.services.agent_manager.settings.STORAGE_LOCAL_ROOT", str(tmp_path))

    manager = AgentManager()
    agent = SimpleNamespace(id=agent_id)

    await manager.initialize_agent_files(db=None, agent=agent)

    assert storage.objects[f"{agent_id}/soul.md"] == b"existing"


@pytest.mark.asyncio
async def test_agent_manager_materializes_s3_prefix_directory(monkeypatch, tmp_path):
    agent_id = uuid.uuid4()
    storage = PrefixOnlyStorage({
        f"{agent_id}/soul.md": b"# Soul\n",
        f"{agent_id}/memory/memory.md": b"# Memory\n",
    })
    monkeypatch.setattr("app.services.agent_manager.get_storage_backend", lambda: storage)
    monkeypatch.setattr("app.services.agent_manager.settings.STORAGE_LOCAL_ROOT", str(tmp_path))

    manager = AgentManager()

    agent_dir = await manager._materialize_agent_dir(agent_id)

    assert (agent_dir / "soul.md").read_text(encoding="utf-8") == "# Soul\n"
    assert (agent_dir / "memory" / "memory.md").read_text(encoding="utf-8") == "# Memory\n"
