import uuid
from types import SimpleNamespace

import pytest

from app.services.onboarding import (
    PHASE_CUSTOM_STYLE,
    PHASE_GREETED,
    PHASE_TEMPLATE_FOCUS,
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


@pytest.mark.asyncio
async def test_first_contact_is_the_only_tool_free_greeting_turn():
    db = RecordingDB(
        [
            DummyResult(scalar_value=None),  # onboarding row
            DummyResult(scalar_value=0),  # user turns
            DummyResult(scalar_value=0),  # peer count
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
            DummyResult(scalar_value=1),  # user turns
            DummyResult(scalar_value=1),  # peer count
            DummyResult(
                scalar_value=SimpleNamespace(
                    capability_bullets=["Install apps"],
                    bootstrap_content="preset bootstrap",
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
async def test_custom_follow_up_keeps_tools_enabled():
    db = RecordingDB(
        [
            DummyResult(scalar_value=SimpleNamespace(phase=PHASE_GREETED)),
            DummyResult(scalar_value=1),  # user turns
            DummyResult(scalar_value=1),  # peer count
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
            DummyResult(scalar_value=2),  # user turns
            DummyResult(scalar_value=1),  # peer count
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
