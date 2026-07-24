"""Project workflow API: plan a team, then provision a leader-led project group."""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import get_current_user
from app.database import get_db
from app.models.agent import Agent, AgentPermission
from app.models.chat_session import ChatSession
from app.models.group import GroupMember
from app.models.org import AgentAgentRelationship
from app.models.participant import Participant
from app.models.project import ProjectDecision, ProjectWorkflow, ProjectWorkflowMember
from app.models.task import Task
from app.models.tenant import Tenant
from app.models.user import User
from app.services import group_chat_service
from app.services import group_message_service
from app.services.group_chat_service import GroupChatServiceError
from app.services.group_message_service import GroupMessageServiceError
from app.services.participant_identity import get_or_create_user_participant
from app.services.project_team_builder import (
    HRPlanningError,
    build_team_wakeup_message,
    plan_team_with_hr,
    validate_team_plan,
)
from app.services.access_relationships import ensure_access_granted_platform_relationships
from app.services.agent_manager import agent_manager
from app.services.llm.model_resolution import load_active_model
from app.services.llm.utils import LLMMessage, create_llm_client, get_model_api_key


router = APIRouter(prefix="/api/projects", tags=["projects"])


class ProjectPlanIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    requirements: str = Field(min_length=1, max_length=20_000)


class TeamRoleIn(BaseModel):
    key: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=100)
    role_description: str = Field(min_length=1, max_length=500)
    personality: str = Field(default="", max_length=2_000)
    boundaries: str = Field(default="", max_length=2_000)
    is_group_leader: bool = False


class CreateProjectIn(ProjectPlanIn):
    team_plan: dict


class TeamPlanOut(BaseModel):
    planner_name: str
    project_name: str
    requirements: str
    roles: list[TeamRoleIn]
    wake_up_message: str


class ProjectMemberOut(BaseModel):
    agent_id: uuid.UUID
    role_key: str
    role_title: str
    is_group_leader: bool


class ProjectOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    template_key: str
    requirements: str
    status: str
    team_plan: dict
    group_id: uuid.UUID | None
    group_leader_agent_id: uuid.UUID | None
    failure_reason: str | None
    created_at: datetime
    members: list[ProjectMemberOut] = Field(default_factory=list)


class ProjectTaskOut(BaseModel):
    id: uuid.UUID
    agent_id: uuid.UUID
    agent_name: str
    title: str
    description: str | None
    status: str
    priority: str
    dependency_task_ids: list[str]
    report_to_agent_id: uuid.UUID | None
    is_project_closure: bool
    completed_at: datetime | None
    updated_at: datetime | None


class ProjectDecisionReplyIn(BaseModel):
    response: str = Field(min_length=1, max_length=12_000)
    intent: Literal["decision", "modification"] = "decision"


class ProjectDecisionDraftIn(BaseModel):
    """Optional user preference to incorporate when drafting a decision reply."""

    instruction: str = Field(default="", max_length=12_000)


class ProjectDecisionDraftOut(BaseModel):
    draft: str


class ProjectDecisionOut(BaseModel):
    id: uuid.UUID
    task_id: uuid.UUID | None
    requesting_agent_id: uuid.UUID | None
    requesting_agent_name: str | None
    title: str
    context: str
    status: str
    response: str | None
    created_at: datetime
    responded_at: datetime | None


def _tenant_id(user: User) -> uuid.UUID:
    if user.tenant_id is None:
        raise HTTPException(status_code=403, detail="A tenant is required for project workflows")
    return user.tenant_id


async def _project_out(db: AsyncSession, workflow: ProjectWorkflow) -> ProjectOut:
    result = await db.execute(
        select(ProjectWorkflowMember).where(ProjectWorkflowMember.workflow_id == workflow.id)
    )
    members = [
        ProjectMemberOut(
            agent_id=member.agent_id,
            role_key=member.role_key,
            role_title=member.role_title,
            is_group_leader=member.is_group_leader,
        )
        for member in result.scalars().all()
    ]
    return ProjectOut(
        id=workflow.id,
        name=workflow.name,
        template_key=workflow.template_key,
        requirements=workflow.requirements,
        status=workflow.status,
        team_plan=workflow.team_plan,
        group_id=workflow.group_id,
        group_leader_agent_id=workflow.group_leader_agent_id,
        failure_reason=workflow.failure_reason,
        created_at=workflow.created_at,
        members=members,
    )


class ProjectProvisioningError(RuntimeError):
    """A project team was not ready to receive work."""


async def _project_default_model_id(
    db: AsyncSession,
    *,
    tenant: Tenant | None,
    tenant_id: uuid.UUID,
) -> uuid.UUID | None:
    """Return the tenant default only when it is usable by project Agents."""
    configured_model_id = tenant.default_model_id if tenant is not None else None
    model = await load_active_model(
        db,
        model_id=configured_model_id,
        tenant_id=tenant_id,
    )
    return model.id if model is not None else None


async def _provision_project_agents(
    db: AsyncSession,
    *,
    agents: list[tuple[dict, Agent, Participant]],
    creator_id: uuid.UUID,
    tenant_id: uuid.UUID,
    default_model_id: uuid.UUID | None,
) -> None:
    """Make every member executable before exposing the project group.

    Project groups are an all-or-nothing collaboration surface: publishing a
    group while one member is still ``creating`` makes the roster look valid
    but makes A2A dispatch fail.  This intentionally performs the same file
    and runtime bootstrap as custom Agent creation synchronously.
    """
    for role, agent, _ in agents:
        active_model = await load_active_model(
            db,
            model_id=agent.primary_model_id,
            tenant_id=tenant_id,
        )
        if active_model is None:
            if default_model_id is None:
                raise ProjectProvisioningError(
                    "项目团队缺少可用主模型。请先在企业模型池启用并设置默认模型，再创建或修复项目。"
                )
            agent.primary_model_id = default_model_id

        await ensure_access_granted_platform_relationships(
            db,
            agent,
            created_by_user_id=creator_id,
        )
        if agent.status not in {"running", "idle"}:
            await agent_manager.initialize_agent_files(
                db,
                agent,
                personality=role["personality"],
                boundaries=role["boundaries"],
            )
            # Native project members execute through the platform's durable
            # Runtime; an optional OpenClaw sidecar is not a readiness
            # prerequisite.  Requiring an image pull here made a transient
            # Docker registry failure leave every team member in ``creating``.
            if agent.agent_type == "native":
                agent.status = "idle"
                agent.last_active_at = datetime.now(UTC)
            else:
                await agent_manager.start_container(db, agent)
        if agent.status not in {"running", "idle"}:
            raise ProjectProvisioningError(
                f"成员“{agent.name}”未能完成初始化（状态：{agent.status}）。"
            )
    await db.flush()


async def _ensure_team_directory_contacts(
    db: AsyncSession,
    *,
    agents: list[tuple[dict, Agent, Participant]],
    created_by_user_id: uuid.UUID,
) -> None:
    """Make every project teammate a mutual, contactable Directory entry."""
    agent_ids = [agent.id for _, agent, _ in agents]
    existing_result = await db.execute(
        select(AgentAgentRelationship.agent_id, AgentAgentRelationship.target_agent_id).where(
            AgentAgentRelationship.agent_id.in_(agent_ids),
            AgentAgentRelationship.target_agent_id.in_(agent_ids),
        )
    )
    existing = set(existing_result.all())
    for _, source, _ in agents:
        for _, target, _ in agents:
            if source.id == target.id or (source.id, target.id) in existing:
                continue
            db.add(
                AgentAgentRelationship(
                    id=uuid.uuid4(),
                    agent_id=source.id,
                    target_agent_id=target.id,
                    relation="project_teammate",
                    description="Auto-added because both Agents belong to the same project group.",
                    created_by_user_id=created_by_user_id,
                    updated_by_user_id=created_by_user_id,
                )
            )
    await db.flush()


@router.post("/team-plans", response_model=TeamPlanOut)
async def create_team_plan(
    body: ProjectPlanIn,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    try:
        return await plan_team_with_hr(
            db,
            tenant_id=_tenant_id(current_user),
            creator_id=current_user.id,
            name=body.name,
            requirements=body.requirements,
        )
    except (ValueError, HRPlanningError) as exc:
        # Keep failed HR attempts in the immutable operations ledger even
        # though this route returns a 422 and the normal request transaction
        # would otherwise roll back.
        await db.commit()
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("", response_model=ProjectOut, status_code=status.HTTP_201_CREATED)
async def create_project(
    body: CreateProjectIn,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ProjectOut:
    """Create all agents first; only then create their leader-led project group."""
    tenant_id = _tenant_id(current_user)
    if not current_user.is_active:
        raise HTTPException(status_code=403, detail="Current user is not active")
    try:
        roles = validate_team_plan(body.team_plan)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    tenant = await db.get(Tenant, tenant_id)
    default_model_id = await _project_default_model_id(
        db,
        tenant=tenant,
        tenant_id=tenant_id,
    )
    if default_model_id is None:
        raise HTTPException(
            status_code=422,
            detail="项目团队无法创建：请先在企业模型池启用并设置一个默认模型。",
        )
    human_participant = await get_or_create_user_participant(
        db,
        current_user.id,
        current_user.display_name,
        current_user.avatar_url,
    )
    now = datetime.now(UTC)
    workflow = ProjectWorkflow(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        creator_id=current_user.id,
        name=body.name.strip(),
        template_key="hr_generated",
        requirements=body.requirements.strip(),
        status="provisioning",
        team_plan={**body.team_plan, "roles": roles},
        created_at=now,
        updated_at=now,
    )
    db.add(workflow)
    agents: list[tuple[dict, Agent, Participant]] = []
    for role in roles:
        agent = Agent(
            id=uuid.uuid4(),
            name=role["name"],
            role_description=role["role_description"],
            bio=f"{body.name.strip()} 项目团队成员：{role['role_description']}",
            creator_id=current_user.id,
            tenant_id=tenant_id,
            agent_type="native",
            status="creating",
            primary_model_id=default_model_id,
            access_mode="company",
            company_access_level="use",
            max_llm_calls_per_day=(tenant.default_max_llm_calls_per_day or 1000) if tenant else 1000,
            max_triggers=(tenant.default_max_triggers or 20) if tenant else 20,
            min_poll_interval_min=(tenant.min_poll_interval_floor or 5) if tenant else 5,
            webhook_rate_limit=(tenant.max_webhook_rate_ceiling or 5) if tenant else 5,
            heartbeat_interval_minutes=max(240, tenant.min_heartbeat_interval_minutes or 0) if tenant else 240,
        )
        participant = Participant(
            id=uuid.uuid4(), type="agent", ref_id=agent.id, display_name=agent.name, avatar_url=None
        )
        db.add_all((agent, participant, AgentPermission(agent_id=agent.id, scope_type="company", access_level="use")))
        db.add(
            ProjectWorkflowMember(
                id=uuid.uuid4(), workflow_id=workflow.id, agent_id=agent.id,
                role_key=role["key"], role_title=role["name"], is_group_leader=role["is_group_leader"],
            )
        )
        agents.append((role, agent, participant))
    await db.flush()

    # Do not expose the group or enqueue the wake-up message until every
    # member has a workspace, a usable primary model and a ready runtime.
    try:
        await _provision_project_agents(
            db,
            agents=agents,
            creator_id=current_user.id,
            tenant_id=tenant_id,
            default_model_id=default_model_id,
        )
    except ProjectProvisioningError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    await _ensure_team_directory_contacts(
        db,
        agents=agents,
        created_by_user_id=current_user.id,
    )

    _, leader_agent, leader_participant = next(
        item for item in agents if item[0]["is_group_leader"]
    )
    try:
        group = await group_chat_service.create_group(
            db,
            tenant_id=tenant_id,
            creator_participant_id=human_participant.id,
            name=f"{workflow.name} · 项目群",
            description=f"由 {leader_agent.name} 负责的项目群。向群主说明需求，群主负责分派并汇报。",
            member_participant_ids=[participant.id for _, _, participant in agents],
        )
    except GroupChatServiceError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    group.owner_agent_id = leader_agent.id
    owner_membership = await db.scalar(
        select(GroupMember).where(
            GroupMember.group_id == group.id,
            GroupMember.participant_id == leader_participant.id,
            GroupMember.removed_at.is_(None),
        )
    )
    if owner_membership is None:
        raise HTTPException(status_code=500, detail="Group leader membership was not created")
    owner_membership.role = "owner"
    session = await group_chat_service.create_group_session(
        db,
        tenant_id=tenant_id,
        group_id=group.id,
        actor_participant_id=human_participant.id,
        title="项目协作",
    )
    try:
        await group_message_service.enqueue_group_message(
            db,
            tenant_id=tenant_id,
            group_id=group.id,
            session_id=session.id,
            sender_participant_id=human_participant.id,
            content=build_team_wakeup_message({
                "project_name": workflow.name,
                "requirements": workflow.requirements,
                "roles": roles,
            }),
            mention_participant_ids=[leader_participant.id],
            message_id=uuid.uuid4(),
        )
    except GroupMessageServiceError as exc:
        raise HTTPException(status_code=422, detail=f"Project kickoff could not be created: {exc}") from exc
    workflow.group_id = group.id
    workflow.group_leader_agent_id = leader_agent.id
    workflow.status = "active"
    workflow.updated_at = datetime.now(UTC)
    await db.flush()

    result = await _project_out(db, workflow)
    # Make the session visible to consumers in the returned transaction, while
    # retaining a deliberate local binding as a regression guard for creation order.
    assert session.group_id == group.id
    return result


@router.get("", response_model=list[ProjectOut])
async def list_projects(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[ProjectOut]:
    tenant_id = _tenant_id(current_user)
    result = await db.execute(
        select(ProjectWorkflow)
        .where(ProjectWorkflow.tenant_id == tenant_id, ProjectWorkflow.creator_id == current_user.id)
        .order_by(ProjectWorkflow.created_at.desc())
    )
    return [await _project_out(db, workflow) for workflow in result.scalars().all()]


@router.post("/{workflow_id}/provision", response_model=ProjectOut)
async def provision_project_team(
    workflow_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ProjectOut:
    """Repair a partially-created team without requiring administrator access.

    Older project groups could be made visible before their Agents left the
    ``creating`` state.  The project owner can safely call this endpoint; it
    never grants a group member provisioning or management permissions.
    """
    tenant_id = _tenant_id(current_user)
    workflow = await db.scalar(
        select(ProjectWorkflow).where(
            ProjectWorkflow.id == workflow_id,
            ProjectWorkflow.tenant_id == tenant_id,
            ProjectWorkflow.creator_id == current_user.id,
        )
    )
    if workflow is None:
        raise HTTPException(status_code=404, detail="Project workflow not found")

    try:
        roles = validate_team_plan(workflow.team_plan)
    except ValueError as exc:
        workflow.status = "failed"
        workflow.failure_reason = f"团队方案无效，无法修复：{exc}"
        workflow.updated_at = datetime.now(UTC)
        await db.commit()
        raise HTTPException(status_code=422, detail=workflow.failure_reason) from exc
    roles_by_key = {role["key"]: role for role in roles}
    member_rows = (
        await db.execute(
            select(ProjectWorkflowMember, Agent)
            .join(Agent, Agent.id == ProjectWorkflowMember.agent_id)
            .where(ProjectWorkflowMember.workflow_id == workflow.id)
        )
    ).all()
    if len(member_rows) != len(roles_by_key):
        workflow.status = "failed"
        workflow.failure_reason = "项目团队成员记录不完整，无法自动修复。"
        workflow.updated_at = datetime.now(UTC)
        await db.commit()
        raise HTTPException(status_code=422, detail=workflow.failure_reason)
    participant_rows = await db.execute(
        select(Participant).where(
            Participant.type == "agent",
            Participant.ref_id.in_([agent.id for _, agent in member_rows]),
        )
    )
    participants = {participant.ref_id: participant for participant in participant_rows.scalars().all()}
    agents: list[tuple[dict, Agent, Participant]] = []
    for member, agent in member_rows:
        role = roles_by_key.get(member.role_key)
        participant = participants.get(agent.id)
        if role is None or participant is None:
            workflow.status = "failed"
            workflow.failure_reason = "项目团队成员身份不完整，无法自动修复。"
            workflow.updated_at = datetime.now(UTC)
            await db.commit()
            raise HTTPException(status_code=422, detail=workflow.failure_reason)
        agents.append((role, agent, participant))

    tenant = await db.get(Tenant, tenant_id)
    default_model_id = await _project_default_model_id(
        db,
        tenant=tenant,
        tenant_id=tenant_id,
    )
    try:
        await _provision_project_agents(
            db,
            agents=agents,
            creator_id=current_user.id,
            tenant_id=tenant_id,
            default_model_id=default_model_id,
        )
    except ProjectProvisioningError as exc:
        workflow.status = "failed"
        workflow.failure_reason = str(exc)
        workflow.updated_at = datetime.now(UTC)
        await db.commit()
        raise HTTPException(status_code=422, detail=workflow.failure_reason) from exc

    await _ensure_team_directory_contacts(
        db,
        agents=agents,
        created_by_user_id=current_user.id,
    )
    workflow.status = "active"
    workflow.failure_reason = None
    workflow.updated_at = datetime.now(UTC)
    await db.flush()
    return await _project_out(db, workflow)


@router.get("/groups/{group_id}/tasks", response_model=list[ProjectTaskOut])
async def list_project_group_tasks(
    group_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[ProjectTaskOut]:
    """Expose the durable project execution board directly from a project group."""
    tenant_id = _tenant_id(current_user)
    workflow = await db.scalar(
        select(ProjectWorkflow).where(
            ProjectWorkflow.group_id == group_id,
            ProjectWorkflow.tenant_id == tenant_id,
            ProjectWorkflow.creator_id == current_user.id,
        )
    )
    if workflow is None:
        raise HTTPException(status_code=404, detail="Project workflow not found")
    rows = await db.execute(
        select(Task, Agent.name)
        .join(Agent, Agent.id == Task.agent_id)
        .where(Task.project_workflow_id == workflow.id)
        .order_by(Task.created_at.asc())
    )
    return [
        ProjectTaskOut(
            id=task.id,
            agent_id=task.agent_id,
            agent_name=agent_name,
            title=task.title,
            description=task.description,
            status=task.status,
            priority=task.priority,
            dependency_task_ids=task.dependency_task_ids or [],
            report_to_agent_id=task.report_to_agent_id,
            is_project_closure=task.is_project_closure,
            completed_at=task.completed_at,
            updated_at=task.updated_at,
        )
        for task, agent_name in rows.all()
    ]


async def _project_group_workflow_for_user(
    db: AsyncSession,
    *,
    group_id: uuid.UUID,
    current_user: User,
) -> ProjectWorkflow:
    workflow = await db.scalar(
        select(ProjectWorkflow).where(
            ProjectWorkflow.group_id == group_id,
            ProjectWorkflow.tenant_id == _tenant_id(current_user),
            ProjectWorkflow.creator_id == current_user.id,
        )
    )
    if workflow is None:
        raise HTTPException(status_code=404, detail="Project workflow not found")
    return workflow


@router.get("/groups/{group_id}/decisions", response_model=list[ProjectDecisionOut])
async def list_project_group_decisions(
    group_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[ProjectDecisionOut]:
    """Return the active decisions that need this project's human owner."""
    workflow = await _project_group_workflow_for_user(
        db, group_id=group_id, current_user=current_user
    )
    rows = await db.execute(
        select(ProjectDecision, Agent.name)
        .outerjoin(Agent, Agent.id == ProjectDecision.requesting_agent_id)
        .where(
            ProjectDecision.workflow_id == workflow.id,
            ProjectDecision.status == "pending",
        )
        .order_by(ProjectDecision.created_at.asc())
    )
    return [
        ProjectDecisionOut(
            id=decision.id,
            task_id=decision.task_id,
            requesting_agent_id=decision.requesting_agent_id,
            requesting_agent_name=requesting_agent_name,
            title=decision.title,
            context=decision.context,
            status=decision.status,
            response=decision.response,
            created_at=decision.created_at,
            responded_at=decision.responded_at,
        )
        for decision, requesting_agent_name in rows.all()
    ]


@router.post(
    "/groups/{group_id}/decisions/{decision_id}/draft",
    response_model=ProjectDecisionDraftOut,
)
async def generate_project_decision_draft(
    group_id: uuid.UUID,
    decision_id: uuid.UUID,
    body: ProjectDecisionDraftIn,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ProjectDecisionDraftOut:
    """Generate an editable reply without answering or notifying the project group."""
    workflow = await _project_group_workflow_for_user(
        db, group_id=group_id, current_user=current_user
    )
    decision = await db.scalar(
        select(ProjectDecision).where(
            ProjectDecision.id == decision_id,
            ProjectDecision.workflow_id == workflow.id,
            ProjectDecision.group_id == group_id,
            ProjectDecision.status == "pending",
        )
    )
    if decision is None:
        raise HTTPException(status_code=404, detail="Pending project decision not found")

    tenant = await db.get(Tenant, workflow.tenant_id)
    model = await load_active_model(
        db,
        model_id=tenant.default_model_id if tenant is not None else None,
        tenant_id=workflow.tenant_id,
    )
    if model is None:
        raise HTTPException(
            status_code=422,
            detail="无法生成建议：请先在企业模型池配置可用的默认模型。",
        )
    api_key = get_model_api_key(model)
    if not api_key:
        raise HTTPException(
            status_code=422,
            detail="无法生成建议：默认模型缺少 API Key，请在企业模型池补充配置。",
        )

    client = create_llm_client(
        provider=model.provider,
        api_key=api_key,
        model=model.model,
        base_url=model.base_url,
        timeout=float(model.request_timeout or 120),
    )
    preference = body.instruction.strip()
    try:
        response = await client.complete(
            messages=[
                LLMMessage(
                    role="system",
                    content=(
                        "你是项目负责人的决策助理。根据项目和待决事项，起草一段可直接发送给"
                        "项目总负责人的中文指令。内容应明确用户的决定、修改要求或需要负责人"
                        "进一步处理的事项，简洁、可执行。只输出草稿正文；不要 Markdown 包装、"
                        "解释、前后缀、JSON 或 <think>/<thinking> 标签。"
                    ),
                ),
                LLMMessage(
                    role="user",
                    content=(
                        f"项目名称：{workflow.name}\n"
                        f"项目需求：{workflow.requirements}\n\n"
                        f"待决事项：{decision.title}\n"
                        f"待决上下文：{decision.context}\n\n"
                        f"用户补充偏好：{preference or '无，请基于待决上下文给出合理建议。'}"
                    ),
                ),
            ],
            temperature=0.2,
            max_tokens=800,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=422,
            detail=(
                f"生成建议时调用默认模型失败（{type(exc).__name__}）。"
                "请检查默认模型、API Key 与服务地址。"
            ),
        ) from exc
    finally:
        await client.close()

    # The shared client normally moves these tags into reasoning metadata.  Keep
    # this final guard for provider variants that return raw tag-marked content.
    draft = re.sub(
        r"<think(?:ing)?\b[^>]*>.*?</think(?:ing)?\s*>",
        "",
        response.content or "",
        flags=re.IGNORECASE | re.DOTALL,
    )
    draft = re.sub(r"<think(?:ing)?\b[^>]*>.*", "", draft, flags=re.IGNORECASE | re.DOTALL)
    draft = re.sub(r"</?think(?:ing)?\b[^>]*>", "", draft, flags=re.IGNORECASE).strip()
    if not draft:
        raise HTTPException(status_code=422, detail="默认模型未返回可用的建议内容，请重试。")
    return ProjectDecisionDraftOut(draft=draft)


@router.post("/groups/{group_id}/decisions/{decision_id}/reply", response_model=ProjectDecisionOut)
async def reply_to_project_group_decision(
    group_id: uuid.UUID,
    decision_id: uuid.UUID,
    body: ProjectDecisionReplyIn,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ProjectDecisionOut:
    """Record a decision or natural-language modification for the group leader."""
    workflow = await _project_group_workflow_for_user(
        db, group_id=group_id, current_user=current_user
    )
    decision = await db.scalar(
        select(ProjectDecision).where(
            ProjectDecision.id == decision_id,
            ProjectDecision.workflow_id == workflow.id,
            ProjectDecision.group_id == group_id,
        ).with_for_update()
    )
    if decision is None:
        raise HTTPException(status_code=404, detail="Project decision not found")
    if decision.status != "pending":
        raise HTTPException(status_code=409, detail="Project decision has already been answered")
    response = body.response.strip()
    decision.status = "answered"
    decision.response = response
    decision.responded_at = datetime.now(UTC)
    participant = await get_or_create_user_participant(
        db, current_user.id, current_user.display_name, current_user.avatar_url
    )
    if body.intent == "modification":
        leader_instruction = (
            f"【用户修改指令】待决事项「{decision.title}」\n{response}\n\n"
            "请项目总负责人把这条自然语言指令视为对当前项目计划的直接修改："
            "更新相关任务、依赖、负责人或验收标准，按需重新分派，并在群内回报变更与风险。"
        )
    else:
        leader_instruction = (
            f"针对待决事项「{decision.title}」，我的决定是：\n{response}\n\n"
            "请项目总负责人据此调整任务、分派执行并回报结果。"
        )
    try:
        await group_message_service.enqueue_group_message(
            db,
            tenant_id=workflow.tenant_id,
            group_id=group_id,
            session_id=decision.session_id,
            sender_participant_id=participant.id,
            content=leader_instruction,
            message_id=uuid.uuid4(),
            project_task_dispatch=False,
        )
    except GroupMessageServiceError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    requester_name = await db.scalar(
        select(Agent.name).where(Agent.id == decision.requesting_agent_id)
    ) if decision.requesting_agent_id is not None else None
    return ProjectDecisionOut(
        id=decision.id,
        task_id=decision.task_id,
        requesting_agent_id=decision.requesting_agent_id,
        requesting_agent_name=requester_name,
        title=decision.title,
        context=decision.context,
        status=decision.status,
        response=decision.response,
        created_at=decision.created_at,
        responded_at=decision.responded_at,
    )


@router.post("/groups/{group_id}/task-flows", status_code=status.HTTP_201_CREATED)
async def start_project_group_task_flow(
    group_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Start the Task loop for a pre-existing project group exactly once per request.

    This is the safe migration path for project groups created before the
    Task-driven loop existed. New project messages already start it implicitly.
    """
    tenant_id = _tenant_id(current_user)
    workflow = await db.scalar(
        select(ProjectWorkflow).where(
            ProjectWorkflow.group_id == group_id,
            ProjectWorkflow.tenant_id == tenant_id,
            ProjectWorkflow.creator_id == current_user.id,
            ProjectWorkflow.status == "active",
        )
    )
    if workflow is None:
        raise HTTPException(status_code=404, detail="Project workflow not found")
    session = await db.scalar(
        select(ChatSession).where(
            ChatSession.group_id == group_id,
            ChatSession.tenant_id == tenant_id,
            ChatSession.deleted_at.is_(None),
        ).order_by(ChatSession.created_at.asc())
    )
    if session is None:
        raise HTTPException(status_code=422, detail="Project group session not found")
    participant = await get_or_create_user_participant(
        db,
        current_user.id,
        current_user.display_name,
        current_user.avatar_url,
    )
    try:
        intake = await group_message_service.enqueue_group_message(
            db,
            tenant_id=tenant_id,
            group_id=group_id,
            session_id=session.id,
            sender_participant_id=participant.id,
            content=(
                "启动项目任务流。请以任务完成、依赖解锁和交付回报推进，"
                f"不要使用固定时间表。\n\n项目目标：{workflow.requirements}"
            ),
            message_id=uuid.uuid4(),
        )
    except GroupMessageServiceError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {
        "message_id": str(intake.message.id),
        "run_ids": [str(handle.run_id) for handle in intake.run_handles],
        "status": "started",
    }


@router.get("/{workflow_id}", response_model=ProjectOut)
async def get_project(
    workflow_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ProjectOut:
    tenant_id = _tenant_id(current_user)
    result = await db.execute(
        select(ProjectWorkflow).where(
            ProjectWorkflow.id == workflow_id,
            ProjectWorkflow.tenant_id == tenant_id,
            ProjectWorkflow.creator_id == current_user.id,
        )
    )
    workflow = result.scalar_one_or_none()
    if workflow is None:
        raise HTTPException(status_code=404, detail="Project workflow not found")
    return await _project_out(db, workflow)
