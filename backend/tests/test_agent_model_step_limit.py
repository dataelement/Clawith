"""Agent model-step limits remain bounded while permitting long Runs."""

import pytest
from pydantic import ValidationError

from app.schemas.schemas import AgentUpdate


@pytest.mark.parametrize("value", [5, 50, 200, 300, 500])
def test_agent_update_accepts_supported_model_step_limits(value: int) -> None:
    assert AgentUpdate(max_tool_rounds=value).max_tool_rounds == value


@pytest.mark.parametrize("value", [0, 4, 501, 10_000])
def test_agent_update_rejects_unsafe_model_step_limits(value: int) -> None:
    with pytest.raises(ValidationError):
        AgentUpdate(max_tool_rounds=value)
