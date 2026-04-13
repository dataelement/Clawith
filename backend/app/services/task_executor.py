"""Background task executor — runs LLM to complete tasks automatically.

Uses the same agent context (soul, memory, skills, relationships, tools)
as the chat dialog. Supports tool-calling loop for autonomous execution.
"""

import asyncio
import json
import uuid
from datetime import datetime, timezone

from loguru import logger
from sqlalchemy import select

from app.config import get_settings
from app.database import async_session
from app.models.agent import Agent
from app.models.llm import LLMModel
from app.models.task import Task, TaskLog

settings = get_settings()


async def execute_task(task_id: uuid.UUID, agent_id: uuid.UUID) -> None:
    """Execute a task using the agent's configured LLM with full context.

    Uses the same context as chat dialog: build_agent_context for system prompt,
    agent tools for tool-calling, and a multi-round tool loop.

    Flow:
      - todo tasks: pending → doing → done
      - supervision tasks: pending → doing → pending (stays active, just logs result)
    """
    logger.info(f"[TaskExec] Starting task {task_id} for agent {agent_id}")

    # Step 1: Mark as doing
    async with async_session() as db:
        result = await db.execute(select(Task).where(Task.id == task_id))
        task = result.scalar_one_or_none()
        if not task:
            logger.warning(f"[TaskExec] Task {task_id} not found")
            return

        task.status = "doing"
        db.add(TaskLog(task_id=task_id, content="🤖 开始执行任务..."))
        await db.commit()
        task_title = task.title
        task_description = task.description or ""
        task_type = task.type  # 'todo' or 'supervision'
        supervision_target = task.supervision_target_name or ""

    # Step 2: Load agent + model
    async with async_session() as db:
        agent_result = await db.execute(select(Agent).where(Agent.id == agent_id))
        agent = agent_result.scalar_one_or_none()
        if not agent:
            await _log_error(task_id, "数字员工未找到")
            if task_type == 'supervision':
                await _restore_supervision_status(task_id)
            return

        model_id = agent.primary_model_id or agent.fallback_model_id
        if not model_id:
            await _log_error(task_id, f"{agent.name} 未配置 LLM 模型，无法执行任务")
            if task_type == 'supervision':
                await _restore_supervision_status(task_id)
            return

        model_result = await db.execute(
            select(LLMModel).where(LLMModel.id == model_id, LLMModel.tenant_id == agent.tenant_id)
        )
        model = model_result.scalar_one_or_none()
        if not model:
            await _log_error(task_id, "配置的模型不存在")
            if task_type == 'supervision':
                await _restore_supervision_status(task_id)
            return

        agent_name = agent.name
        creator_id = agent.creator_id

    # Step 3: Build full agent context (same as chat dialog)
    from app.services.agent_context import build_agent_context
    static_prompt, dynamic_prompt = await build_agent_context(agent_id, agent_name, agent.role_description or "")

    # Add task-execution-specific instructions
    task_addendum = """

## Task Execution Mode

You are now in TASK EXECUTION MODE (not a conversation). A task has been assigned to you.
- Focus on completing the task as thoroughly as possible.
- Break down complex tasks into steps and execute each step.
- Use your tools actively to gather information, send messages, read/write files, etc.
- Provide a detailed execution report at the end.
- If the task involves contacting someone, use `send_feishu_message` to reach them.
- If the task requires data or information, use your tools to fetch it.
- Do NOT ask the user follow-up questions — take initiative and complete the task autonomously.
"""
    dynamic_prompt += task_addendum

    # Build user prompt
    if task_type == 'supervision':
        user_prompt = f"[督办任务] {task_title}"
        if task_description:
            user_prompt += f"\n任务描述: {task_description}"
        if supervision_target:
            user_prompt += f"\n督办对象: {supervision_target}"
        user_prompt += "\n\n请执行此督办任务：联系督办对象，了解进展，并汇报结果。"
    else:
        user_prompt = f"[任务执行] {task_title}"
        if task_description:
            user_prompt += f"\n任务描述: {task_description}"
        user_prompt += "\n\n请认真完成此任务，给出详细的执行结果。"

    # Step 4: Call LLM with tool loop
    from app.services.llm_utils import create_llm_client, get_max_tokens, LLMMessage, LLMError, get_model_api_key

    messages = [
        LLMMessage(role="system", content=static_prompt, dynamic_content=dynamic_prompt),
        LLMMessage(role="user", content=user_prompt),
    ]

    # Normalize base_url
    if not model.base_url:
        await _log_error(task_id, f"未配置 {model.provider} 的 API 地址")
        if task_type == 'supervision':
            await _restore_supervision_status(task_id)
        return

    # Create unified LLM client
    try:
        client = create_llm_client(
            provider=model.provider,
            api_key=get_model_api_key(model),
            model=model.model,
            base_url=model.base_url,
            timeout=float(getattr(model, 'request_timeout', None) or 1200.0),
        )
    except Exception as e:
        await _log_error(task_id, f"创建 LLM 客户端失败: {e}")
        if task_type == 'supervision':
            await _restore_supervision_status(task_id)
        return

    # Load tools (same as chat dialog)
    from app.services.agent_tools import execute_tool, get_agent_tools_for_llm
    tools_for_llm = await get_agent_tools_for_llm(agent_id)

    try:
        logger.info(f"[TaskExec] Calling LLM with tools for task: {task_title}")
        reply = ""

        # Tool-calling loop (max 50 rounds for task execution)
        for round_i in range(50):
            try:
                response = await client.complete(
                    messages=messages,
                    tools=tools_for_llm if tools_for_llm else None,
                    temperature=model.temperature,
                    max_tokens=get_max_tokens(model.provider, model.model, getattr(model, 'max_output_tokens', None)),
                )
            except LLMError as e:
                await _log_error(task_id, f"LLM 错误: {e}")
                if task_type == 'supervision':
                    await _restore_supervision_status(task_id)
                return
            except Exception as e:
                await _log_error(task_id, f"调用模型失败: {str(e)[:200]}")
                if task_type == 'supervision':
                    await _restore_supervision_status(task_id)
                return

            if response.tool_calls:
                # Add assistant message with tool calls
                messages.append(LLMMessage(
                    role="assistant",
                    content=response.content or None,
                    tool_calls=[{
                        "id": tc["id"],
                        "type": "function",
                        "function": tc["function"],
                    } for tc in response.tool_calls],
                    reasoning_content=response.reasoning_content,
                ))

                for tc in response.tool_calls:
                    fn = tc["function"]
                    tool_name = fn["name"]
                    raw_args = fn.get("arguments", "{}")
                    logger.info(f"[TaskExec] Round {round_i+1} calling tool: {tool_name}({json.dumps(raw_args, ensure_ascii=False)[:100]})")
                    try:
                        args = json.loads(raw_args) if raw_args else {}
                    except Exception:
                        args = {}

                    tool_result = await execute_tool(tool_name, args, agent_id, creator_id)
                    messages.append(LLMMessage(
                        role="tool",
                        tool_call_id=tc["id"],
                        content=str(tool_result),
                    ))
            else:
                reply = response.content or ""
                break
        else:
            reply = "(已达到最大工具调用轮数)"

        await client.close()
        logger.info(f"[TaskExec] LLM reply: {reply[:80]}")
    except Exception as e:
        error_msg = str(e) or repr(e)
        logger.error(f"[TaskExec] Error: {error_msg}")
        await _log_error(task_id, f"执行出错: {error_msg[:150]}")
        if task_type == 'supervision':
            await _restore_supervision_status(task_id)
        return

    # Step 5: Save result and update status
    async with async_session() as db:
        result = await db.execute(select(Task).where(Task.id == task_id))
        task = result.scalar_one_or_none()
        if task:
            if task_type == 'supervision':
                # Supervision tasks stay active; just log the result
                task.status = "pending"
                db.add(TaskLog(task_id=task_id, content=f"✅ 督办执行完成\n\n{reply}"))
            else:
                task.status = "done"
                task.completed_at = datetime.now(timezone.utc)
                db.add(TaskLog(task_id=task_id, content=f"✅ 任务完成\n\n{reply}"))
            await db.commit()
            logger.info(f"[TaskExec] Task {task_id} {'logged' if task_type == 'supervision' else 'completed'}!")

    # Log activity
    from app.services.activity_logger import log_activity
    await log_activity(
        agent_id, "task_updated",
        f"{'督办' if task_type == 'supervision' else '任务'}执行: {task_title[:60]}",
        detail={"task_id": str(task_id), "task_type": task_type, "title": task_title, "reply": reply[:500]},
        related_id=task_id,
    )


async def _log_error(task_id: uuid.UUID, message: str) -> None:
    """Add an error log to the task."""
    logger.error(f"[TaskExec] Error for {task_id}: {message}")
    async with async_session() as db:
        db.add(TaskLog(task_id=task_id, content=f"❌ {message}"))
        await db.commit()


async def _restore_supervision_status(task_id: uuid.UUID) -> None:
    """Restore supervision task status back to pending after a failed execution."""
    async with async_session() as db:
        result = await db.execute(select(Task).where(Task.id == task_id))
        task = result.scalar_one_or_none()
        if task and task.status == "doing":
            task.status = "pending"
            await db.commit()
