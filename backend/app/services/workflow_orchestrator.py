"""Workflow Orchestrator — plans and executes multi-agent trade workflows.

Three phases:
1. Planning: LLM generates execution plan from user instruction + agent capabilities
2. Execution: Sequential step execution with full tool-calling loop
3. Summary: LLM summarizes all deliverables and suggests next steps
"""

import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.database import async_session
from app.models.agent import Agent
from app.models.llm import LLMModel
from app.models.workflow import Workflow, WorkflowStep

logger = logging.getLogger(__name__)



async def _retry_llm_call(fn, max_retries=3, base_delay=5.0):
    """Retry LLM calls on transient errors with exponential backoff."""
    retryable = ("429", "overloaded", "rate_limit", "disconnected", "ConnectError", "timeout", "502", "503", "524")
    for attempt in range(max_retries + 1):
        try:
            return await fn()
        except Exception as e:
            err_str = str(e)
            if any(k in err_str for k in retryable) and attempt < max_retries:
                delay = base_delay * (2 ** attempt)
                logger.warning(f"LLM transient error (attempt {attempt+1}/{max_retries+1}): {err_str[:100]}, retrying in {delay}s...")
                await asyncio.sleep(delay)
                continue
            raise

# Agent name -> role mapping for the planning prompt
AGENT_ROLES = {
    "Leo": "客户挖掘专家 — google_maps_search, find_emails, enrich_lead, web_search, jina_search, jina_read",
    "Ava": "情报研究员 — enrich_lead, jina_search, jina_read, recall, remember, web_search",
    "Marco": "跨文化开发信+社媒营销专家 — send_email, publish_social, social_plan_content, social_batch_schedule, social_list_posts, social_get_analytics, jina_search",
    "Iris": "市场战略师 — web_search, jina_search, jina_read, social_get_analytics",
    "Serena": "SDR需求分析 — recall, remember, jina_search, send_email, read_emails, reply_email",
    "Orion": "成交专家 — generate_pi, send_email, write_file, execute_code",
    "Meeseeks": "任务执行者 — write_file, execute_code, list_files, web_search",
}

PLANNING_PROMPT = """你是 PulseAgent 外贸全流程工作流编排器。

用户输入了一个外贸业务目标，你需要编排多个 AI 数字员工协作完成全流程。

## 可用数字员工
{agent_list}

## 可用工具清单
- web_search: Tavily 互联网搜索（行业报告、公司信息、市场数据）
- jina_search: Jina AI 搜索（结构化搜索结果）
- jina_read: 读取网页正文内容
- google_maps_search: 按关键词+地区搜索 Google Maps 企业（名称、地址、电话、网站）
- find_emails: 查找公司域名关联邮箱 (Hunter.io)
- enrich_lead: 企业画像增强（规模、行业、联系人、社媒）
- send_email: 发送邮件（SMTP）
- read_emails: 读取收件箱
- reply_email: 回复邮件
- publish_social: 发布社媒到 LinkedIn/Twitter/Facebook 等（Postiz，真实发布）
- social_plan_content: AI 生成多天多平台社媒内容排期
- social_batch_schedule: 批量排期发布社媒
- social_list_posts: 查看已排期/已发布帖子
- social_get_analytics: 获取社媒互动/曝光数据分析
- generate_pi: 生成 Proforma Invoice 形式发票
- write_file: 保存文件到工作空间
- execute_code: 执行 Python 代码
- remember / recall: 长期语义记忆存取

## 标准外贸全流程步骤（按需裁剪）
1. **市场调研** (Iris) → web_search + jina_search 搜索目标市场数据，输出市场分析报告
2. **客户挖掘** (Leo) → google_maps_search + find_emails + enrich_lead 挖掘潜在客户，输出客户列表
3. **客户画像** (Ava) → enrich_lead + jina_read 深入调研重点客户，输出客户画像
4. **社媒营销** (Marco) → social_plan_content 生成内容计划 + publish_social 真实发布到社媒平台
5. **开发信** (Marco) → 基于客户数据撰写个性化开发信 + send_email 真实发送
6. **SDR 跟进** (Serena) → BANT 需求分析话术 + send_email 首次触达
7. **报价单** (Orion) → generate_pi 生成 Proforma Invoice

## 输出格式
严格返回 JSON（无其他文字）：
{{
  "title": "工作流标题（简短中文）",
  "steps": [
    {{
      "agent_name": "员工名字（必须是上面列表中的）",
      "title": "步骤标题",
      "instruction": "给该员工的具体指令，要包含用户的产品/行业/目标市场等上下文。指令要足够详细，让员工能独立完成。",
      "deliverable_type": "table|markdown|email_template|pi|social_post|report"
    }}
  ]
}}

规则：
- 根据用户目标选择 4-7 个步骤，不必包含所有步骤
- 不要添加"综合报告"或"汇总"步骤，工作流引擎会自动生成汇总
- instruction 必须具体，包含用户提供的所有上下文（产品、公司、目标市场等）
- 如果用户提供了公司官网、产品信息，在每步 instruction 中都要传递
- table 类型用于客户列表，email_template 用于开发信，report 用于分析报告，markdown 用于通用内容
"""

SUMMARY_PROMPT = """以下是一个外贸全流程工作流的执行结果：

用户指令：{instruction}

各步骤交付物：
{deliverables}

请生成汇总，严格返回 JSON：
{{
  "summary": "200字以内的关键成果汇总",
  "next_steps": "- 建议1（具体可操作）\\n- 建议2\\n- 建议3\\n- 建议4\\n- 建议5"
}}
"""


async def get_available_agents(tenant_id: uuid.UUID) -> list[dict]:
    async with async_session() as db:
        result = await db.execute(
            select(Agent).where(Agent.tenant_id == tenant_id, Agent.status.in_(["running", "idle"]))
        )
        agents = result.scalars().all()
        return [
            {
                "name": a.name,
                "id": str(a.id),
                "role": a.role_description or "",
                "tools": AGENT_ROLES.get(a.name, ""),
            }
            for a in agents
        ]


async def get_default_llm_model() -> LLMModel | None:
    async with async_session() as db:
        # Prefer non-free models to avoid rate limiting
        result = await db.execute(
            select(LLMModel).where(LLMModel.enabled == True)
            .order_by(LLMModel.model.contains(":free").asc())
            .limit(1)
        )
        return result.scalar_one_or_none()


async def call_llm_simple(system_prompt: str, user_prompt: str) -> str:
    model = await get_default_llm_model()
    if not model:
        raise RuntimeError("No LLM model configured")

    from app.services.llm_client import create_llm_client
    from app.services.llm_utils import LLMMessage

    client = create_llm_client(
        provider=model.provider,
        api_key=model.api_key_encrypted,
        model=model.model,
        base_url=model.base_url,
        timeout=120.0,
    )
    try:
        async def _do_call():
            return await client.complete(
                messages=[
                    LLMMessage(role="system", content=system_prompt),
                    LLMMessage(role="user", content=user_prompt),
                ],
                temperature=0.3,
                max_tokens=4096,
            )
        response = await _retry_llm_call(_do_call, max_retries=3, base_delay=8.0)
        return response.content or ""
    finally:
        await client.close()


async def plan_workflow(instruction: str, tenant_id: uuid.UUID) -> dict:
    agents = await get_available_agents(tenant_id)
    if not agents:
        raise ValueError("No active agents found — please ensure at least one agent is running")

    agent_list = "\n".join(
        f"- **{a['name']}**: {a['role']}" + (f" (工具: {a['tools']})" if a['tools'] else "")
        for a in agents
    )
    system = PLANNING_PROMPT.format(agent_list=agent_list)

    raw = await call_llm_simple(system, instruction)
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0]

    try:
        plan = json.loads(raw)
    except json.JSONDecodeError as e:
        logger.error(f"Plan JSON parse error: {e}, raw: {raw[:500]}")
        raise ValueError(f"LLM returned invalid plan: {str(e)[:100]}")

    # Map agent names to IDs
    agent_map = {a["name"]: a["id"] for a in agents}
    for step in plan.get("steps", []):
        name = step.get("agent_name", "")
        step["agent_id"] = agent_map.get(name)
        if not step["agent_id"]:
            for aname, aid in agent_map.items():
                if name.lower() in aname.lower() or aname.lower() in name.lower():
                    step["agent_id"] = aid
                    step["agent_name"] = aname
                    break
            if not step.get("agent_id") and agents:
                step["agent_id"] = agents[0]["id"]
                step["agent_name"] = agents[0]["name"]

    return plan


async def create_and_run_workflow(
    instruction: str, user_id: uuid.UUID, tenant_id: uuid.UUID
) -> uuid.UUID:
    # Create workflow record immediately, plan + execute in background
    async with async_session() as db:
        workflow = Workflow(
            title=instruction[:80],
            user_instruction=instruction,
            status="planning",
            created_by=user_id,
            tenant_id=tenant_id,
        )
        db.add(workflow)
        await db.flush()
        wf_id = workflow.id
        await db.commit()

    asyncio.create_task(_plan_and_execute(wf_id, instruction, tenant_id), name=f"workflow-{wf_id}")
    return wf_id


async def _plan_and_execute(workflow_id: uuid.UUID, instruction: str, tenant_id: uuid.UUID) -> None:
    try:
        plan = await plan_workflow(instruction, tenant_id)
        title = plan.get("title", instruction[:80])

        async with async_session() as db:
            result = await db.execute(select(Workflow).where(Workflow.id == workflow_id))
            wf = result.scalar_one()
            wf.title = title
            wf.plan_data = plan

            for i, step_data in enumerate(plan.get("steps", [])):
                step = WorkflowStep(
                    workflow_id=workflow_id,
                    agent_id=uuid.UUID(step_data["agent_id"]) if step_data.get("agent_id") else None,
                    step_order=i,
                    title=step_data.get("title", f"步骤 {i+1}"),
                    instruction=step_data.get("instruction", ""),
                    agent_name=step_data.get("agent_name", ""),
                    deliverable_type=step_data.get("deliverable_type", "markdown"),
                )
                db.add(step)

            wf.status = "running"
            await db.commit()

        await _execute_workflow(workflow_id)

    except Exception as e:
        logger.error(f"Workflow {workflow_id} plan+execute error: {e}")
        import traceback
        traceback.print_exc()
        try:
            async with async_session() as db:
                result = await db.execute(select(Workflow).where(Workflow.id == workflow_id))
                wf = result.scalar_one_or_none()
                if wf:
                    wf.status = "failed"
                    wf.summary = f"规划失败: {str(e)[:200]}"
                    await db.commit()
        except Exception as db_err:
            logger.error(f"Failed to update workflow {workflow_id} status: {db_err}")


async def _execute_workflow(workflow_id: uuid.UUID) -> None:
    try:
        async with async_session() as db:
            result = await db.execute(
                select(Workflow).where(Workflow.id == workflow_id).options(selectinload(Workflow.steps))
            )
            workflow = result.scalar_one_or_none()
            if not workflow:
                return
            steps = sorted(workflow.steps, key=lambda s: s.step_order)

        prev_outputs: list[str] = []

        for step in steps:
            async with async_session() as db:
                result = await db.execute(select(WorkflowStep).where(WorkflowStep.id == step.id))
                s = result.scalar_one()
                s.status = "running"
                s.started_at = datetime.now(timezone.utc)
                await db.commit()

            try:
                output = await _execute_step(step, prev_outputs, workflow.user_instruction)
                prev_outputs.append(f"[{step.title}]\n{output[:2000]}")

                async with async_session() as db:
                    result = await db.execute(select(WorkflowStep).where(WorkflowStep.id == step.id))
                    s = result.scalar_one()
                    s.status = "done"
                    s.raw_output = output
                    s.deliverable_data = {"content": output}
                    s.completed_at = datetime.now(timezone.utc)
                    await db.commit()

                print(f"[Workflow] Step {step.step_order+1} '{step.title}' done ({len(output)} chars)")

            except Exception as e:
                logger.error(f"Workflow step {step.id} failed: {e}")
                async with async_session() as db:
                    result = await db.execute(select(WorkflowStep).where(WorkflowStep.id == step.id))
                    s = result.scalar_one()
                    s.status = "failed"
                    s.raw_output = f"Error: {str(e)[:500]}"
                    s.completed_at = datetime.now(timezone.utc)
                    await db.commit()

        # Phase 3: Summary
        await _summarize_workflow(workflow_id)

    except Exception as e:
        logger.error(f"Workflow {workflow_id} execution error: {e}")
        import traceback
        traceback.print_exc()
        try:
            async with async_session() as db:
                result = await db.execute(select(Workflow).where(Workflow.id == workflow_id))
                wf = result.scalar_one_or_none()
                if wf:
                    wf.status = "failed"
                    await db.commit()
        except Exception as db_err:
            logger.error(f"Failed to update workflow {workflow_id} status: {db_err}")


async def _execute_step(step: WorkflowStep, prev_outputs: list[str], user_instruction: str) -> str:
    if not step.agent_id:
        return "No agent assigned"

    context_from_prev = "\n\n".join(prev_outputs[-3:]) if prev_outputs else ""

    deliverable_hints = {
        "table": "输出 Markdown 表格，包含完整数据（公司名、联系人、邮箱、电话、网站等列）",
        "markdown": "输出结构化 Markdown 报告",
        "email_template": "输出 3 封不同风格的个性化开发信模板（邮件主题 + 正文），可直接发送",
        "pi": "使用 generate_pi 工具生成 Proforma Invoice",
        "social_post": """发布社媒内容。必须按以下步骤调用 publish_social 工具：
第一步：调用 publish_social(action="list_channels") 获取已连接的社媒渠道列表
第二步：根据返回的 integration ID，调用 publish_social(action="post", content="帖子内容", integration_ids=["id1","id2"])
注意：action 和 content 是必填参数。如果 list_channels 返回空，说明未连接社媒，改为输出帖子文案即可。""",
        "report": "输出详细的分析报告，包含数据、洞察和建议",
        "bant": "输出 BANT 客户分析（Budget/Authority/Need/Timeline）",
    }

    hint = deliverable_hints.get(step.deliverable_type, "输出结构化 Markdown")

    task_prompt = f"""你正在执行一个外贸全流程工作流的一个环节。请充分使用你的工具来完成任务。

## 用户的总体目标
{user_instruction}

## 你的当前任务
{step.title}

## 具体指令
{step.instruction}

## 交付物要求
{hint}

{f'## 前置步骤产出（供参考）\n{context_from_prev}' if context_from_prev else ''}

重要：
1. 主动使用你的工具（搜索、数据库查询、邮件发送等）获取真实数据
2. 不要编造数据，如果工具调用失败，说明原因
3. 直接输出交付物内容"""

    async with async_session() as db:
        agent_result = await db.execute(select(Agent).where(Agent.id == step.agent_id))
        agent = agent_result.scalar_one_or_none()
        if not agent:
            return "Agent not found"

        model_id = agent.primary_model_id or agent.fallback_model_id
        if not model_id:
            return f"{agent.name} 未配置 LLM 模型"

        model_result = await db.execute(select(LLMModel).where(LLMModel.id == model_id))
        model = model_result.scalar_one_or_none()
        if not model:
            return "LLM model not found"

        creator_id = agent.creator_id
        agent_name = agent.name
        agent_role = agent.role_description or ""

    from app.services.llm_client import create_llm_client
    from app.services.llm_utils import LLMMessage, get_max_tokens
    from app.services.agent_context import build_agent_context
    from app.services.agent_tools import get_agent_tools_for_llm, execute_tool

    system_prompt = await build_agent_context(step.agent_id, agent_name, agent_role)

    client = create_llm_client(
        provider=model.provider,
        api_key=model.api_key_encrypted,
        model=model.model,
        base_url=model.base_url,
        timeout=600.0,
    )

    tools = await get_agent_tools_for_llm(step.agent_id)

    messages = [
        LLMMessage(role="system", content=system_prompt),
        LLMMessage(role="user", content=task_prompt),
    ]

    try:
        reply = ""
        tool_results_collected = []
        last_content = ""
        repeat_count = 0
        for round_i in range(10):  # Up to 10 tool rounds (reduced from 15)
            async def _do_step_call():
                return await client.complete(
                    messages=messages,
                    tools=tools if tools else None,
                    temperature=0.5,
                    max_tokens=get_max_tokens(model.provider, model.model),
                )
            response = await _retry_llm_call(_do_step_call, max_retries=3, base_delay=8.0)

            # Detect repeated content (loop prevention)
            current_content = (response.content or "")[:200]
            if current_content and current_content == last_content:
                repeat_count += 1
                if repeat_count >= 2:
                    print(f"[Workflow] Step '{step.title}' detected loop (same output {repeat_count+1}x), stopping")
                    reply = response.content or ""
                    if tool_results_collected:
                        reply += "\n\n## 工具调用结果\n\n" + "\n\n".join(tool_results_collected[-3:])
                    break
            else:
                repeat_count = 0
            last_content = current_content

            if response.tool_calls:
                messages.append(LLMMessage(
                    role="assistant", content=response.content or None,
                    tool_calls=[{"id": tc["id"], "type": "function", "function": tc["function"]} for tc in response.tool_calls],
                ))
                for tc in response.tool_calls:
                    fn = tc["function"]
                    tool_name = fn["name"]
                    try:
                        args = json.loads(fn.get("arguments", "{}")) if fn.get("arguments") else {}
                    except Exception:
                        args = {}
                    print(f"[Workflow] Step '{step.title}' calling tool: {tool_name}")
                    tool_result = await execute_tool(tool_name, args, step.agent_id, creator_id)
                    tool_result_str = str(tool_result)[:8000]
                    tool_results_collected.append(f"[{tool_name}]: {tool_result_str[:2000]}")
                    messages.append(LLMMessage(role="tool", tool_call_id=tc["id"], content=tool_result_str))
            else:
                reply = response.content or ""
                break
        else:
            if tool_results_collected:
                reply = "## 工具调用结果汇总\n\n" + "\n\n".join(tool_results_collected[-5:])
            else:
                reply = response.content or "(max tool rounds reached)"

        return reply
    except Exception as e:
        logger.error(f"Step execution error: {e}")
        raise
    finally:
        await client.close()


async def _summarize_workflow(workflow_id: uuid.UUID) -> None:
    async with async_session() as db:
        result = await db.execute(
            select(Workflow).where(Workflow.id == workflow_id).options(selectinload(Workflow.steps))
        )
        workflow = result.scalar_one_or_none()
        if not workflow:
            return

        deliverables = ""
        for s in sorted(workflow.steps, key=lambda x: x.step_order):
            status_icon = "[完成]" if s.status == "done" else "[失败]"
            output = (s.raw_output or "")[:800]
            deliverables += f"\n### {status_icon} {s.title} ({s.agent_name})\n{output}\n"

    try:
        system = "你是一个外贸工作流汇总助手。返回 JSON 格式。"
        user_msg = SUMMARY_PROMPT.format(instruction=workflow.user_instruction, deliverables=deliverables)
        raw = await call_llm_simple(system, user_msg)
        raw = raw.strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0]
        result_data = json.loads(raw)
    except Exception as e:
        logger.error(f"Summary generation failed: {e}")
        result_data = {"summary": "工作流已完成，请查看各步骤交付物", "next_steps": "- 审阅各步骤产出\n- 筛选高价值客户\n- 发送开发信"}

    async with async_session() as db:
        result = await db.execute(select(Workflow).where(Workflow.id == workflow_id))
        wf = result.scalar_one()
        wf.status = "done"
        wf.summary = result_data.get("summary", "")
        wf.next_steps = result_data.get("next_steps", "")
        wf.completed_at = datetime.now(timezone.utc)
        await db.commit()

    print(f"[Workflow] {workflow_id} completed with summary")


async def get_workflow_detail(workflow_id: uuid.UUID) -> Workflow | None:
    async with async_session() as db:
        result = await db.execute(
            select(Workflow).where(Workflow.id == workflow_id).options(selectinload(Workflow.steps))
        )
        return result.scalar_one_or_none()


async def list_workflows(tenant_id: uuid.UUID, user_id: uuid.UUID, page: int = 1, size: int = 20) -> tuple[list[Workflow], int]:
    async with async_session() as db:
        from sqlalchemy import func as sqlfunc
        count_result = await db.execute(
            select(sqlfunc.count()).select_from(Workflow).where(
                Workflow.tenant_id == tenant_id, Workflow.created_by == user_id
            )
        )
        total = count_result.scalar() or 0

        result = await db.execute(
            select(Workflow).where(
                Workflow.tenant_id == tenant_id, Workflow.created_by == user_id
            ).order_by(Workflow.created_at.desc()).offset((page - 1) * size).limit(size)
        )
        return list(result.scalars().all()), total
