import io
import zipfile
import uuid
from types import SimpleNamespace

import httpx
import pytest
from fastapi import HTTPException

from app.api import skills as skills_api
from app.core.security import get_current_admin, get_current_user
from app.main import app


class FakeScalarResult:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class TrapList(list):
    def __iter__(self):
        raise AssertionError("newly created skills should not iterate over lazy files")


class FakeSession:
    def __init__(self, *, skill=None):
        self.skill = skill
        self.added = []
        self.deleted = []
        self.committed = False

    async def execute(self, _query):
        return FakeScalarResult(self.skill)

    def add(self, value):
        self.added.append(value)

    async def flush(self):
        return None

    async def delete(self, value):
        self.deleted.append(value)

    async def commit(self):
        self.committed = True


class FakeAsyncSessionFactory:
    def __init__(self, session):
        self.session = session

    def __call__(self):
        return self

    async def __aenter__(self):
        return self.session

    async def __aexit__(self, exc_type, exc, tb):
        return False


class FakeQuery:
    def where(self, *_args, **_kwargs):
        return self

    def options(self, *_args, **_kwargs):
        return self

    def order_by(self, *_args, **_kwargs):
        return self


class RaiseOnInstanceAccess:
    def __get__(self, instance, owner):
        if instance is None:
            return self
        raise AssertionError("newly created skills should not iterate over lazy files")


class QueryField:
    def is_(self, _value):
        return self

    def __eq__(self, _other):
        return self


class FakeSkill:
    folder_name = QueryField()
    tenant_id = QueryField()
    files = RaiseOnInstanceAccess()

    def __init__(self, **kwargs):
        self.id = uuid.uuid4()
        for key, value in kwargs.items():
            setattr(self, key, value)


@pytest.fixture
def org_admin_user():
    return SimpleNamespace(
        id=uuid.uuid4(),
        role="org_admin",
        tenant_id=uuid.uuid4(),
        is_active=True,
        department_id=None,
    )


@pytest.fixture
def platform_admin_user():
    return SimpleNamespace(
        id=uuid.uuid4(),
        role="platform_admin",
        tenant_id=uuid.uuid4(),
        is_active=True,
        department_id=None,
    )


@pytest.fixture
def client():
    transport = httpx.ASGITransport(app=app)

    async def _build():
        return httpx.AsyncClient(transport=transport, base_url="http://test")

    return _build


@pytest.mark.asyncio
async def test_org_admin_can_delete_custom_skill_via_browse(monkeypatch, client, org_admin_user):
    skill = SimpleNamespace(
        id=uuid.uuid4(),
        folder_name="tenant-skill",
        tenant_id=org_admin_user.tenant_id,
        is_builtin=False,
        files=[],
    )
    session = FakeSession(skill=skill)

    monkeypatch.setattr(skills_api, "async_session", FakeAsyncSessionFactory(session))
    app.dependency_overrides[get_current_user] = lambda: org_admin_user

    async with await client() as ac:
        response = await ac.delete("/api/skills/browse/delete", params={"path": "tenant-skill"})

    app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json() == {"ok": True}
    assert session.deleted == [skill]
    assert session.committed is True


@pytest.mark.asyncio
async def test_org_admin_can_delete_custom_skill_directly(monkeypatch, client, org_admin_user):
    skill = SimpleNamespace(
        id=uuid.uuid4(),
        folder_name="tenant-skill",
        tenant_id=org_admin_user.tenant_id,
        is_builtin=False,
    )
    session = FakeSession(skill=skill)

    monkeypatch.setattr(skills_api, "async_session", FakeAsyncSessionFactory(session))
    app.dependency_overrides[get_current_user] = lambda: org_admin_user

    async with await client() as ac:
        response = await ac.delete(f"/api/skills/{skill.id}")

    app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json() == {"ok": True}
    assert session.deleted == [skill]
    assert session.committed is True


@pytest.mark.asyncio
async def test_browse_write_creates_tenant_skill_without_iterating_lazy_files(
    monkeypatch, client, platform_admin_user
):
    session = FakeSession(skill=None)

    monkeypatch.setattr(skills_api, "async_session", FakeAsyncSessionFactory(session))
    monkeypatch.setattr(skills_api, "select", lambda *_args, **_kwargs: FakeQuery())
    monkeypatch.setattr(skills_api, "selectinload", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(skills_api, "Skill", FakeSkill)
    app.dependency_overrides[get_current_user] = lambda: platform_admin_user

    async with await client() as ac:
        response = await ac.put(
            "/api/skills/browse/write",
            json={"path": "tenant-skill/SKILL.md", "content": "# test"},
        )

    app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json() == {"ok": True}
    created_skill = next(value for value in session.added if isinstance(value, FakeSkill))
    created_file = next(value for value in session.added if isinstance(value, skills_api.SkillFile))
    assert created_skill.folder_name == "tenant-skill"
    assert created_skill.tenant_id == platform_admin_user.tenant_id
    assert created_file.path == "SKILL.md"
    assert created_file.content == "# test"
    assert session.committed is True


class FakeSkillFile:
    def __init__(self, path: str, content: str):
        self.path = path
        self.content = content


def _zip_bytes(files: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for path, content in files.items():
            archive.writestr(path, content)
    return buffer.getvalue()


@pytest.mark.asyncio
async def test_preview_folder_upload_reports_existing_registry_diff(monkeypatch, platform_admin_user):
    existing_skill = SimpleNamespace(
        id=uuid.uuid4(),
        folder_name="demo-skill",
        name="Demo",
        description="old",
        icon="📋",
        category="general",
        tenant_id=platform_admin_user.tenant_id,
        files=[FakeSkillFile("SKILL.md", "# Old\n"), FakeSkillFile("stale.txt", "remove\n")],
    )
    session = FakeSession(skill=existing_skill)
    monkeypatch.setattr(skills_api, "async_session", FakeAsyncSessionFactory(session))

    archive = _zip_bytes({"demo-skill/SKILL.md": b"# New\n", "demo-skill/scripts/run.py": b"print(\'ok\')\n"})
    result = await skills_api.preview_folder_upload_from_archive(
        archive,
        target_folder="demo-skill",
        current_user=platform_admin_user,
    )

    assert result["mode"] == "update"
    assert result["deleted_paths"] == ["stale.txt"]


@pytest.mark.asyncio
async def test_apply_folder_upload_replaces_registry_files(monkeypatch, platform_admin_user):
    existing_skill = SimpleNamespace(
        id=uuid.uuid4(),
        folder_name="demo-skill",
        name="Demo",
        description="old",
        icon="📋",
        category="general",
        tenant_id=platform_admin_user.tenant_id,
        files=[FakeSkillFile("SKILL.md", "# Old\n"), FakeSkillFile("stale.txt", "remove\n")],
    )
    session = FakeSession(skill=existing_skill)
    monkeypatch.setattr(skills_api, "async_session", FakeAsyncSessionFactory(session))

    archive = _zip_bytes(
        {
            "demo-skill/SKILL.md": (
                b"---\nname: Uploaded Demo\n"
                b"description: updated description\n"
                b"icon: \xf0\x9f\x9a\x80\n"
                b"category: automation\n"
                b"---\n\n# New\n"
            ),
            "demo-skill/scripts/run.py": b"print(\'ok\')\n",
        }
    )
    preview = await skills_api.preview_folder_upload_from_archive(
        archive,
        target_folder="demo-skill",
        current_user=platform_admin_user,
    )

    result = await skills_api.apply_folder_upload_from_archive(
        archive,
        target_folder="demo-skill",
        expected_digest=preview["digest"],
        expected_target_state_digest=preview["target_state_digest"],
        replace_confirmed=True,
        current_user=platform_admin_user,
    )

    assert result["mode"] == "update"
    assert result["files_written"] == 2
    assert result["deleted_count"] == 1
    assert existing_skill.name == "Uploaded Demo"
    assert existing_skill.description == "updated description"
    assert existing_skill.icon == "🚀"
    assert existing_skill.category == "automation"
    assert session.deleted == existing_skill.files
    written_files = [value for value in session.added if isinstance(value, skills_api.SkillFile)]
    assert [(value.path, value.content) for value in written_files] == [
        (
            "SKILL.md",
            "---\nname: Uploaded Demo\n"
            "description: updated description\n"
            "icon: 🚀\n"
            "category: automation\n"
            "---\n\n# New\n",
        ),
        ("scripts/run.py", "print(\'ok\')\n"),
    ]
    assert all(value.path != "stale.txt" for value in written_files)
    assert session.committed is True


@pytest.mark.asyncio
async def test_preview_folder_upload_rejects_missing_root_skill_md(monkeypatch, platform_admin_user):
    session = FakeSession(skill=None)
    monkeypatch.setattr(skills_api, "async_session", FakeAsyncSessionFactory(session))

    archive = _zip_bytes({"demo-skill/readme.md": b"missing\n"})

    with pytest.raises(HTTPException) as exc:
        await skills_api.preview_folder_upload_from_archive(
            archive,
            target_folder="demo-skill",
            current_user=platform_admin_user,
        )

    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_preview_folder_upload_rejects_invalid_target_folder(monkeypatch, platform_admin_user):
    session = FakeSession(skill=None)
    monkeypatch.setattr(skills_api, "async_session", FakeAsyncSessionFactory(session))

    archive = _zip_bytes({"demo-skill/SKILL.md": b"# Demo\n"})

    with pytest.raises(HTTPException) as exc:
        await skills_api.preview_folder_upload_from_archive(
            archive,
            target_folder="bad/name",
            current_user=platform_admin_user,
        )

    assert exc.value.status_code == 400
    assert "folder" in str(exc.value.detail).lower()


@pytest.mark.asyncio
async def test_upload_folder_apply_requires_target_state_digest_field(client, monkeypatch, platform_admin_user):
    async def _should_not_run(**_kwargs):
        raise AssertionError("handler should not run when required form field is missing")

    monkeypatch.setattr(skills_api, "apply_folder_upload_from_archive", _should_not_run)
    app.dependency_overrides[get_current_admin] = lambda: platform_admin_user

    files = {"file": ("demo-skill.zip", _zip_bytes({"demo-skill/SKILL.md": b"# Demo\n"}), "application/zip")}
    data = {
        "target_folder": "demo-skill",
        "expected_digest": "digest",
        "replace_confirmed": "true",
    }

    async with await client() as ac:
        response = await ac.post("/api/skills/upload-folder/apply", data=data, files=files)

    app.dependency_overrides.clear()

    assert response.status_code == 422
