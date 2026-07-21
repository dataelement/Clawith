import uuid
from unittest.mock import AsyncMock, patch

import pytest

from app.services.storage import StorageEntry


def _context_patches(*, soul: str = "", memory: str = "", skills: str = ""):
    agent_id_holder: dict[str, uuid.UUID] = {}

    async def fake_read_file(key, _max_chars=3000):
        agent_id = agent_id_holder["agent_id"]
        if key == f"{agent_id}/soul.md":
            return soul
        if key in {f"{agent_id}/memory/memory.md", f"{agent_id}/memory.md"}:
            return memory
        return ""

    return agent_id_holder, (
        patch("app.services.agent_context._read_file_safe", side_effect=fake_read_file),
        patch(
            "app.services.agent_context._load_skills_index",
            new_callable=AsyncMock,
            return_value=skills,
        ),
        patch(
            "app.services.agent_context._load_relationships_from_db",
            new_callable=AsyncMock,
            return_value="",
        ),
        patch(
            "app.services.timezone_utils.get_agent_timezone",
            new_callable=AsyncMock,
            return_value="UTC",
        ),
    )


@pytest.mark.asyncio
async def test_base_prompt_starts_with_name_and_soul_and_never_injects_self_role():
    from app.services.agent_context import build_agent_context

    agent_id = uuid.uuid4()
    holder, patches = _context_patches(
        soul="# Soul\nBe precise and preserve evidence.",
        memory="# Memory\nThe release owner is Alice.",
    )
    holder["agent_id"] = agent_id

    with patches[0], patches[1], patches[2], patches[3]:
        static, dynamic = await build_agent_context(
            agent_id,
            "TestAgent",
            "THIS ROLE MUST NOT ENTER THE MODEL",
            allowed_tool_names={"finish", "wait"},
        )

    assert static.startswith("# Identity\n\nYou are TestAgent, a digital employee in Clawith.")
    assert "<soul>\nBe precise and preserve evidence.\n</soul>" in static
    assert static.index("<soul>") < static.index("# Clawith Environment")
    assert "THIS ROLE MUST NOT ENTER THE MODEL" not in f"{static}\n{dynamic}"
    assert "# Memory" in static
    assert "The release owner is Alice." not in static
    assert "The release owner is Alice." in dynamic
    assert "## Role" not in static


@pytest.mark.asyncio
async def test_focus_mechanism_is_constant_but_tool_policy_follows_effective_tools():
    from app.services.agent_context import build_agent_context

    agent_id = uuid.uuid4()
    holder, patches = _context_patches()
    holder["agent_id"] = agent_id

    with patches[0], patches[1], patches[2], patches[3]:
        without_tools, _ = await build_agent_context(
            agent_id,
            "TestAgent",
            allowed_tool_names={"finish", "wait"},
        )
        with_focus_tools, _ = await build_agent_context(
            agent_id,
            "TestAgent",
            allowed_tool_names={
                "finish",
                "wait",
                "list_focus_items",
                "upsert_focus_item",
                "complete_focus_item",
            },
        )

    assert "## Focus" in without_tools
    assert "Focus is your structured persistent working state" in without_tools
    assert "list_focus_items" not in without_tools
    assert "list_focus_items" in with_focus_tools
    assert "Do not read or write `focus.md`" in with_focus_tools


@pytest.mark.asyncio
async def test_skill_catalog_requires_read_file_and_prompt_has_no_hardcoded_channel_manuals():
    from app.services.agent_context import build_agent_context

    agent_id = uuid.uuid4()
    holder, patches = _context_patches(
        skills="| Risk Review | Check release risks | skills/risk/SKILL.md |",
    )
    holder["agent_id"] = agent_id

    with patches[0], patches[1], patches[2], patches[3]:
        without_loader, _ = await build_agent_context(
            agent_id,
            "TestAgent",
            allowed_tool_names={"finish", "wait"},
        )
        with_loader, _ = await build_agent_context(
            agent_id,
            "TestAgent",
            allowed_tool_names={"finish", "wait", "read_file", "list_files"},
        )

    assert "Risk Review" not in without_loader
    assert "# Available Skills" in with_loader
    assert "skills/risk/SKILL.md" in with_loader
    assert "MCP Import Rules" not in with_loader
    assert "atlassian_jira_search_issues" not in with_loader
    assert "Pre-installed Feishu Tools" not in with_loader


@pytest.mark.asyncio
async def test_lowercase_skill_entry_advertises_the_actual_readable_path(monkeypatch):
    from app.services import agent_context

    agent_id = uuid.uuid4()
    prefix = f"{agent_id}/skills"
    folder_key = f"{prefix}/risk-review"
    lowercase_key = f"{folder_key}/skill.md"

    class _Storage:
        async def exists(self, key):
            return key in {prefix, folder_key, lowercase_key}

        async def is_dir(self, key):
            return key in {prefix, folder_key}

        async def list_dir(self, key):
            assert key == prefix
            return [
                StorageEntry(
                    name="risk-review",
                    key=folder_key,
                    is_dir=True,
                )
            ]

        async def read_text(self, key, **_kwargs):
            assert key == lowercase_key
            return "---\nname: Risk Review\ndescription: Check release risks\n---\n"

    monkeypatch.setattr(agent_context, "get_storage_backend", lambda: _Storage())

    catalog = await agent_context._load_skills_index(agent_id)

    assert "skills/risk-review/skill.md" in catalog
    assert "skills/risk-review/SKILL.md" not in catalog


@pytest.mark.asyncio
async def test_directory_and_human_send_policies_only_name_enabled_tools():
    from app.services.agent_context import build_agent_context

    agent_id = uuid.uuid4()
    holder, patches = _context_patches()
    holder["agent_id"] = agent_id

    with patches[0], patches[1], patches[2], patches[3]:
        static, dynamic = await build_agent_context(
            agent_id,
            "TestAgent",
            allowed_tool_names={
                "finish",
                "wait",
                "query_directory",
                "send_platform_message",
                "send_channel_message",
            },
        )

    prompt = f"{static}\n{dynamic}"
    assert "send_feishu_message" not in prompt
    assert "query_directory" in prompt
    assert "send_platform_message" in prompt
    assert "send_channel_message" in prompt


@pytest.mark.asyncio
async def test_experience_policy_is_short_and_only_names_enabled_operations():
    from app.services.agent_context import build_agent_context

    agent_id = uuid.uuid4()
    holder, patches = _context_patches()
    holder["agent_id"] = agent_id

    with patches[0], patches[1], patches[2], patches[3]:
        read_only, _ = await build_agent_context(
            agent_id,
            "TestAgent",
            allowed_tool_names={
                "finish",
                "wait",
                "search_experience",
                "read_experience",
            },
        )
        with_draft, _ = await build_agent_context(
            agent_id,
            "TestAgent",
            allowed_tool_names={
                "finish",
                "wait",
                "search_experience",
                "read_experience",
                "propose_experience_draft",
            },
        )

    assert "search_experience" in read_only
    assert "read_experience" in read_only
    assert "propose_experience_draft" not in read_only
    assert "现有标签" not in read_only
    assert "propose_experience_draft" in with_draft
