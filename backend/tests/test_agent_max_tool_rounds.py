from app.api.agents import _max_tool_rounds_from_create
from app.schemas.schemas import AgentCreate
from app.services.scheduler import _normalize_max_tool_rounds as normalize_schedule_rounds
from app.services.task_executor import _normalize_max_tool_rounds as normalize_task_rounds


def test_agent_create_accepts_explicit_max_tool_rounds() -> None:
    data = AgentCreate(name="WebArena Agent", max_tool_rounds=20)

    assert _max_tool_rounds_from_create(data) == 20


def test_agent_create_maps_autonomy_max_rounds_to_max_tool_rounds() -> None:
    data = AgentCreate(
        name="WebArena Agent",
        autonomy_policy={"mode": "webarena-agentbay-eval", "max_rounds": 20},
    )

    assert _max_tool_rounds_from_create(data) == 20


def test_agent_create_explicit_max_tool_rounds_wins_over_autonomy_policy() -> None:
    data = AgentCreate(
        name="WebArena Agent",
        max_tool_rounds=12,
        autonomy_policy={"mode": "webarena-agentbay-eval", "max_rounds": 20},
    )

    assert _max_tool_rounds_from_create(data) == 12


def test_background_round_normalizers_preserve_twenty() -> None:
    assert normalize_task_rounds(20) == 20
    assert normalize_schedule_rounds(20) == 20
