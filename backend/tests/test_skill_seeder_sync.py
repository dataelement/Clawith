from types import SimpleNamespace

import pytest

from app.services.skill_seeder import (
    _default_skills_sync_digest,
    _sync_missing_default_skill_files,
)


def _skill(*files: tuple[str, str]):
    return SimpleNamespace(
        folder_name="budget-approval-workflow",
        files=[SimpleNamespace(path=path, content=content) for path, content in files],
    )


def test_default_skills_sync_digest_tracks_files_and_content() -> None:
    base = _skill(("SKILL.md", "instructions"))
    with_script = _skill(
        ("SKILL.md", "instructions"),
        ("scripts/auth.py", "authenticate()"),
    )
    changed_script = _skill(
        ("SKILL.md", "instructions"),
        ("scripts/auth.py", "authenticate_v2()"),
    )

    assert _default_skills_sync_digest([base]) != _default_skills_sync_digest([with_script])
    assert _default_skills_sync_digest([with_script]) != _default_skills_sync_digest([changed_script])


@pytest.mark.asyncio
async def test_sync_missing_files_does_not_overwrite_existing_skill_content() -> None:
    class Storage:
        def __init__(self) -> None:
            self.files = {"agent/skills/budget-approval-workflow/SKILL.md": "custom"}
            self.writes: list[tuple[str, str]] = []

        async def is_file(self, key: str) -> bool:
            return key in self.files

        async def write_text(self, key: str, content: str, *, encoding: str) -> None:
            assert encoding == "utf-8"
            self.files[key] = content
            self.writes.append((key, content))

    storage = Storage()
    written = await _sync_missing_default_skill_files(
        storage,
        "agent",
        _skill(
            ("SKILL.md", "registry"),
            ("scripts/auth.py", "authenticate()"),
        ),
    )

    assert written == 1
    assert storage.files["agent/skills/budget-approval-workflow/SKILL.md"] == "custom"
    assert storage.writes == [
        ("agent/skills/budget-approval-workflow/scripts/auth.py", "authenticate()")
    ]
