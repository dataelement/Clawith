"""HR Agent team-planning service.

The HR Agent proposes a team from the user's actual brief.  It never writes to
the database itself: the user reviews the returned plan before provisioning.
"""

from __future__ import annotations

import json
import re
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


HR_RECRUITER_NAME = "HR 招聘 Agent"


class HRPlanningError(RuntimeError):
    pass


async def _get_or_create_hr_recruiter(
    db: "AsyncSession",
    *,
    tenant_id: uuid.UUID,
    creator_id: uuid.UUID,
    model_id: uuid.UUID | None,
):
    """Materialize the built-in HR Recruiter so its work has an audit identity."""
    from sqlalchemy import select

    from app.models.agent import Agent, AgentPermission

    result = await db.execute(
        select(Agent).where(
            Agent.tenant_id == tenant_id,
            Agent.name == HR_RECRUITER_NAME,
            Agent.is_system.is_(True),
            Agent.deleted_at.is_(None),
        ).limit(1)
    )
    recruiter = result.scalar_one_or_none()
    if recruiter is None:
        recruiter = Agent(
            id=uuid.uuid4(),
            name=HR_RECRUITER_NAME,
            role_description="根据项目需求招聘并组建可执行的 AI 团队",
            bio="系统 HR 招聘 Agent：将项目目标转化为岗位、职责边界与项目群主。",
            creator_id=creator_id,
            tenant_id=tenant_id,
            status="idle",
            is_system=True,
            heartbeat_enabled=False,
            primary_model_id=model_id,
        )
        db.add_all((
            recruiter,
            AgentPermission(agent_id=recruiter.id, scope_type="company", access_level="use"),
        ))
    elif model_id is not None:
        recruiter.primary_model_id = model_id
    await db.flush()
    return recruiter


async def _start_hr_planning_run(
    db: "AsyncSession",
    *,
    tenant_id: uuid.UUID,
    creator_id: uuid.UUID,
    model_id: uuid.UUID | None,
    name: str,
    requirements: str,
):
    """Record HR planning in the same immutable Run ledger as every other AI task."""
    from app.models.agent_run import AgentRun
    from app.models.agent_run_event import AgentRunEvent

    recruiter = await _get_or_create_hr_recruiter(
        db,
        tenant_id=tenant_id,
        creator_id=creator_id,
        model_id=model_id,
    )
    run = AgentRun(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        agent_id=recruiter.id,
        source_type="task",
        source_id="hr_team_plan",
        origin_user_id=creator_id,
        goal=f"HR 团队组建：{name.strip()}\n{requirements.strip()}",
        run_kind="foreground",
        model_id=model_id,
        model_turn_limit=1,
        runtime_type="legacy",
        runtime_thread_id=f"hr-team-plan:{uuid.uuid4()}",
        graph_name="hr_team_planner",
        graph_version="v1",
        delivery_status="not_required",
    )
    now = datetime.now(UTC)
    db.add_all((
        run,
        AgentRunEvent(
            id=uuid.uuid5(run.id, "lifecycle-event:run_created"),
            tenant_id=tenant_id,
            run_id=run.id,
            agent_id=recruiter.id,
            event_type="run_created",
            summary="HR 招聘 Agent 开始组建团队",
            payload={"status": "running", "source_type": "hr_team_plan"},
            artifact_refs=[],
            idempotency_key=f"run:{run.id}:created",
            source_checkpoint_id=None,
            created_at=now,
        ),
    ))
    await db.flush()
    return run


async def _finish_hr_planning_run(
    db: "AsyncSession",
    *,
    run,
    succeeded: bool,
    roles_count: int = 0,
    error_code: str | None = None,
) -> None:
    from app.models.agent_run_event import AgentRunEvent

    event_type = "run_completed" if succeeded else "run_failed"
    db.add(
        AgentRunEvent(
            id=uuid.uuid5(run.id, f"lifecycle-event:{event_type}"),
            tenant_id=run.tenant_id,
            run_id=run.id,
            agent_id=run.agent_id,
            event_type=event_type,
            summary=("HR 招聘 Agent 已生成团队方案" if succeeded else "HR 招聘 Agent 团队方案生成失败"),
            payload={
                "status": "completed" if succeeded else "failed",
                "report_type": "hr_team_plan",
                "roles_count": roles_count,
                **({"error_code": error_code or "hr_team_plan_failed"} if not succeeded else {}),
            },
            artifact_refs=[],
            idempotency_key=f"run:{run.id}:{event_type}",
            source_checkpoint_id=None,
            created_at=datetime.now(UTC),
        )
    )
    await db.flush()


def _json_object(text: str) -> dict:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", cleaned, flags=re.IGNORECASE)
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start < 0 or end < start:
        raise HRPlanningError("HR 招聘 Agent 未返回有效的团队方案，请重试")
    try:
        value = json.loads(cleaned[start : end + 1])
    except json.JSONDecodeError as exc:
        raise HRPlanningError("HR 招聘 Agent 返回的团队方案格式无效，请重试") from exc
    if not isinstance(value, dict):
        raise HRPlanningError("HR 招聘 Agent 返回的团队方案格式无效，请重试")
    return value


def _role_key(value: str, index: int) -> str:
    key = re.sub(r"[^a-z0-9_]+", "_", value.lower()).strip("_")
    return key[:56] or f"role_{index + 1}"


def validate_team_plan(team_plan: dict) -> list[dict]:
    """Validate an HR proposal or its user-edited confirmation payload."""
    if not isinstance(team_plan, dict):
        raise ValueError("Team plan is invalid")
    roles = team_plan.get("roles")
    if not isinstance(roles, list) or not roles or len(roles) > 15:
        raise ValueError("Team plan must include between 1 and 15 roles")
    normalized: list[dict] = []
    keys: set[str] = set()
    for index, role in enumerate(roles):
        if not isinstance(role, dict):
            raise ValueError("Team role is invalid")
        title = str(role.get("name", "")).strip()
        description = str(role.get("role_description", "")).strip()
        key = _role_key(str(role.get("key") or title), index)
        personality = str(role.get("personality") or f"专注于{title}，主动协作并及时向项目负责人同步。")[:2_000]
        boundaries = str(role.get("boundaries") or "仅在职责范围内行动；遇到范围、风险或外部决策时先向项目负责人汇报。")[:2_000]
        if not title or not description or len(title) > 100 or len(description) > 500 or key in keys:
            raise ValueError("Team role fields are invalid")
        keys.add(key)
        normalized.append({
            "key": key,
            "name": title,
            "role_description": description,
            "personality": personality,
            "boundaries": boundaries,
            "is_group_leader": bool(role.get("is_group_leader")),
        })
    if sum(role["is_group_leader"] for role in normalized) != 1:
        raise ValueError("Team plan must have exactly one group leader")
    return normalized


def parse_hr_team_plan(*, name: str, requirements: str, response_text: str) -> dict:
    proposal = _json_object(response_text)
    roles = validate_team_plan(proposal)
    plan = {
        "planner_name": HR_RECRUITER_NAME,
        "project_name": name.strip(),
        "requirements": requirements.strip(),
        "roles": roles,
    }
    plan["wake_up_message"] = build_team_wakeup_message(plan)
    return plan


def build_team_wakeup_message(team_plan: dict) -> str:
    """Build a user-visible kickoff message for the selected team leader."""
    roles = validate_team_plan(team_plan)
    leader = next(role for role in roles if role["is_group_leader"])
    teammate_lines = "\n".join(
        f"- @{role['name']}：{role['role_description']}"
        for role in roles
        if not role["is_group_leader"]
    ) or "- 暂无其他成员"
    return (
        f"@{leader['name']}，你是「{str(team_plan.get('project_name') or '本项目')}」的项目总负责人。\n\n"
        f"项目需求：\n{str(team_plan.get('requirements') or '').strip()}\n\n"
        "请现在启动团队：\n"
        "1. 基于项目目标拆分工作包、优先级、验收标准和时间节奏；\n"
        "2. 在群内 @ 以下成员逐项分派任务，并协调依赖与风险：\n"
        f"{teammate_lines}\n"
        "3. 需要我决策时先向我汇报；全部完成后，汇总交付物、关键数据、风险和下一步建议，向我提交完成报告。"
    )


def _hr_system_prompt() -> str:
    return """你是 HR 招聘 Agent。根据用户的项目需求组建最小且足够的 Agent 团队。
不要套用固定部门模板；根据目标、交付物、约束和专业需要决定岗位。团队中必须且只能有一位项目群主。
群主由项目需要决定，可能是创始人、业务负责人、技术负责人、运营负责人或项目经理；不要默认设置为 PMO。
群主负责接收用户要求、拆分和分派工作、整合结果并向用户汇报；它不是招聘负责人。
例如跨境电商、Shopify 建站或一件代发需求，应覆盖市场与竞品调研、选品/供应链、店铺搭建、内容与增长、运营数据/利润核算等真实必要能力，并按需求选择合适群主。
只返回一个 JSON 对象，不要 Markdown，不要解释，不要 <think> 内容。格式：
{"roles":[{"key":"english_snake_case","name":"岗位名称","role_description":"职责与交付物","personality":"工作风格","boundaries":"职责边界","is_group_leader":true}]}"""


async def plan_team_with_hr(
    db: "AsyncSession",
    *,
    tenant_id: uuid.UUID,
    creator_id: uuid.UUID,
    name: str,
    requirements: str,
) -> dict:
    """Call the tenant model as the built-in HR Agent and parse its safe proposal."""
    from app.models.tenant import Tenant
    from app.services.llm.model_resolution import load_active_model
    from app.services.llm.utils import LLMMessage, create_llm_client, get_model_api_key

    tenant = await db.get(Tenant, tenant_id)
    model = await load_active_model(
        db,
        model_id=tenant.default_model_id if tenant is not None else None,
        tenant_id=tenant_id,
    )
    run = await _start_hr_planning_run(
        db,
        tenant_id=tenant_id,
        creator_id=creator_id,
        model_id=model.id if model is not None else None,
        name=name,
        requirements=requirements,
    )
    if model is None:
        error = HRPlanningError("请先在公司设置中配置可用的默认模型，HR 招聘 Agent 才能组建团队")
        await _finish_hr_planning_run(db, run=run, succeeded=False, error_code="default_model_unavailable")
        raise error
    api_key = get_model_api_key(model)
    if not api_key:
        error = HRPlanningError("HR 招聘 Agent 使用的默认模型没有 API Key，请在公司设置中补充配置")
        await _finish_hr_planning_run(db, run=run, succeeded=False, error_code="model_api_key_missing")
        raise error
    client = create_llm_client(
        provider=model.provider,
        api_key=api_key,
        model=model.model,
        base_url=model.base_url,
        timeout=float(model.request_timeout or 120),
    )
    try:
        response = await client.complete(
            messages=[
                LLMMessage(role="system", content=_hr_system_prompt()),
                LLMMessage(role="user", content=f"项目名称：{name.strip()}\n项目需求：\n{requirements.strip()}"),
            ],
            temperature=0.2,
            max_tokens=1800,
        )
    except Exception as exc:
        await _finish_hr_planning_run(db, run=run, succeeded=False, error_code="model_call_failed")
        raise HRPlanningError(
            f"HR 招聘 Agent 调用默认模型失败（{type(exc).__name__}）。请检查公司默认模型、API Key 与服务地址"
        ) from exc
    finally:
        await client.close()
    try:
        plan = parse_hr_team_plan(name=name, requirements=requirements, response_text=response.content or "")
    except HRPlanningError:
        await _finish_hr_planning_run(db, run=run, succeeded=False, error_code="invalid_team_plan")
        raise
    await _finish_hr_planning_run(db, run=run, succeeded=True, roles_count=len(plan["roles"]))
    return plan
