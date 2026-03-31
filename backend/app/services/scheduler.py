"""Lightweight asyncio scheduler for agent cron jobs.

Runs as a background task inside the FastAPI process.
Every 30 seconds, checks for schedules whose next_run_at <= now
and executes them by calling the LLM with the schedule's instruction.
"""

import asyncio
import json
import uuid
from datetime import datetime, timezone

from croniter import croniter
from loguru import logger
from sqlalchemy import select, update


def compute_next_run(cron_expr: str, after: datetime | None = None) -> datetime | None:
    """Compute the next run time from a cron expression."""
    try:
        base = after or datetime.now(timezone.utc)
        cron = croniter(cron_expr, base)
        return cron.get_next(datetime).replace(tzinfo=timezone.utc)
    except Exception as e:
        logger.error(f"Invalid cron expression '{cron_expr}': {e}")
        return None


async def _execute_schedule(schedule_id: uuid.UUID, agent_id: uuid.UUID, instruction: str):
    """Execute a single schedule by calling the LLM with the instruction."""
    try:
        from app.database import async_session
        from app.models.agent import Agent
        from app.models.llm import LLMModel

        async with async_session() as db:
            # Load agent + model
            result = await db.execute(select(Agent).where(Agent.id == agent_id))
            agent = result.scalar_one_or_none()
            if not agent:
                logger.warning(f"Schedule {schedule_id}: agent {agent_id} not found")
                return

            if agent.status != "running":
                logger.info(f"Schedule {schedule_id}: agent {agent.name} not running, skipping")
                return

            from app.core.permissions import is_agent_expired
            if is_agent_expired(agent):
                logger.info(f"Schedule {schedule_id}: agent {agent.name} has expired, skipping")
                return

            model_id = agent.primary_model_id or agent.fallback_model_id
            if not model_id:
                logger.warning(f"Schedule {schedule_id}: agent {agent.name} has no LLM model")
                return

            model_result = await db.execute(select(LLMModel).where(LLMModel.id == model_id))
            model = model_result.scalar_one_or_none()
            if not model:
                logger.warning(f"Schedule {schedule_id}: LLM model {model_id} not found")
                return

            # Build context and call LLM
            from app.services.agent_context import build_agent_context
            from app.services.agent_tools import execute_tool, get_agent_tools_for_llm
            from app.services.llm_utils import create_llm_client, get_max_tokens, LLMMessage, LLMError

            static_prompt, dynamic_prompt = await build_agent_context(agent_id, agent.name, agent.role_description or "")

            messages = [
                LLMMessage(role="system", content=static_prompt, dynamic_content=dynamic_prompt),
                LLMMessage(role="user", content=f"[自动调度任务] {instruction}"),
            ]

            # Load tools dynamically from DB (respects per-agent config and MCP tools)
            tools_for_llm = await get_agent_tools_for_llm(agent_id)

            # Create unified LLM client
            try:
                client = create_llm_client(
                    provider=model.provider,
                    api_key=model.api_key_encrypted,
                    model=model.model,
                    base_url=model.base_url,
                    timeout=120.0,
                )
            except Exception as e:
                logger.error(f"Schedule {schedule_id}: Failed to create LLM client: {e}")
                return

            # Tool-calling loop (max 50 rounds for scheduled tasks)
            reply = ""
            for round_i in range(50):
                try:
                    response = await client.complete(
                        messages=messages,
                        tools=tools_for_llm if tools_for_llm else None,
                        temperature=model.temperature,
                        max_tokens=get_max_tokens(model.provider, model.model, getattr(model, 'max_output_tokens', None)),
                    )
                except LLMError as e:
                    logger.error(f"Schedule {schedule_id}: LLM error: {e}")
                    reply = f"(LLM 错误: {e})"
                    break
                except Exception as e:
                    logger.error(f"Schedule {schedule_id}: LLM call error: {e}")
                    reply = f"(LLM 调用异常: {str(e)[:200]})"
                    break

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

                    # Tools that require arguments — if LLM sends empty args, skip and ask to retry
                    _TOOLS_REQUIRING_ARGS = {
                        "write_file", "read_file", "delete_file", "read_document",
                        "send_message_to_agent", "send_feishu_message", "send_email",
                        "web_search", "jina_search", "jina_read",
                    }

                    for tc in response.tool_calls:
                        fn = tc["function"]
                        tool_name = fn["name"]
                        raw_args = fn.get("arguments", "{}")
                        logger.info(f"[Scheduler] Raw arguments for {tool_name} (len={len(raw_args) if raw_args else 0}): {repr(raw_args[:300]) if raw_args else 'None'}")
                        try:
                            args = json.loads(raw_args) if raw_args else {}
                        except json.JSONDecodeError as je:
                            logger.warning(f"[Scheduler] JSON parse failed for {tool_name}: {je}. Raw: {repr(raw_args[:200])}")
                            args = {}

                        # Guard: if a tool that requires arguments received empty args,
                        # return an error to LLM instead of executing
                        if not args and tool_name in _TOOLS_REQUIRING_ARGS:
                            logger.warning(f"[Scheduler] Empty arguments for {tool_name}, asking LLM to retry")
                            messages.append(LLMMessage(
                                role="tool",
                                tool_call_id=tc["id"],
                                content=f"Error: {tool_name} was called with empty arguments. You must provide the required parameters. Please retry with the correct arguments.",
                            ))
                            continue

                        tool_result = await execute_tool(fn["name"], args, agent_id, agent.creator_id)
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

            # Log activity
            from app.services.activity_logger import log_activity
            await log_activity(
                agent_id, "schedule_run",
                f"定时任务执行: {instruction[:60]}",
                detail={"schedule_id": str(schedule_id), "instruction": instruction, "reply": reply[:500]},
            )

            logger.info(f"Schedule {schedule_id} executed for agent {agent.name}: {reply[:80]}")

    except Exception as e:
        logger.error(f"Schedule {schedule_id} execution error: {e}", exc_info=True)


async def _tick():
    """One scheduler tick: find and execute due schedules."""
    from app.database import async_session
    from app.models.schedule import AgentSchedule
    from app.services.audit_logger import write_audit_log

    now = datetime.now(timezone.utc)

    try:
        async with async_session() as db:
            result = await db.execute(
                select(AgentSchedule).where(
                    AgentSchedule.is_enabled == True,
                    AgentSchedule.next_run_at <= now,
                )
            )
            due_schedules = result.scalars().all()

            if due_schedules:
                await write_audit_log("schedule_tick", {"due_count": len(due_schedules)})

            for sched in due_schedules:
                # Update run tracking immediately
                next_run = compute_next_run(sched.cron_expr, now)
                sched.last_run_at = now
                sched.next_run_at = next_run
                sched.run_count = (sched.run_count or 0) + 1
                await db.commit()

                await write_audit_log(
                    "schedule_fire",
                    {"schedule_id": str(sched.id), "name": sched.name, "instruction": sched.instruction[:100], "next_run": str(next_run)},
                    agent_id=sched.agent_id,
                )

                # Fire execution in background (don't block ticker)
                asyncio.create_task(
                    _execute_schedule(sched.id, sched.agent_id, sched.instruction)
                )
                logger.info(f"Triggered schedule '{sched.name}' (next: {next_run})")

    except Exception as e:
        logger.error(f"Scheduler tick error: {e}", exc_info=True)
        await write_audit_log("schedule_error", {"error": str(e)[:300]})


async def start_scheduler():
    """Start the background scheduler loop. Call from FastAPI startup."""
    logger.info("🕐 Agent scheduler started (30s interval)")
    while True:
        await _tick()
        await asyncio.sleep(30)
