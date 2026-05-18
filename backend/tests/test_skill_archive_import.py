import io
import zipfile

import pytest
from fastapi import HTTPException

from app.services.skill_archive_import import inspect_skill_archive, diff_skill_manifests


def _zip_bytes(entries: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for path, content in entries.items():
            zf.writestr(path, content)
    return buffer.getvalue()


def test_inspect_skill_archive_requires_root_skill_md():
    data = _zip_bytes({"demo/readme.md": b"missing root skill"})

    with pytest.raises(HTTPException) as exc:
        inspect_skill_archive(data, target_folder="demo")

    assert exc.value.status_code == 400
    assert "SKILL.md" in str(exc.value.detail)


def test_diff_skill_manifests_reports_added_changed_deleted():
    uploaded = {
        "SKILL.md": "# New\n",
        "scripts/run.py": "print('new')\n",
    }
    existing = {
        "SKILL.md": "# Old\n",
        "docs/notes.md": "stale\n",
    }

    diff = diff_skill_manifests(uploaded, existing)

    assert diff["added"] == ["scripts/run.py"]
    assert diff["changed"] == ["SKILL.md"]
    assert diff["deleted"] == ["docs/notes.md"]


def test_inspect_skill_archive_strips_single_root_folder():
    data = _zip_bytes({"demo-skill/SKILL.md": b"# Demo\n", "demo-skill/scripts/run.py": b"print('ok')\n"})

    result = inspect_skill_archive(data, target_folder="demo-skill")

    assert sorted(result["files"].keys()) == ["SKILL.md", "scripts/run.py"]


def test_inspect_skill_archive_rejects_parent_traversal():
    data = _zip_bytes({"demo/../secrets.txt": b"bad", "demo/SKILL.md": b"# Demo\n"})

    with pytest.raises(HTTPException):
        inspect_skill_archive(data, target_folder="demo")


def test_inspect_skill_archive_preserves_root_files_without_common_folder():
    data = _zip_bytes({"SKILL.md": b"# Demo\n", "scripts/run.py": b"print('ok')\n"})

    result = inspect_skill_archive(data, target_folder="demo")

    assert sorted(result["files"].keys()) == ["SKILL.md", "scripts/run.py"]


def test_inspect_skill_archive_rejects_leading_slash_paths():
    data = _zip_bytes({"/etc/passwd": b"bad", "SKILL.md": b"# Demo\n"})

    with pytest.raises(HTTPException):
        inspect_skill_archive(data, target_folder="demo")


def test_inspect_skill_archive_rejects_windows_style_parent_traversal():
    data = _zip_bytes({r"..\\evil.txt": b"bad", "SKILL.md": b"# Demo\n"})

    with pytest.raises(HTTPException):
        inspect_skill_archive(data, target_folder="demo")
