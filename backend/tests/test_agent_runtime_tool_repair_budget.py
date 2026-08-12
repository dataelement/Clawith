"""Pure Tool repair episode transition contracts."""

from app.services.agent_runtime.tool_repair_budget import (
    SAME_FINGERPRINT_FAILURE_LIMIT,
    TOOL_EPISODE_FAILURE_LIMIT,
    apply_tool_result,
    reset_tool_repair_episodes,
)


def _failure(
    *,
    tool_name: str = "read_file",
    content: str = "$.path is required.",
) -> dict:
    return {
        "role": "tool",
        "tool_call_id": "call-1",
        "name": tool_name,
        "content": content,
        "execution_status": "failed",
        "error_code": "tool_arguments_invalid",
        "model_action": "repair_arguments",
        "side_effect_state": "none",
    }


def _episode(state: dict, tool_name: str = "read_file") -> dict:
    return state["by_tool"][tool_name]


def test_tenth_consecutive_fingerprint_pauses_without_off_by_one() -> None:
    state: dict = {}
    transition = None
    for model_step in range(1, SAME_FINGERPRINT_FAILURE_LIMIT + 1):
        transition = apply_tool_result(
            state,
            _failure(),
            model_step=model_step,
        )
        state = transition.episodes
        assert transition.pause_reason is (
            None
            if model_step < SAME_FINGERPRINT_FAILURE_LIMIT
            else "tool_repair_same_fingerprint_limit_reached"
        )

    assert transition is not None
    assert _episode(state)["same_fingerprint_failures"] == 10
    assert _episode(state)["total_failures"] == 10


def test_tenth_tool_failure_pauses_even_when_fingerprint_changes() -> None:
    state: dict = {}
    transition = None
    for model_step in range(1, TOOL_EPISODE_FAILURE_LIMIT + 1):
        transition = apply_tool_result(
            state,
            _failure(content=f"problem-{model_step}"),
            model_step=model_step,
        )
        state = transition.episodes

    assert transition is not None
    assert transition.pause_reason == "tool_repair_episode_limit_reached"
    assert _episode(state)["total_failures"] == 10
    assert _episode(state)["same_fingerprint_failures"] == 1


def test_fingerprint_change_only_resets_consecutive_counter() -> None:
    first = apply_tool_result({}, _failure(content="first"), model_step=1)
    second = apply_tool_result(
        first.episodes,
        _failure(content="second"),
        model_step=2,
    )

    assert _episode(second.episodes)["total_failures"] == 2
    assert _episode(second.episodes)["same_fingerprint_failures"] == 1


def test_same_tool_success_and_explicit_user_correction_reset_episode() -> None:
    failed = apply_tool_result({}, _failure(), model_step=1)
    unrelated_success = apply_tool_result(
        failed.episodes,
        {
            "role": "tool",
            "tool_call_id": "call-2",
            "name": "list_files",
            "execution_status": "succeeded",
        },
        model_step=2,
    )
    assert "read_file" in unrelated_success.episodes["by_tool"]

    same_tool_success = apply_tool_result(
        unrelated_success.episodes,
        {
            "role": "tool",
            "tool_call_id": "call-3",
            "name": "read_file",
            "execution_status": "succeeded",
        },
        model_step=3,
    )
    assert "read_file" not in same_tool_success.episodes["by_tool"]

    failed_again = apply_tool_result(
        same_tool_success.episodes,
        _failure(),
        model_step=4,
    )
    assert reset_tool_repair_episodes(failed_again.episodes) == {
        "version": 1,
        "by_tool": {},
    }


def test_retry_wait_pending_cancel_unknown_and_nonrepairable_failures_do_not_count() -> None:
    excluded = (
        {**_failure(), "execution_status": "pending", "model_action": "wait"},
        {
            **_failure(),
            "execution_status": "unknown",
            "model_action": "reconcile",
            "side_effect_state": "unknown",
        },
        {**_failure(), "model_action": "ask_user"},
        {**_failure(), "side_effect_state": "possible"},
    )
    state: dict = {}
    for model_step, message in enumerate(excluded, start=1):
        transition = apply_tool_result(state, message, model_step=model_step)
        state = transition.episodes
        assert transition.counted is False
        assert transition.pause_reason is None

    assert state == {"version": 1, "by_tool": {}}
