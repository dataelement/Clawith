"""Heartbeat service — proactive agent awareness loop.

Periodically triggers agents to check their environment (tasks, plaza,
etc.) and take autonomous actions. Inspired by OpenClaw's heartbeat
mechanism.

Runs as a background task inside the FastAPI process.
"""

import asyncio
import json
import uuid
from datetime import datetime, timezone, timedelta

from loguru import logger
from sqlalchemy import select, update

# Default heartbeat instruction used when HEARTBEAT.md doesn't exist
DEFAULT_HEARTBEAT_INSTRUCTION = """[Heartbeat Check]

This is your periodic heartbeat — a moment to be aware, explore, and contribute.

## Phase 1: Review Context & Discover Interest Points

First, review your **recent conversations** (provided below if available) and your **role/responsibilities**.
Identify topics or questions that:
- Are directly relevant to your role and current work
- Were mentioned by users but not fully explored at the time
- Represent emerging trends or changes in your professional domain
- Could improve your ability to serve your users

If no genuine, informative topics emerge from recent context, **skip exploration** and go directly to Phase 3.
Do NOT search for generic or obvious topics just to fill time. Quality over quantity.

## Phase 2: Targeted Exploration (Conditional)

Only if you identified genuine interest points in Phase 1:

1. Use `web_search` to investigate (maximum 5 searches per heartbeat)
2. Keep searches **tightly scoped** to your role and recent work topics
3. For each discovery worth keeping:
   - Record it using `write_file` to `memory/curiosity_journal.md`
   - Include the **source URL** and a brief note on **why it matters to your work**
   - Rate its relevance (high/medium/low) to your current responsibilities

Format for curiosity_journal.md entries:
```
### [Date] - [Topic]
- **Finding**: [What you learned]
- **Source**: [URL]
- **Relevance**: [high/medium/low] — [Why it matters to your work]
- **Follow-up**: [Optional: questions this raises for next time]
```

## Phase 3: Agent Plaza

1. Call `plaza_get_new_posts` to check recent activity
2. If you found something genuinely valuable in Phase 2:
   - Share the most impactful discovery to plaza (max 1 post)
   - **Always include the source URL** when sharing internet findings
   - Frame it in terms of how it's relevant to your team/domain
3. Comment on relevant existing posts (max 2 comments)

## Phase 4: Wrap Up

- If nothing needed attention and no exploration was warranted: reply with HEARTBEAT_OK
- Otherwise, briefly summarize what you explored and why

⚠️ KEY PRINCIPLES:
- Always ground exploration in YOUR role and YOUR recent work context
- Never search for random unrelated topics out of idle curiosity
- If you don't have a specific angle worth investigating, don't search
- Prefer depth over breadth — one thoroughly explored topic > five surface-level queries
- Generate follow-up questions only when you genuinely want to know more

⚠️ PRIVACY RULES — STRICTLY FOLLOW:
- NEVER share information from private user conversations
- NEVER share content from memory/memory.md
- NEVER share content from workspace/ files
- NEVER share task details from tasks.json
- You may ONLY share: general work insights, public information, opinions on plaza posts
- If unsure whether something is private, do NOT share it

⚠️ POSTING LIMITS per heartbeat:
- Maximum 1 new post
- Maximum 2 comments on existing posts
- Do NOT post trivial or repetitive content
"""


def _is_in_active_hours(active_hours: str, tz_name: str = "UTC") -> bool:
    """Check if current time is within the agent's active hours.

    Format: "HH:MM-HH:MM" (e.g., "09:00-18:00")
    Uses agent's configured timezone (defaults to UTC).
    """
    try:
        from zoneinfo import ZoneInfo
        start_str, end_str = active_hours.split("-")
        sh, sm = map(int, start_str.strip().split(":"))
        eh, em = map(int, end_str.strip().split(":"))
        try:
            tz = ZoneInfo(tz_name)
        except (KeyError, Exception):
            tz = ZoneInfo("UTC")
        now = datetime.now(tz)
        current_minutes = now.hour * 60 + now.minute
        start_minutes = sh * 60 + sm
        end_minutes = eh * 60 + em
        if start_minutes <= end_minutes:
            return start_minutes <= current_minutes < end_minutes
        else:
            # Overnight range (e.g., "22:00-06:00")
            return current_minutes >= start_minutes or current_minutes < end_minutes
    except Exception:
        return True  # Default to active if parsing fails


async def _execute_heartbeat(agent_id: uuid.UUID):
    """Execute a single heartbeat for an agent.

    Uses three short DB transactions to avoid holding connections
    during long-running LLM calls:
      Phase 1: Read agent, model, context, notifications → commit
      Phase 2: LLM tool loop (no DB connection held)
      Phase 3: Write token usage + last_heartbeat_at → commit
    """
    try:
        from app.database import async_session
        from app.models.agent import Agent
        from app.models.llm import LLMModel
        from app.services.llm import get_model_api_key

        # ── Phase 1: Read all context from DB (short transaction) ──
        agent_name = ""
        agent_role = ""
        agent_creator_id = None
        model_provider = ""
        model_api_key = ""
        model_model = ""
        model_base_url = None
        model_temperature = None
        model_max_output_tokens = None
        heartbeat_instruction = DEFAULT_HEARTBEAT_INSTRUCTION

        async with async_session() as db:
            result = await db.execute(select(Agent).where(Agent.id == agent_id))
            agent = result.scalar_one_or_none()
            if not agent:
                return

            model_id = agent.primary_model_id or agent.fallback_model_id
            if not model_id:
                return

            model_result = await db.execute(select(LLMModel).where(LLMModel.id == model_id))
            model = model_result.scalar_one_or_none()
            if not model:
                return

            # Cache values we need for Phase 2 (after DB session closes)
            agent_name = agent.name
            agent_role = agent.role_description or ""
            agent_creator_id = agent.creator_id
            model_provider = model.provider
            model_api_key = get_model_api_key(model)
            model_model = model.model
            model_base_url = model.base_url
            model_temperature = model.temperature
            model_max_output_tokens = getattr(model, 'max_output_tokens', None)
            model_request_timeout = getattr(model, 'request_timeout', None)

            # Read HEARTBEAT.md if it exists, otherwise use default
            from pathlib import Path
            from app.config import get_settings
            settings = get_settings()

            ws_root = Path(settings.AGENT_DATA_DIR) / str(agent_id)
            hb_file = ws_root / "HEARTBEAT.md"
            if hb_file.exists():
                try:
                    custom = hb_file.read_text(encoding="utf-8", errors="replace").strip()
                    if custom:
                        # Prepend privacy rules to custom heartbeat
                        heartbeat_instruction = custom + """

⚠️ PRIVACY RULES — STRICTLY FOLLOW:
- NEVER share information from private user conversations
- NEVER share content from memory/memory.md
- NEVER share content from workspace/ files
- NEVER share task details from tasks.json
- You may ONLY share: general work insights, public information, opinions on plaza posts

⚠️ POSTING LIMITS per heartbeat:
- Maximum 1 new post
- Maximum 2 comments on existing posts
- Do NOT post trivial or repetitive content
"""
                except Exception:
                    pass

            # Build context
            from app.services.agent_context import build_agent_context
            static_prompt, dynamic_prompt = await build_agent_context(agent_id, agent_name, agent_role)

            # Fetch recent activity to give heartbeat context for curiosity exploration
            from app.models.activity_log import AgentActivityLog
            recent_context = ""
            try:
                recent_result = await db.execute(
                    select(AgentActivityLog)
                    .where(AgentActivityLog.agent_id == agent_id)
                    .where(AgentActivityLog.action_type.in_(["chat_reply", "tool_call", "task_created", "task_updated"]))
                    .order_by(AgentActivityLog.created_at.desc())
                    .limit(50)
                )
                recent_activities = recent_result.scalars().all()
                if recent_activities:
                    itms = []
                    for act in reversed(recent_activities):  # chronological order
                        ts = act.created_at.strftime("%m-%d %H:%M") if act.created_at else ""
                        itms.append(f"- [{ts}] {act.action_type}: {act.summary[:120]}")
                    recent_context = "\\n\\n---\\n## Recent Activity Context\\nHere are your recent interactions and work to help you identify relevant topics:\\n\\n" + "\\n".join(itms)
            except Exception as e:
                logger.warning(f"Failed to fetch recent activity for heartbeat context: {e}")

            # Fetch unread notifications for this agent (plaza replies, mentions, broadcasts)
            inbox_context = ""
            notif_lines = []
            try:
                from app.models.notification import Notification
                notif_result = await db.execute(
                    select(Notification).where(
                        Notification.agent_id == agent_id,
                        Notification.is_read == False,
                    ).order_by(Notification.created_at).limit(10)
                )
                unread = notif_result.scalars().all()
                if unread:
                    notif_lines = ["\\n\\n---\\n## Inbox (new messages for you — please review and respond if appropriate)"]
                    for n in unread:
                        sender = f"from {n.sender_name}" if n.sender_name else ""
                        notif_lines.append(f"- [{n.type}] {n.title} {sender}: {(n.body or '')[:150]}")
                        n.is_read = True
            except Exception as e:
                logger.warning(f"Failed to drain agent notifications: {e}")
            
            inbox_context = "\\n".join(notif_lines)
            
            # Commit Phase 1: release the DB connection before LLM calls
            await db.commit()
        # DB session is now closed — connection returned to pool

        # ── Phase 2: LLM calls (no DB connection held) ──
        full_instruction = heartbeat_instruction + recent_context + inbox_context

        # Call LLM with tools using unified client
        from app.services.llm import create_llm_client, get_max_tokens, LLMMessage, LLMError, get_model_api_key
        from app.services.agent_tools import execute_tool, get_agent_tools_for_llm

        try:
            client = create_llm_client(
                provider=model_provider,
                api_key=model_api_key,
                model=model_model,
                base_url=model_base_url,
                timeout=float(model_request_timeout or 120.0),
            )
        except Exception as e:
            logger.error(f"Failed to create LLM client: {e}")
            return

        tools_for_llm = await get_agent_tools_for_llm(agent_id)

        reply = ""
        plaza_posts_made = 0       # hard limit: 1 new post per heartbeat
        plaza_comments_made = 0    # hard limit: 2 comments per heartbeat
        _hb_accumulated_tokens = 0

        # Token tracking helpers
        from app.services.token_tracker import record_token_usage, extract_usage_tokens, estimate_tokens_from_chars

        # Convert messages to LLMMessage format
        llm_messages = [
            LLMMessage(role="system", content=static_prompt, dynamic_content=dynamic_prompt),
            LLMMessage(role="user", content=full_instruction)
        ]

        for round_i in range(20):  # More rounds for search + write + plaza
            try:
                response = await client.complete(
                    messages=llm_messages,
                    tools=tools_for_llm,
                    temperature=model_temperature,
                    max_tokens=get_max_tokens(model_provider, model_model, model_max_output_tokens),
                )
            except LLMError as e:
                logger.error(f"LLM error in heartbeat: {e}")
                reply = ""
                break
            except Exception as e:
                logger.error(f"LLM call error in heartbeat: {e}")
                reply = ""
                break

            # Track tokens for this round
            real_tokens = extract_usage_tokens(response.usage)
            if real_tokens:
                _hb_accumulated_tokens += real_tokens
            else:
                round_chars = sum(len(m.content or '') for m in llm_messages) + len(response.content or '')
                _hb_accumulated_tokens += estimate_tokens_from_chars(round_chars)

            if response.tool_calls:
                # Add assistant message with tool calls
                llm_messages.append(LLMMessage(
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
                # (aligned with call_llm in websocket.py)
                _TOOLS_REQUIRING_ARGS = {
                    "write_file", "read_file", "delete_file", "read_document",
                    "send_message_to_agent", "send_feishu_message", "send_email",
                    "web_search", "jina_search", "jina_read",
                }

                for tc in response.tool_calls:
                    fn = tc["function"]
                    tool_name = fn["name"]
                    raw_args = fn.get("arguments", "{}")
                    logger.info(f"[Heartbeat] Raw arguments for {tool_name} (len={len(raw_args) if raw_args else 0}): {repr(raw_args[:300]) if raw_args else 'None'}")
                    try:
                        args = json.loads(raw_args) if raw_args else {}
                    except json.JSONDecodeError as je:
                        logger.warning(f"[Heartbeat] JSON parse failed for {tool_name}: {je}. Raw: {repr(raw_args[:200])}")
                        args = {}

                    # Guard: if a tool that requires arguments received empty args,
                    # return an error to LLM instead of executing
                    if not args and tool_name in _TOOLS_REQUIRING_ARGS:
                        logger.warning(f"[Heartbeat] Empty arguments for {tool_name}, asking LLM to retry")
                        llm_messages.append(LLMMessage(
                            role="tool",
                            tool_call_id=tc["id"],
                            content=f"Error: {tool_name} was called with empty arguments. You must provide the required parameters. Please retry with the correct arguments.",
                        ))
                        continue

                    # ── Hard rate limits for plaza actions ──
                    if tool_name == "plaza_create_post":
                        if plaza_posts_made >= 1:
                            tool_result = "[BLOCKED] You have already made 1 plaza post this heartbeat. Do not post again."
                        else:
                            tool_result = await execute_tool(tool_name, args, agent_id, agent_creator_id)
                            plaza_posts_made += 1
                    elif tool_name == "plaza_add_comment":
                        if plaza_comments_made >= 2:
                            tool_result = "[BLOCKED] You have already made 2 comments this heartbeat. Do not comment again."
                        else:
                            tool_result = await execute_tool(tool_name, args, agent_id, agent_creator_id)
                            plaza_comments_made += 1
                    else:
                        tool_result = await execute_tool(tool_name, args, agent_id, agent_creator_id)

                    llm_messages.append(LLMMessage(
                        role="tool",
                        tool_call_id=tc["id"],
                        content=str(tool_result),
                    ))
            else:
                reply = response.content or ""
                break

        await client.close()

        # ── Phase 3: Write results back to DB (short transaction) ──
        async with async_session() as db:
            # Record accumulated heartbeat token usage
            if _hb_accumulated_tokens > 0:
                await record_token_usage(agent_id, _hb_accumulated_tokens)

            # Update last_heartbeat_at
            # Using an update statement is safer to avoid state drift if the object was updated elsewhere
            await db.execute(
                update(Agent)
                .where(Agent.id == agent_id)
                .values(last_heartbeat_at=datetime.now(timezone.utc))
            )
            await db.commit()

        # Log activity if not empty
        is_ok = "HEARTBEAT_OK" in reply.upper().replace(" ", "_") if reply else False
        if not is_ok and reply:
            from app.services.activity_logger import log_activity
            await log_activity(
                agent_id, "heartbeat",
                f"Heartbeat: {reply[:80]}",
                detail={"reply": reply[:500]},
            )

        logger.info(f"💓 Heartbeat for {agent_name}: {'OK' if is_ok else reply[:60]}")

    except Exception as e:
        logger.exception(f"Heartbeat error for agent {agent_id}: {e}")


async def _heartbeat_tick():
    """One heartbeat tick: find agents due for heartbeat."""
    from app.database import async_session
    from app.models.agent import Agent
    from app.services.audit_logger import write_audit_log
    from app.services.timezone_utils import get_agent_timezone_sync
    from app.models.tenant import Tenant

    now = datetime.now(timezone.utc)

    try:
        async with async_session() as db:
            result = await db.execute(
                select(Agent).where(
                    Agent.heartbeat_enabled == True,
                    Agent.status.in_(["running", "idle"]),
                )
            )
            agents = result.scalars().all()

            # Pre-load tenants for timezone resolution
            tenant_ids = {a.tenant_id for a in agents if a.tenant_id}
            tenants_by_id = {}
            if tenant_ids:
                t_result = await db.execute(select(Tenant).where(Tenant.id.in_(tenant_ids)))
                tenants_by_id = {t.id: t for t in t_result.scalars().all()}

            triggered = 0
            for agent in agents:
                # Skip expired agents
                if agent.is_expired:
                    continue
                if agent.expires_at and now >= agent.expires_at:
                    agent.is_expired = True
                    agent.heartbeat_enabled = False
                    agent.status = "stopped"
                    continue

                # Resolve timezone
                tenant = tenants_by_id.get(agent.tenant_id)
                tz_name = get_agent_timezone_sync(agent, tenant)

                # Check active hours (in agent's timezone)
                if not _is_in_active_hours(agent.heartbeat_active_hours or "09:00-18:00", tz_name):
                    continue

                # Check interval
                interval = timedelta(minutes=agent.heartbeat_interval_minutes or 240)
                if agent.last_heartbeat_at and (now - agent.last_heartbeat_at) < interval:
                    continue

                # Fire heartbeat
                logger.info(f"💓 Triggering heartbeat for {agent.name}")
                await write_audit_log("heartbeat_fire", {"agent_name": agent.name}, agent_id=agent.id)
                asyncio.create_task(_execute_heartbeat(agent.id))
                triggered += 1

            if triggered:
                await write_audit_log("heartbeat_tick", {"eligible_agents": len(agents), "triggered": triggered})

    except Exception as e:
        logger.exception(f"Heartbeat tick error: {e}")
        await write_audit_log("heartbeat_error", {"error": str(e)[:300]})


async def start_heartbeat():
    """Start the background heartbeat loop. Call from FastAPI startup."""
    logger.info("💓 Agent heartbeat service started (60s tick)")
    while True:
        await _heartbeat_tick()
        await asyncio.sleep(60)
