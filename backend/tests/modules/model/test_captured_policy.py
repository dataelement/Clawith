from dataclasses import replace
from uuid import uuid4

import pytest

from app.infrastructure.errors import InvalidInput
from app.modules.model.public import (
    ModelContextProfile,
    PrivateModelPolicy,
    ResolvedModel,
    validate_resolved_model,
)


def captured():
    model = uuid4()
    policy = PrivateModelPolicy(uuid4(), model, "test", "openai_chat", "model", "https://model.test/v1",
        uuid4(), 8192, 2048, '{"supports_tool_calling":true}', '{"protocol":"openai_chat"}')
    return ResolvedModel(policy, ModelContextProfile(model, "test", "model", 8192, 2048, False, False, False))


def test_captured_policy_is_checked_without_current_configuration():
    value = captured()
    validate_resolved_model(value)
    for modified in (
        replace(value, policy=replace(value.policy, settings_json='{}')),
        replace(value, policy=replace(value.policy, settings_json='{"protocol":"anthropic"}')),
        replace(value, policy=replace(value.policy, capabilities_json='{}')),
        replace(value, policy=replace(value.policy, context_limit=True)),
        replace(value, profile=replace(value.profile, supports_images=True)),
        replace(value, profile=replace(value.profile, supports_streaming=True)),
        replace(value, profile=replace(value.profile, supports_prompt_cache=True)),
    ):
        with pytest.raises(InvalidInput):
            validate_resolved_model(modified)


def test_captured_policy_json_is_bounded_before_parsing(monkeypatch):
    value = captured()
    def reject_parse(*args, **kwargs):
        pytest.fail("Oversized captured policy reached parsing")
    monkeypatch.setattr("app.modules.model.public.json.loads", reject_parse)
    with pytest.raises(InvalidInput, match="byte"):
        validate_resolved_model(replace(value, policy=replace(value.policy, settings_json="x" * 16385)))
