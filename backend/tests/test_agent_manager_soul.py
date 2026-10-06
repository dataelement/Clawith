import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest


class _MemoryStorage:
    def __init__(self):
        self.objects: dict[str, bytes] = {}

    async def exists(self, key):
        return key in self.objects

    async def is_dir(self, key):
        return any(stored_key.startswith(f"{key}/") for stored_key in self.objects)

    async def write_bytes(self, key, value):
        self.objects[key] = value

    async def write_text(self, key, value, encoding="utf-8"):
        self.objects[key] = value.encode(encoding)

    async def read_text(self, key, encoding="utf-8", errors=None):
        return self.objects[key].decode(encoding, errors=errors)


class _Result:
    def __init__(self, value):
        self.value = value

    def scalar_one_or_none(self):
        return self.value


class _Database:
    def __init__(self, selected_soul=None):
        self.selected_soul = selected_soul

    async def execute(self, _query):
        return _Result(self.selected_soul)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("locale", "expected_text"),
    [
        ("en", "Responsible and detail-oriented"),
        ("zh", "认真负责、注重细节"),
        (None, "认真负责、注重细节"),
    ],
)
async def test_agent_initialization_selects_default_soul_by_locale(
    monkeypatch, locale, expected_text
):
    from app.services import agent_manager as agent_manager_module

    storage = _MemoryStorage()
    monkeypatch.setattr(agent_manager_module, "get_storage_backend", lambda: storage)
    monkeypatch.setattr(
        agent_manager_module.settings,
        "AGENT_TEMPLATE_DIR",
        str(Path(__file__).parents[1] / "agent_template"),
    )
    creator = SimpleNamespace(display_name="Ray")

    async def execute_query(_db, _query):
        return _Result(creator)

    monkeypatch.setattr(agent_manager_module.query_dao, "execute", execute_query)
    agent = SimpleNamespace(
        id=uuid.uuid4(),
        creator_id=uuid.uuid4(),
        template_id=None,
        name="Evidence Agent",
    )

    await agent_manager_module.AgentManager().initialize_agent_files(
        _Database(),
        agent,
        locale=locale,
    )

    soul = storage.objects[f"{agent.id}/soul.md"].decode()
    assert expected_text in soul
    assert "Evidence Agent" in soul
    assert "Ray" in soul
    assert "{{agent_name}}" not in soul
    assert "{{creator_name}}" not in soul
    assert "{{created_at}}" not in soul
    assert f"{agent.id}/soul.en.md" not in storage.objects


@pytest.mark.asyncio
async def test_explicit_agent_template_soul_takes_precedence_over_locale_default(
    monkeypatch
):
    from app.services import agent_manager as agent_manager_module

    storage = _MemoryStorage()
    monkeypatch.setattr(agent_manager_module, "get_storage_backend", lambda: storage)
    monkeypatch.setattr(
        agent_manager_module.settings,
        "AGENT_TEMPLATE_DIR",
        str(Path(__file__).parents[1] / "agent_template"),
    )

    async def execute_query(_db, _query):
        return _Result(SimpleNamespace(display_name="Ray"))

    monkeypatch.setattr(agent_manager_module.query_dao, "execute", execute_query)
    agent = SimpleNamespace(
        id=uuid.uuid4(),
        creator_id=uuid.uuid4(),
        template_id=uuid.uuid4(),
        name="Selected Agent",
    )
    db = _Database(
        selected_soul="# Selected Template\n\n## Identity\nCreated for {{agent_name}}"
    )

    await agent_manager_module.AgentManager().initialize_agent_files(
        db,
        agent,
        locale="en",
    )

    soul = storage.objects[f"{agent.id}/soul.md"].decode()
    assert soul == "# Selected Template\n\n## Identity\nCreated for Selected Agent"
    assert "Responsible and detail-oriented" not in soul


def test_agent_create_locale_is_optional_and_validated():
    from pydantic import ValidationError

    from app.schemas.schemas import AgentCreate

    assert AgentCreate(name="Agent").locale is None
    assert AgentCreate(name="Agent", locale="en").locale == "en"
    assert AgentCreate(name="Agent", locale="zh").locale == "zh"
    with pytest.raises(ValidationError):
        AgentCreate(name="Agent", locale="fr")


def test_agent_soul_template_never_receives_role_description_metadata():
    from app.services.agent_manager import _render_soul_template

    rendered = _render_soul_template(
        """# Soul — {{agent_name}}

## Identity
- Name: {{agent_name}}
- Role: {{role_description}}
- Creator: {{creator_name}}
- Created: {{created_at}}
""",
        agent_name="Evidence Agent",
        creator_name="Ray",
        created_at="2026-07-16",
    )

    assert "Evidence Agent" in rendered
    assert "Ray" in rendered
    assert "2026-07-16" in rendered
    assert "role_description" not in rendered
    assert "- Role:" not in rendered


def test_demo_seed_does_not_copy_product_role_metadata_into_soul():
    from pathlib import Path

    seed_source = (Path(__file__).parents[1] / "seed.py").read_text(encoding="utf-8")

    copied_role_pattern = (
        'soul_path.write_text(f"# {agent.name}\\n\\n{agent.role_description}'
    )
    assert copied_role_pattern not in seed_source
    assert "_Describe your identity, responsibilities, and boundaries._" in seed_source


def test_agent_template_soul_uses_the_selected_agent_name_placeholder():
    from app.services.agent_manager import _render_soul_template

    rendered = _render_soul_template(
        "# Soul — {name}\n\n## Identity\nTemplate-owned identity",
        agent_name="Risk Partner",
        creator_name="Ray",
        created_at="2026-07-16",
    )

    assert rendered.startswith("# Soul — Risk Partner")
    assert "{name}" not in rendered
    assert "Template-owned identity" in rendered
