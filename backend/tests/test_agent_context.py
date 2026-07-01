import uuid
from unittest.mock import AsyncMock, patch

import pytest


@pytest.mark.asyncio
async def test_build_agent_context_uses_focus_tools_instead_of_focus_file():
    from app.services.agent_context import build_agent_context

    agent_id = uuid.uuid4()

    async def fake_read_file(key, _max_chars=3000):
        if key == f"{agent_id}/focus.md":
            return "# Focus\n\n- [ ] follow_up: Check the deployment"
        return ""

    with (
        patch("app.services.agent_context._read_file_safe", side_effect=fake_read_file),
        patch("app.services.agent_context._load_skills_index", new_callable=AsyncMock, return_value=""),
        patch("app.services.timezone_utils.get_agent_timezone", new_callable=AsyncMock, return_value="UTC"),
    ):
        _static, dynamic = await build_agent_context(agent_id, "TestAgent")

    prompt = f"{_static}\n{dynamic}"
    assert "## Focus" not in prompt
    assert "follow_up: Check the deployment" not in prompt
    assert "list_focus_items" in prompt
    assert "Do not read, write, or edit focus.md" in prompt


@pytest.mark.asyncio
async def test_build_agent_context_does_not_prompt_legacy_human_send_path():
    from app.services.agent_context import build_agent_context

    agent_id = uuid.uuid4()

    async def fake_read_file(_key, _max_chars=3000):
        return ""

    with (
        patch("app.services.agent_context._read_file_safe", side_effect=fake_read_file),
        patch("app.services.agent_context._load_skills_index", new_callable=AsyncMock, return_value=""),
        patch("app.services.agent_context._load_relationships_from_db", new_callable=AsyncMock, return_value=""),
        patch("app.services.timezone_utils.get_agent_timezone", new_callable=AsyncMock, return_value="UTC"),
    ):
        static, dynamic = await build_agent_context(agent_id, "TestAgent")

    prompt = f"{static}\n{dynamic}"
    assert "send_feishu_message" not in prompt
    assert "## 人类同事背景" not in prompt
    assert "query_directory(member_type=\"human\"" in prompt
    assert "send_platform_message(target_member_id=" in prompt
    assert "send_channel_message(target_member_id=" in prompt
