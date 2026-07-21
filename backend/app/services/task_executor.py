"""Durable Runtime intake for todo and supervision Task executions."""

import uuid

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.database import async_session
from app.models.agent import Agent
from app.models.task import Task, TaskLog
from app.services.agent_runtime.adapter import RuntimeCommandIntake
from app.services.agent_runtime.config import decide_runtime_v2
from app.services.agent_runtime.contracts import RunHandle, StartRunCommand

settings = get_settings()


class TaskRuntimeIntakeError(RuntimeError):
    """A Task selected for Runtime v2 cannot be registered safely."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _task_goal(task: Task) -> str:
    if task.type == "supervision":
        goal = f"[督办任务] {task.title}"
    else:
        goal = f"[任务执行] {task.title}"
    if task.description:
        goal += f"\n任务描述: {task.description}"
    if task.type == "supervision":
        if task.supervision_target_name:
            goal += f"\n督办对象: {task.supervision_target_name}"
        return goal + "\n\n请执行此督办任务：联系督办对象，了解进展，并汇报结果。"
    return goal + "\n\n请认真完成此任务，给出详细的执行结果。"


async def enqueue_task_runtime(
    db: AsyncSession,
    *,
    task: Task,
    agent: Agent,
    execution_id: uuid.UUID | None = None,
    settings_override: Settings | None = None,
) -> RunHandle | None:
    """Register one Task execution in the caller transaction when v2 is selected."""
    runtime_settings = settings_override or settings
    if task.type not in {"todo", "supervision"}:
        raise TaskRuntimeIntakeError(
            "task_type_unsupported",
            f"Runtime does not support Task type {task.type!r}",
        )
    decision = decide_runtime_v2(
        agent_id=agent.id,
        source_type="task",
        settings=runtime_settings,
    )
    if not decision.use_v2:
        return None
    if task.agent_id != agent.id:
        raise TaskRuntimeIntakeError(
            "task_agent_mismatch",
            "Task does not belong to the requested Agent",
        )
    if agent.tenant_id is None:
        raise TaskRuntimeIntakeError(
            "agent_tenant_missing",
            "Runtime Task Agent has no tenant",
        )
    model_id = agent.primary_model_id
    if model_id is None:
        raise TaskRuntimeIntakeError(
            "agent_model_missing",
            "Runtime Task Agent has no configured primary model",
        )

    if task.type == "supervision":
        occurrence_id = execution_id or uuid.uuid4()
        source_execution_id = f"task:{task.id}:supervision:{occurrence_id}"
    else:
        source_execution_id = f"task:{task.id}"

    handle = await RuntimeCommandIntake(
        db,
        settings=runtime_settings,
    ).start_run(
        StartRunCommand(
            tenant_id=agent.tenant_id,
            agent_id=agent.id,
            source_type="task",
            source_id=str(task.id),
            source_execution_id=source_execution_id,
            goal=_task_goal(task),
            run_kind="background",
            model_id=model_id,
            delivery_status="not_required",
            idempotency_key=f"start:{source_execution_id}",
            payload={
                "task_id": str(task.id),
                "task_type": task.type,
                "title": task.title,
                "description": task.description,
            },
            origin_user_id=task.created_by,
            actor_user_id=task.created_by,
        )
    )
    task.status = "doing"
    if handle.created:
        db.add(
            TaskLog(
                task_id=task.id,
                content=f"🤖 已进入持久化执行队列（Run {handle.run_id}）",
            )
        )
    return handle


async def _try_enqueue_runtime_task(
    task_id: uuid.UUID,
    agent_id: uuid.UUID,
    *,
    execution_id: uuid.UUID,
) -> RunHandle | None:
    async with async_session() as db:
        async with db.begin():
            task_result = await db.execute(
                select(Task).where(
                    Task.id == task_id,
                    Task.agent_id == agent_id,
                )
            )
            task = task_result.scalar_one_or_none()
            if task is None:
                raise TaskRuntimeIntakeError(
                    "task_not_found",
                    "Task does not exist for the requested Agent",
                )
            agent_result = await db.execute(select(Agent).where(Agent.id == agent_id))
            agent = agent_result.scalar_one_or_none()
            if agent is None:
                raise TaskRuntimeIntakeError(
                    "agent_not_found",
                    "Task Agent does not exist",
                )
            return await enqueue_task_runtime(
                db,
                task=task,
                agent=agent,
                execution_id=execution_id,
            )


async def execute_task(task_id: uuid.UUID, agent_id: uuid.UUID) -> None:
    """Register one Task execution; the Runtime worker owns all model/tool work."""
    logger.info(f"[TaskExec] Starting task {task_id} for agent {agent_id}")

    try:
        runtime_handle = await _try_enqueue_runtime_task(
            task_id,
            agent_id,
            execution_id=uuid.uuid4(),
        )
    except TaskRuntimeIntakeError as exc:
        if exc.code == "task_not_found":
            logger.warning(f"[TaskExec] Task {task_id} not found")
            return
        logger.error(f"[TaskExec] Runtime intake failed ({exc.code}): {exc}")
        await _log_error(task_id, f"持久化执行登记失败: {exc.code}")
        return
    except Exception as exc:
        error_code = getattr(exc, "code", type(exc).__name__)
        logger.error(f"[TaskExec] Runtime intake failed ({error_code}): {exc}")
        await _log_error(task_id, f"持久化执行登记失败: {error_code}")
        return
    if runtime_handle is not None:
        logger.info(
            f"[TaskExec] Task {task_id} queued as Runtime Run {runtime_handle.run_id}"
        )
        return
    await _log_error(
        task_id,
        "统一 Runtime 当前未对 task 入口启用；未回退旧执行循环",
    )


async def _log_error(task_id: uuid.UUID, message: str) -> None:
    """Add an error log to the task."""
    logger.error(f"[TaskExec] Error for {task_id}: {message}")
    async with async_session() as db:
        db.add(TaskLog(task_id=task_id, content=f"❌ {message}"))
        await db.commit()
