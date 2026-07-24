"""Task-driven execution loop for HR-created project groups.

Project groups used to be only a sequence of public @mentions.  This module
turns every human project instruction into durable Tasks: a leader first
breaks down and coordinates the work, specialists execute after that task is
complete, and the leader receives every result or failure in the group.
"""

from __future__ import annotations

from collections.abc import Iterable
import re
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent import Agent
from app.models.participant import Participant
from app.models.project import ProjectDecision, ProjectWorkflow, ProjectWorkflowMember
from app.models.task import Task
from app.services.task_executor import enqueue_task_runtime


_DETAIL_LIMIT = 12_000
_USER_DECISION_MARKER = re.compile(r"(?:【|\[)需要用户决策(?:】|\])\s*[:：-]?\s*(.+)", re.S)


def _compact(value: str, limit: int = 260) -> str:
    normalized = " ".join(value.split())
    return normalized if len(normalized) <= limit else normalized[: limit - 1] + "…"


def _dependencies_done(task: Task, tasks_by_id: dict[str, Task]) -> bool:
    ids = task.dependency_task_ids or []
    return bool(ids) and all(
        (dependency := tasks_by_id.get(str(task_id))) is not None
        and dependency.status == "done"
        for task_id in ids
    )


async def is_project_group(db: AsyncSession, group_id: uuid.UUID) -> bool:
    return await db.scalar(
        select(ProjectWorkflow.id).where(
            ProjectWorkflow.group_id == group_id,
            ProjectWorkflow.status == "active",
        )
    ) is not None


async def create_project_task_flow(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    group_id: uuid.UUID,
    session_id: uuid.UUID,
    trigger_message_id: uuid.UUID,
    creator_id: uuid.UUID,
    goal: str,
) -> tuple[Task, object | None] | None:
    """Create one task DAG for a human message and queue its entry task.

    The trigger message ID is the idempotency boundary, so client retries do
    not create a second team workflow or a second leader Run.
    """
    existing = await db.scalar(
        select(Task).where(
            Task.trigger_message_id == trigger_message_id,
            Task.project_workflow_id.is_not(None),
        )
    )
    if existing is not None:
        return existing, None

    workflow = await db.scalar(
        select(ProjectWorkflow).where(
            ProjectWorkflow.tenant_id == tenant_id,
            ProjectWorkflow.group_id == group_id,
            ProjectWorkflow.status == "active",
        )
    )
    if workflow is None or workflow.group_leader_agent_id is None:
        return None

    members = (
        await db.execute(
            select(ProjectWorkflowMember, Agent)
            .join(Agent, Agent.id == ProjectWorkflowMember.agent_id)
            .where(
                ProjectWorkflowMember.workflow_id == workflow.id,
                Agent.deleted_at.is_(None),
            )
        )
    ).all()
    by_agent_id = {agent.id: (member, agent) for member, agent in members}
    leader_pair = by_agent_id.get(workflow.group_leader_agent_id)
    if leader_pair is None:
        return None
    leader_member, leader = leader_pair
    normalized_goal = goal.strip()
    label = _compact(normalized_goal, 180)

    leader_task = Task(
        id=uuid.uuid4(),
        agent_id=leader.id,
        title=f"项目任务拆解与首轮分派：{label}",
        description=(
            f"项目目标：{normalized_goal}\n\n"
            "你是项目总负责人。先依据团队成员职责拆解目标、明确每项可验证交付物、"
            "处理范围和依赖风险；不要用第几天或固定日期作为推进条件。"
            "完成后给出本轮分派依据和关键决策。"
        ),
        type="todo",
        status="pending",
        priority="high",
        created_by=creator_id,
        project_workflow_id=workflow.id,
        group_id=group_id,
        session_id=session_id,
        trigger_message_id=trigger_message_id,
        dependency_task_ids=[],
        report_to_agent_id=None,
    )
    db.add(leader_task)
    await db.flush()

    specialist_tasks: list[Task] = []
    for member, agent in members:
        if member.is_group_leader:
            continue
        specialist = Task(
            id=uuid.uuid4(),
            agent_id=agent.id,
            title=f"{member.role_title}：执行项目工作包",
            description=(
                f"项目目标：{normalized_goal}\n\n"
                f"你的项目职责：{agent.role_description or member.role_title}\n\n"
                "请只完成你的工作包，产出可验证的结论、清单、方案或文件；"
                "说明关键假设、风险和需要项目总负责人决策的事项。"
                "若必须由用户拍板，请单独使用“【需要用户决策】事项、选项和你的建议”标记。"
                "任务完成后，结果会自动向项目总负责人回报。"
            ),
            type="todo",
            status="blocked",
            priority="high",
            created_by=creator_id,
            project_workflow_id=workflow.id,
            group_id=group_id,
            session_id=session_id,
            trigger_message_id=trigger_message_id,
            dependency_task_ids=[str(leader_task.id)],
            report_to_agent_id=leader.id,
        )
        db.add(specialist)
        specialist_tasks.append(specialist)
    await db.flush()

    closure_task = Task(
        id=uuid.uuid4(),
        agent_id=leader.id,
        title="项目总负责人：汇总交付并提交项目回报",
        description=(
            f"项目目标：{normalized_goal}\n\n"
            "汇总各工作包的真实交付、未决风险与下一步建议，向用户提交项目阶段完成回报。"
            "结论必须基于已完成任务的产物，不得用固定日期替代完成条件。"
        ),
        type="todo",
        status="blocked",
        priority="high",
        created_by=creator_id,
        project_workflow_id=workflow.id,
        group_id=group_id,
        session_id=session_id,
        trigger_message_id=trigger_message_id,
        dependency_task_ids=[str(task.id) for task in specialist_tasks] or [str(leader_task.id)],
        report_to_agent_id=None,
        is_project_closure=True,
    )
    db.add(closure_task)
    await db.flush()
    handle = await enqueue_task_runtime(db, task=leader_task, agent=leader)
    return leader_task, handle


async def _project_participants(
    db: AsyncSession,
    agent_ids: Iterable[uuid.UUID],
) -> dict[uuid.UUID, Participant]:
    result = await db.execute(
        select(Participant).where(
            Participant.type == "agent",
            Participant.ref_id.in_(list(agent_ids)),
        )
    )
    return {participant.ref_id: participant for participant in result.scalars().all()}


async def advance_project_task(
    db: AsyncSession,
    *,
    task: Task,
    tenant_id: uuid.UUID,
    succeeded: bool,
    detail: str,
) -> None:
    """Report one terminal project Task and queue all newly-unblocked Tasks."""
    if task.project_workflow_id is None or task.group_id is None or task.session_id is None:
        return
    detail = detail.strip()[:_DETAIL_LIMIT]
    await capture_project_decisions(db, task=task, detail=detail)
    tasks = list(
        (
            await db.execute(
                select(Task).where(Task.project_workflow_id == task.project_workflow_id)
            )
        ).scalars().all()
    )
    tasks_by_id = {str(candidate.id): candidate for candidate in tasks}
    agents = {
        agent.id: agent
        for agent in (
            await db.execute(
                select(Agent).where(Agent.id.in_({candidate.agent_id for candidate in tasks}))
            )
        ).scalars().all()
    }
    queued: list[str] = []
    if succeeded:
        for candidate in tasks:
            if candidate.status != "blocked" or not _dependencies_done(candidate, tasks_by_id):
                continue
            agent = agents.get(candidate.agent_id)
            if agent is None:
                candidate.status = "failed"
                continue
            candidate.status = "pending"
            handle = await enqueue_task_runtime(db, task=candidate, agent=agent)
            queued.append(candidate.title if handle is not None else f"{candidate.title}（运行时未启用）")

    source_agent = agents.get(task.agent_id)
    if source_agent is None:
        return
    participant_map = await _project_participants(
        db,
        [task.agent_id, *( [task.report_to_agent_id] if task.report_to_agent_id else [])],
    )
    sender = participant_map.get(task.agent_id)
    if sender is None:
        return

    if succeeded:
        prefix = "✅ 项目最终回报" if task.is_project_closure else "✅ 任务完成"
        content = f"{prefix}：{task.title}\n\n交付：\n{detail}"
        mention_ids: list[uuid.UUID] = []
        if task.report_to_agent_id is not None:
            leader_participant = participant_map.get(task.report_to_agent_id)
            leader = agents.get(task.report_to_agent_id)
            if leader_participant is not None and leader is not None:
                content += f"\n\n@{leader.name} 请基于该交付继续分派或决策。"
                mention_ids.append(leader_participant.id)
        elif queued:
            content += "\n\n已按任务依赖解锁：\n" + "\n".join(f"- {title}" for title in queued)
    else:
        content = f"❌ 任务失败：{task.title}\n\n原因：\n{detail}\n\n任务已停在失败状态，不会按固定时间自动重跑。"
        mention_ids = []
        if task.report_to_agent_id is not None:
            leader_participant = participant_map.get(task.report_to_agent_id)
            leader = agents.get(task.report_to_agent_id)
            if leader_participant is not None and leader is not None:
                content += f"\n\n@{leader.name} 请决定重试、调整任务或重新分派。"
                mention_ids.append(leader_participant.id)

    from app.services.group_message_service import enqueue_group_message

    await enqueue_group_message(
        db,
        tenant_id=tenant_id,
        group_id=task.group_id,
        session_id=task.session_id,
        sender_participant_id=sender.id,
        content=content,
        mention_participant_ids=mention_ids,
        message_id=uuid.uuid5(task.id, f"project-task-report:{'done' if succeeded else 'failed'}"),
    )


async def capture_project_decisions(
    db: AsyncSession,
    *,
    task: Task,
    detail: str,
) -> list[ProjectDecision]:
    """Turn an explicit task-output marker into a user decision inbox item."""
    if task.project_workflow_id is None or task.group_id is None or task.session_id is None:
        return []
    match = _USER_DECISION_MARKER.search(detail)
    if match is None:
        return []
    context = match.group(1).strip()[:_DETAIL_LIMIT]
    if not context:
        return []
    # Terminal task handling is receipt-idempotent; this guard also prevents a
    # repaired output from generating the same decision twice.
    existing = await db.scalar(
        select(ProjectDecision.id).where(
            ProjectDecision.task_id == task.id,
            ProjectDecision.context == context,
        )
    )
    if existing is not None:
        return []
    decision = ProjectDecision(
        id=uuid.uuid4(),
        workflow_id=task.project_workflow_id,
        group_id=task.group_id,
        session_id=task.session_id,
        task_id=task.id,
        requesting_agent_id=task.agent_id,
        title=_compact(context.splitlines()[0], 300),
        context=context,
        status="pending",
    )
    db.add(decision)
    return [decision]


__all__ = [
    "advance_project_task",
    "capture_project_decisions",
    "create_project_task_flow",
    "is_project_group",
]
