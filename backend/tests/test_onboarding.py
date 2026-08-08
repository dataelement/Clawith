import uuid
from types import SimpleNamespace

import pytest

from app.services.onboarding import (
    PHASE_CUSTOM_STYLE,
    PHASE_GREETED,
    PHASE_TEMPLATE_FOCUS,
    _CUSTOM_CONFIG_PROMPT,
    _TEMPLATE_FINALIZE_PROMPT,
    _render_template_greeting,
    resolve_onboarding_prompt,
)


class DummyResult:
    def __init__(self, *, scalar_value=None):
        self._scalar_value = scalar_value

    def scalar_one_or_none(self):
        return self._scalar_value

    def scalar_one(self):
        return self._scalar_value


class RecordingDB:
    def __init__(self, responses):
        self.responses = list(responses)

    async def execute(self, _statement):
        if not self.responses:
            raise AssertionError("unexpected execute() call")
        return self.responses.pop(0)


def _make_agent(*, template_id=None):
    return SimpleNamespace(
        id=uuid.uuid4(),
        name="helper",
        role_description="assistant",
        template_id=template_id,
    )


def test_template_greeting_uses_name_and_soul_without_reinjecting_product_role():
    agent = _make_agent(template_id=uuid.uuid4())
    agent.role_description = "THIS PRODUCT ROLE MUST NOT ENTER THE PROMPT"

    prompt = _render_template_greeting(
        agent,
        ["Analyze evidence", "Write reports"],
        "Ray",
    )

    assert "**helper**" in prompt
    assert "THIS PRODUCT ROLE MUST NOT ENTER THE PROMPT" not in prompt
    assert "Analyze evidence" in prompt


def test_finalize_instructions_do_not_claim_unavailable_workspace_or_focus_tools():
    for prompt in (_CUSTOM_CONFIG_PROMPT, _TEMPLATE_FINALIZE_PROMPT):
        assert "current Tool Schema" in prompt
        assert "do not simulate" in prompt.lower()
        assert "You MUST persist" not in prompt


def test_template_sources_do_not_ship_a_second_bootstrap_prompt():
    from app.models.agent import AgentTemplate
    from app.services.template_seeder import _TEMPLATE_ROOT, _merged_templates

    templates = _merged_templates()

    assert templates
    assert "bootstrap_content" not in AgentTemplate.__table__.columns
    assert all("bootstrap_content" not in template for template in templates)
    assert list(_TEMPLATE_ROOT.glob("*/bootstrap.md")) == []


@pytest.mark.asyncio
async def test_first_contact_is_the_only_tool_free_greeting_turn():
    db = RecordingDB(
        [
            DummyResult(scalar_value=None),  # onboarding row
        ]
    )

    injection = await resolve_onboarding_prompt(
        db,
        _make_agent(),
        uuid.uuid4(),
        user_name="Ray",
        user_locale="zh",
    )

    assert injection is not None
    assert injection.is_greeting_turn is True


@pytest.mark.asyncio
async def test_template_follow_up_keeps_tools_enabled():
    template_id = uuid.uuid4()
    db = RecordingDB(
        [
            DummyResult(scalar_value=SimpleNamespace(phase=PHASE_GREETED)),
            DummyResult(
                scalar_value=SimpleNamespace(
                    capability_bullets=["Install apps"],
                )
            ),
        ]
    )

    injection = await resolve_onboarding_prompt(
        db,
        _make_agent(template_id=template_id),
        uuid.uuid4(),
        user_name="Ray",
        user_locale="zh",
    )

    assert injection is not None
    assert injection.target_phase == PHASE_TEMPLATE_FOCUS
    assert injection.is_greeting_turn is False


@pytest.mark.asyncio
async def test_template_first_contact_uses_shared_flow_without_bootstrap_content():
    template_id = uuid.uuid4()
    db = RecordingDB(
        [
            DummyResult(scalar_value=None),
            DummyResult(
                scalar_value=SimpleNamespace(
                    capability_bullets=["Analyze evidence"],
                )
            ),
        ]
    )

    injection = await resolve_onboarding_prompt(
        db,
        _make_agent(template_id=template_id),
        uuid.uuid4(),
        user_name="Ray",
        user_locale="zh",
    )

    assert injection is not None
    assert injection.target_phase == PHASE_GREETED
    assert injection.is_greeting_turn is True
    assert "Analyze evidence" in injection.prompt


@pytest.mark.asyncio
async def test_custom_follow_up_keeps_tools_enabled():
    db = RecordingDB(
        [
            DummyResult(scalar_value=SimpleNamespace(phase=PHASE_GREETED)),
        ]
    )

    injection = await resolve_onboarding_prompt(
        db,
        _make_agent(),
        uuid.uuid4(),
        user_name="Ray",
        user_locale="zh",
    )

    assert injection is not None
    assert injection.target_phase == PHASE_CUSTOM_STYLE
    assert injection.is_greeting_turn is False


@pytest.mark.asyncio
async def test_custom_boundary_follow_up_keeps_tools_enabled():
    db = RecordingDB(
        [
            DummyResult(scalar_value=SimpleNamespace(phase=PHASE_CUSTOM_STYLE)),
        ]
    )

    injection = await resolve_onboarding_prompt(
        db,
        _make_agent(),
        uuid.uuid4(),
        user_name="Ray",
        user_locale="zh",
    )

    assert injection is not None
    assert injection.is_greeting_turn is False
