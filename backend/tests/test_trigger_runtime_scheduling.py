"""Scheduled occurrence ownership across evaluator and dispatch."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch
import uuid
from zoneinfo import ZoneInfo

import pytest

from app.models.trigger import AgentTrigger
from app.services.trigger_runtime.dispatch import enqueue_due_trigger
from app.services.trigger_runtime.evaluator import evaluate_trigger
from app.services.trigger_runtime.keys import build_scheduled_execution_key


def _cron_trigger(
    *,
    created_at: datetime,
    last_fired_at: datetime | None = None,
    config: dict | None = None,
) -> AgentTrigger:
    return AgentTrigger(
        id=uuid.uuid4(),
        agent_id=uuid.uuid4(),
        name="daily-check",
        type="cron",
        config=config or {"expr": "0 9 * * *"},
        reason="Daily check",
        is_enabled=True,
        created_at=created_at,
        last_fired_at=last_fired_at,
        fire_count=0,
        cooldown_seconds=60,
    )


@pytest.mark.asyncio
async def test_cron_evaluator_returns_agent_local_occurrence() -> None:
    now = datetime(2026, 8, 5, 1, 0, 8, tzinfo=UTC)
    trigger = _cron_trigger(created_at=now - timedelta(days=2))

    with patch(
        "app.services.timezone_utils.get_agent_timezone",
        new=AsyncMock(return_value="Asia/Shanghai"),
    ):
        scheduled_at = await evaluate_trigger(trigger, now)

    assert scheduled_at == datetime(
        2026,
        8,
        5,
        9,
        0,
        tzinfo=ZoneInfo("Asia/Shanghai"),
    )


@pytest.mark.asyncio
async def test_cron_evaluator_ignores_trigger_timezone_override() -> None:
    now = datetime(2026, 8, 5, 1, 0, 8, tzinfo=UTC)
    trigger = _cron_trigger(
        created_at=now - timedelta(days=2),
        config={"expr": "0 9 * * *", "timezone": "America/New_York"},
    )

    with patch(
        "app.services.timezone_utils.get_agent_timezone",
        new=AsyncMock(return_value="Asia/Shanghai"),
    ):
        scheduled_at = await evaluate_trigger(trigger, now)

    assert scheduled_at is not None
    assert scheduled_at.tzinfo == ZoneInfo("Asia/Shanghai")
    assert scheduled_at.astimezone(UTC) == datetime(2026, 8, 5, 1, 0, tzinfo=UTC)


@pytest.mark.asyncio
async def test_cron_occurrence_does_not_drift_with_last_fired_at() -> None:
    now = datetime(2026, 8, 5, 1, 0, 8, tzinfo=UTC)
    trigger = _cron_trigger(
        created_at=now - timedelta(days=3),
        last_fired_at=datetime(2026, 8, 4, 1, 5, tzinfo=UTC),
    )

    with patch(
        "app.services.timezone_utils.get_agent_timezone",
        new=AsyncMock(return_value="Asia/Shanghai"),
    ):
        scheduled_at = await evaluate_trigger(trigger, now)

    assert scheduled_at is not None
    assert scheduled_at.astimezone(UTC) == datetime(2026, 8, 5, 1, 0, tzinfo=UTC)


@pytest.mark.asyncio
@pytest.mark.parametrize("delay_seconds, expected_due", [(30, True), (31, False)])
async def test_cron_evaluator_applies_thirty_second_grace(
    delay_seconds: int,
    expected_due: bool,
) -> None:
    now = datetime(2026, 8, 5, 1, 0, delay_seconds, tzinfo=UTC)
    trigger = _cron_trigger(created_at=now - timedelta(days=2))

    with patch(
        "app.services.timezone_utils.get_agent_timezone",
        new=AsyncMock(return_value="Asia/Shanghai"),
    ):
        scheduled_at = await evaluate_trigger(trigger, now)

    assert (scheduled_at is not None) is expected_due


@pytest.mark.asyncio
async def test_cron_evaluator_rejects_occurrence_before_trigger_creation() -> None:
    now = datetime(2026, 8, 5, 1, 0, 8, tzinfo=UTC)
    trigger = _cron_trigger(
        created_at=datetime(2026, 8, 5, 1, 0, 5, tzinfo=UTC),
    )

    with patch(
        "app.services.timezone_utils.get_agent_timezone",
        new=AsyncMock(return_value="Asia/Shanghai"),
    ):
        scheduled_at = await evaluate_trigger(trigger, now)

    assert scheduled_at is None


@pytest.mark.asyncio
async def test_cron_evaluator_does_not_fallback_for_invalid_timezone() -> None:
    now = datetime(2026, 8, 5, 1, 0, 8, tzinfo=UTC)
    trigger = _cron_trigger(created_at=now - timedelta(days=2))
    bound_logger = MagicMock()

    with (
        patch(
            "app.services.timezone_utils.get_agent_timezone",
            new=AsyncMock(return_value="Invalid/Timezone"),
        ),
        patch(
            "app.services.trigger_runtime.evaluator.logger.bind",
            return_value=bound_logger,
        ) as bind,
    ):
        scheduled_at = await evaluate_trigger(trigger, now)

    assert scheduled_at is None
    bind.assert_called_once_with(
        trigger_id=str(trigger.id),
        trigger_name=trigger.name,
        trigger_type=trigger.type,
        cron_expr="0 9 * * *",
    )
    bound_logger.warning.assert_called_once()


def test_cron_execution_key_uses_supplied_occurrence() -> None:
    scheduled_at = datetime(
        2026,
        8,
        5,
        9,
        0,
        tzinfo=ZoneInfo("Asia/Shanghai"),
    )
    trigger = _cron_trigger(created_at=scheduled_at - timedelta(days=2))

    key = build_scheduled_execution_key(trigger, scheduled_at)

    assert key == f"cron:{trigger.id}:2026-08-05T01:00:00+00:00"


class _SessionContext:
    async def __aenter__(self):
        return MagicMock()

    async def __aexit__(self, exc_type, exc, traceback):
        return False


@pytest.mark.asyncio
async def test_dispatch_passes_occurrence_to_queue_unchanged() -> None:
    scheduled_at = datetime(
        2026,
        8,
        5,
        9,
        0,
        tzinfo=ZoneInfo("Asia/Shanghai"),
    )
    trigger = _cron_trigger(created_at=scheduled_at - timedelta(days=2))

    with (
        patch(
            "app.services.trigger_runtime.dispatch.query_dao.session",
            return_value=_SessionContext(),
        ),
        patch(
            "app.services.trigger_runtime.dispatch.enqueue_trigger_execution",
            new=AsyncMock(),
        ) as enqueue,
    ):
        await enqueue_due_trigger(trigger, scheduled_at)

    assert enqueue.await_args.kwargs["scheduled_at"] is scheduled_at
    assert enqueue.await_args.kwargs["idempotency_key"] == (
        f"cron:{trigger.id}:2026-08-05T01:00:00+00:00"
    )


@pytest.mark.asyncio
async def test_dispatch_logs_scheduled_occurrence_registration_failure() -> None:
    scheduled_at = datetime(
        2026,
        8,
        5,
        9,
        0,
        tzinfo=ZoneInfo("Asia/Shanghai"),
    )
    trigger = _cron_trigger(created_at=scheduled_at - timedelta(days=2))
    error = RuntimeError("database unavailable")
    bound_logger = MagicMock()

    with (
        patch(
            "app.services.trigger_runtime.dispatch.query_dao.session",
            return_value=_SessionContext(),
        ),
        patch(
            "app.services.trigger_runtime.dispatch.enqueue_trigger_execution",
            new=AsyncMock(side_effect=error),
        ),
        patch(
            "app.services.trigger_runtime.dispatch.logger.bind",
            return_value=bound_logger,
        ) as bind,
        pytest.raises(RuntimeError, match="database unavailable"),
    ):
        await enqueue_due_trigger(trigger, scheduled_at)

    bind.assert_called_once_with(
        trigger_id=str(trigger.id),
        trigger_name=trigger.name,
        trigger_type=trigger.type,
        scheduled_at=scheduled_at.isoformat(),
    )
    bound_logger.error.assert_called_once()
