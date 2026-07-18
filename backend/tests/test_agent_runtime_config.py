"""Pure tests for Agent Runtime settings and rollout precedence."""

import uuid

from pydantic import ValidationError
import pytest

from app.config import Settings
from app.services.agent_runtime.config import (
    RuntimeConfigurationError,
    RuntimeRolloutPolicy,
    decide_runtime_v2,
)


def _settings(**overrides: object) -> Settings:
    return Settings(_env_file=None, **overrides)


def test_runtime_settings_have_safe_confirmed_defaults() -> None:
    settings = _settings()

    assert settings.AGENT_RUNTIME_V2_ENABLED is True
    assert settings.AGENT_RUNTIME_V2_AGENT_IDS == ""
    assert settings.AGENT_RUNTIME_V2_SOURCE_TYPES == "task"
    assert settings.AGENT_RUNTIME_GRAPH_NAME == "clawith_agent_runtime"
    assert settings.AGENT_RUNTIME_GRAPH_VERSION == "v1"
    assert settings.LANGGRAPH_CHECKPOINT_DATABASE_URL is None
    assert settings.LANGGRAPH_AES_KEY is None
    assert settings.AGENT_RUNTIME_COMMAND_CLAIM_TTL_SECONDS == 60
    assert settings.AGENT_RUNTIME_COMMAND_CLAIM_RENEW_SECONDS == 20
    assert settings.AGENT_RUNTIME_COMMAND_MAX_ATTEMPTS == 5
    assert settings.AGENT_RUNTIME_SUMMARY_THRESHOLD_RATIO == 0.85
    assert settings.AGENT_RUNTIME_SESSION_RECENT_MESSAGES == 20
    assert settings.AGENT_RUNTIME_SESSION_COMPACT_MESSAGE_THRESHOLD is None
    assert settings.AGENT_RUNTIME_RUN_COMPACT_MESSAGE_THRESHOLD is None
    assert settings.AGENT_RUNTIME_RUN_COMPACT_TOOL_RESULT_BYTES is None
    assert settings.AGENT_RUNTIME_VERIFY_REPAIR_COMPACT_ROUNDS is None
    assert settings.AGENT_RUNTIME_MODEL_CAPABILITY_REFRESH_SECONDS == 86400
    assert settings.AGENT_RUNTIME_FALLBACK_CONTEXT_WINDOW_TOKENS == 131072
    assert settings.MULTI_AGENT_COMPACT_MODEL_ID is None
    assert settings.MULTI_AGENT_PLANNING_MODEL_ID is None
    assert settings.AGENT_RUNTIME_CHECKPOINT_RETENTION_DAYS == 30
    assert settings.AGENT_RUNTIME_EVENT_PAYLOAD_MAX_BYTES == 16384
    assert settings.AGENT_RUNTIME_TOOL_RESULT_INLINE_MAX_BYTES == 8192
    assert settings.AGENT_RUNTIME_ASYNC_TOOL_POLL_SCAN_SECONDS == 0.25
    assert settings.MAX_AGENT_CYCLE_COUNT == 5


def test_blank_optional_runtime_environment_values_are_usable() -> None:
    settings = _settings(
        LANGGRAPH_CHECKPOINT_DATABASE_URL="  ",
        LANGGRAPH_AES_KEY="",
        MULTI_AGENT_COMPACT_MODEL_ID="",
        MULTI_AGENT_PLANNING_MODEL_ID=" ",
        AGENT_RUNTIME_SESSION_COMPACT_MESSAGE_THRESHOLD="",
        AGENT_RUNTIME_RUN_COMPACT_MESSAGE_THRESHOLD=" ",
        AGENT_RUNTIME_RUN_COMPACT_TOOL_RESULT_BYTES="",
        AGENT_RUNTIME_VERIFY_REPAIR_COMPACT_ROUNDS=" ",
    )

    assert settings.LANGGRAPH_CHECKPOINT_DATABASE_URL is None
    assert settings.LANGGRAPH_AES_KEY is None
    assert settings.MULTI_AGENT_COMPACT_MODEL_ID is None
    assert settings.MULTI_AGENT_PLANNING_MODEL_ID is None
    assert settings.AGENT_RUNTIME_SESSION_COMPACT_MESSAGE_THRESHOLD is None
    assert settings.AGENT_RUNTIME_RUN_COMPACT_MESSAGE_THRESHOLD is None
    assert settings.AGENT_RUNTIME_RUN_COMPACT_TOOL_RESULT_BYTES is None
    assert settings.AGENT_RUNTIME_VERIFY_REPAIR_COMPACT_ROUNDS is None


def test_runtime_graph_identifiers_are_trimmed() -> None:
    settings = _settings(
        AGENT_RUNTIME_GRAPH_NAME=" runtime_graph ",
        AGENT_RUNTIME_GRAPH_VERSION=" v2 ",
    )

    assert settings.AGENT_RUNTIME_GRAPH_NAME == "runtime_graph"
    assert settings.AGENT_RUNTIME_GRAPH_VERSION == "v2"


@pytest.mark.parametrize(
    "overrides",
    [
        {"AGENT_RUNTIME_COMMAND_CLAIM_TTL_SECONDS": 0},
        {
            "AGENT_RUNTIME_COMMAND_CLAIM_TTL_SECONDS": 20,
            "AGENT_RUNTIME_COMMAND_CLAIM_RENEW_SECONDS": 20,
        },
        {"AGENT_RUNTIME_SUMMARY_THRESHOLD_RATIO": 1.1},
        {"AGENT_RUNTIME_FALLBACK_CONTEXT_WINDOW_TOKENS": 0},
        {"AGENT_RUNTIME_EVENT_PAYLOAD_MAX_BYTES": -1},
        {"AGENT_RUNTIME_GRAPH_NAME": " "},
    ],
)
def test_invalid_runtime_settings_fail_validation(overrides: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        _settings(**overrides)


def test_rollout_policy_parses_uuid_allowlist_and_source_types() -> None:
    first_agent = uuid.uuid4()
    second_agent = uuid.uuid4()
    settings = _settings(
        AGENT_RUNTIME_V2_AGENT_IDS=f" {first_agent}, {second_agent}, {first_agent} ",
        AGENT_RUNTIME_V2_SOURCE_TYPES=" TASK, chat ",
    )

    policy = RuntimeRolloutPolicy.from_settings(settings)

    assert policy.agent_ids == frozenset({first_agent, second_agent})
    assert policy.source_types == frozenset({"task", "chat"})


@pytest.mark.parametrize(
    ("field_name", "value", "message"),
    [
        ("AGENT_RUNTIME_V2_AGENT_IDS", "not-a-uuid", "invalid UUID"),
        ("AGENT_RUNTIME_V2_AGENT_IDS", f"{uuid.uuid4()},", "empty comma-separated"),
        ("AGENT_RUNTIME_V2_SOURCE_TYPES", "task,other", "unsupported values"),
    ],
)
def test_invalid_rollout_lists_fail_closed(field_name: str, value: str, message: str) -> None:
    settings = _settings(**{field_name: value})

    with pytest.raises(RuntimeConfigurationError, match=message):
        RuntimeRolloutPolicy.from_settings(settings)


def test_new_run_gate_uses_allowlist_then_source_then_global_flag() -> None:
    allowlisted_agent = uuid.uuid4()
    other_agent = uuid.uuid4()
    policy = RuntimeRolloutPolicy.from_settings(
        _settings(
            AGENT_RUNTIME_V2_ENABLED=True,
            AGENT_RUNTIME_V2_AGENT_IDS=str(allowlisted_agent),
            AGENT_RUNTIME_V2_SOURCE_TYPES="task",
        )
    )

    allowlist_decision = policy.decide(
        agent_id=allowlisted_agent,
        source_type="chat",
    )
    source_decision = policy.decide(
        agent_id=other_agent,
        source_type="task",
    )
    global_decision = policy.decide(
        agent_id=other_agent,
        source_type="trigger",
    )

    assert (allowlist_decision.use_v2, allowlist_decision.reason) == (
        True,
        "agent_allowlist",
    )
    assert (source_decision.use_v2, source_decision.reason) == (True, "source_type")
    assert (global_decision.use_v2, global_decision.reason) == (True, "global_flag")


def test_new_run_gate_can_remain_on_legacy_when_every_gate_is_off() -> None:
    decision = decide_runtime_v2(
        agent_id=uuid.uuid4(),
        source_type="chat",
        settings=_settings(
            AGENT_RUNTIME_V2_ENABLED=False,
            AGENT_RUNTIME_V2_AGENT_IDS="",
            AGENT_RUNTIME_V2_SOURCE_TYPES="",
        ),
    )

    assert (decision.use_v2, decision.reason) == (False, "global_flag")


def test_existing_langgraph_run_always_resumes_v2() -> None:
    policy = RuntimeRolloutPolicy.from_settings(
        _settings(
            AGENT_RUNTIME_V2_ENABLED=False,
            AGENT_RUNTIME_V2_AGENT_IDS="",
            AGENT_RUNTIME_V2_SOURCE_TYPES="",
        )
    )

    decision = policy.decide(
        agent_id=None,
        source_type="no-longer-routed",
        existing_runtime_type="langgraph",
    )

    assert (decision.use_v2, decision.reason) == (
        True,
        "existing_langgraph_run",
    )


def test_existing_legacy_run_never_switches_mid_execution() -> None:
    decision = decide_runtime_v2(
        agent_id=uuid.uuid4(),
        source_type="task",
        existing_runtime_type="legacy",
        settings=_settings(AGENT_RUNTIME_V2_ENABLED=True),
    )

    assert (decision.use_v2, decision.reason) == (False, "existing_legacy_run")


@pytest.mark.parametrize(
    ("source_type", "existing_runtime_type"),
    [("unknown", None), ("task", "unknown")],
)
def test_invalid_gate_inputs_fail_closed(
    source_type: str,
    existing_runtime_type: str | None,
) -> None:
    policy = RuntimeRolloutPolicy(False, frozenset(), frozenset())

    with pytest.raises(RuntimeConfigurationError):
        policy.decide(
            agent_id=None,
            source_type=source_type,
            existing_runtime_type=existing_runtime_type,
        )
