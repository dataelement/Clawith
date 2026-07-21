"""Gateway API for OpenClaw agent communication.

OpenClaw agents authenticate via X-Api-Key header and use these endpoints
to poll for messages, report results, send messages, and send heartbeat pings.
"""

import hashlib
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Header, HTTPException, Depends
from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.core.permissions import (
    can_auto_contact_company_agent,
    evaluate_agent_relationship_status,
    evaluate_human_relationship_status,
)
from app.models.agent import Agent
from app.models.gateway_message import GatewayMessage
from app.models.user import User
from app.services.agent_runtime.a2a_runtime import (
    A2ARuntimeError,
    complete_gateway_a2a_runtime,
    enqueue_gateway_a2a_runtime,
)
from app.schemas.schemas import (
    GatewayPollResponse, GatewayMessageOut, GatewayReportRequest,
    GatewayHistoryItem, GatewayRelationshipItem, GatewaySendMessageRequest,
)

router = APIRouter(prefix="/gateway", tags=["gateway"])


def _hash_key(key: str) -> str:
    """Hash an API key for storage."""
    return hashlib.sha256(key.encode()).hexdigest()


async def _get_agent_by_key(api_key: str, db: AsyncSession) -> Agent:
    """Authenticate an OpenClaw agent by its API key."""
    # First try plaintext (new behavior)
    result = await db.execute(
        select(Agent).where(
            Agent.api_key_hash == api_key,
            Agent.agent_type == "openclaw",
        )
    )
    agent = result.scalar_one_or_none()

    # Fallback to hashed (legacy behavior)
    if not agent:
        key_hash = _hash_key(api_key)
        result = await db.execute(
            select(Agent).where(
                Agent.api_key_hash == key_hash,
                Agent.agent_type == "openclaw",
            )
        )
        agent = result.scalar_one_or_none()

    if not agent:
        raise HTTPException(status_code=401, detail="Invalid API key")
    return agent


# ─── Poll for messages ──────────────────────────────────

@router.get("/poll", response_model=GatewayPollResponse)
async def poll_messages(
    x_api_key: str = Header(..., alias="X-Api-Key"),
    db: AsyncSession = Depends(get_db),
):
    """OpenClaw agent polls for pending messages.

    Returns all pending messages and marks them as delivered.
    Also updates openclaw_last_seen for online status tracking.
    """
    logger.info(f"[Gateway] poll called, key_prefix={x_api_key[:8]}...")
    agent = await _get_agent_by_key(x_api_key, db)

    # Update last seen
    agent.openclaw_last_seen = datetime.now(timezone.utc)
    agent.status = "running"

    # Fetch pending messages
    result = await db.execute(
        select(GatewayMessage)
        .where(GatewayMessage.agent_id == agent.id, GatewayMessage.status == "pending")
        .order_by(GatewayMessage.created_at.asc())
    )
    messages = result.scalars().all()

    # Mark as delivered
    now = datetime.now(timezone.utc)
    out = []
    for msg in messages:
        msg.status = "delivered"
        msg.delivered_at = now

        # Resolve sender names
        sender_agent_name = None
        sender_user_name = None
        if msg.sender_agent_id:
            r = await db.execute(select(Agent.name).where(Agent.id == msg.sender_agent_id))
            sender_agent_name = r.scalar_one_or_none()
        if msg.sender_user_id:
            r = await db.execute(select(User.display_name).where(User.id == msg.sender_user_id))
            sender_user_name = r.scalar_one_or_none()

        # Fetch conversation history (last 10 messages) for context
        history = []
        if msg.conversation_id:
            from app.models.audit import ChatMessage
            hist_result = await db.execute(
                select(ChatMessage)
                .where(ChatMessage.conversation_id == msg.conversation_id)
                .order_by(ChatMessage.created_at.desc())
                .limit(10)
            )
            hist_msgs = list(reversed(hist_result.scalars().all()))
            for h in hist_msgs:
                # Resolve sender name for each history message
                h_sender = None
                if h.role == "user" and h.user_id:
                    r = await db.execute(select(User.display_name).where(User.id == h.user_id))
                    h_sender = r.scalar_one_or_none()
                elif h.role == "assistant":
                    h_sender = agent.name
                history.append(GatewayHistoryItem(
                    role=h.role,
                    content=h.content or "",
                    sender_name=h_sender,
                    created_at=h.created_at,
                ))

        out.append(GatewayMessageOut(
            id=msg.id,
            conversation_id=msg.conversation_id,
            sender_agent_name=sender_agent_name,
            sender_user_name=sender_user_name,
            sender_user_id=str(msg.sender_user_id) if msg.sender_user_id else None,
            content=msg.content,
            created_at=msg.created_at,
            history=history,
        ))

    # Fetch legacy relationships for the gateway compatibility payload
    from app.models.org import AgentRelationship, AgentAgentRelationship
    from sqlalchemy.orm import selectinload

    rel_items = []

    # Legacy human relationships (with available channels)
    h_result = await db.execute(
        select(AgentRelationship)
        .where(AgentRelationship.agent_id == agent.id)
        .options(selectinload(AgentRelationship.member))
    )
    for r in h_result.scalars().all():
        status_info = await evaluate_human_relationship_status(db, r, source_agent=agent)
        if r.member and status_info["access_status"] == "active":
            channels = []
            if getattr(r.member, 'external_id', None) or getattr(r.member, 'open_id', None):
                channels.append("feishu")
            if getattr(r.member, 'email', None):
                channels.append("email")
            rel_items.append(GatewayRelationshipItem(
                name=r.member.name,
                type="human",
                role=r.relation,
                description=r.description or None,
                channels=channels,
            ))

    # Legacy agent-to-agent relationships
    a_result = await db.execute(
        select(AgentAgentRelationship)
        .where(AgentAgentRelationship.agent_id == agent.id)
        .options(selectinload(AgentAgentRelationship.target_agent))
    )
    related_agent_ids = set()
    for r in a_result.scalars().all():
        status_info = await evaluate_agent_relationship_status(db, r)
        if r.target_agent and status_info["access_status"] == "active":
            related_agent_ids.add(r.target_agent.id)
            rel_items.append(GatewayRelationshipItem(
                name=r.target_agent.name,
                type="agent",
                role=r.relation,
                description=r.description or None,
                channels=["agent"],
            ))

    c_result = await db.execute(
        select(Agent)
        .where(
            Agent.tenant_id == agent.tenant_id,
            Agent.id != agent.id,
            Agent.access_mode == "company",
            Agent.status.in_(["running", "idle"]),
        )
        .order_by(Agent.name.asc(), Agent.created_at.asc())
    )
    for candidate in c_result.scalars().all():
        if candidate.id in related_agent_ids:
            continue
        if can_auto_contact_company_agent(agent, candidate):
            rel_items.append(GatewayRelationshipItem(
                name=candidate.name,
                type="agent",
                role="company",
                description=candidate.role_description or None,
                channels=["agent"],
            ))

    await db.commit()
    return GatewayPollResponse(messages=out, relationships=rel_items)


# ─── Report results ─────────────────────────────────────

@router.post("/report")
async def report_result(
    body: GatewayReportRequest,
    x_api_key: str = Header(None, alias="X-Api-Key"),
    db: AsyncSession = Depends(get_db),
):
    """OpenClaw agent reports the result of a processed message."""
    if not x_api_key:
        raise HTTPException(status_code=401, detail="Missing X-Api-Key header")
    logger.info(f"[Gateway] report called, key_prefix={x_api_key[:8]}..., msg_id={body.message_id}")
    agent = await _get_agent_by_key(x_api_key, db)

    result = await db.execute(
        select(GatewayMessage).where(
            GatewayMessage.id == body.message_id,
            GatewayMessage.agent_id == agent.id,
        )
    )
    msg = result.scalar_one_or_none()
    if not msg:
        raise HTTPException(status_code=404, detail="Message not found")

    if msg.status == "completed":
        if msg.result != body.result:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "gateway_result_mismatch",
                    "message": "Message already completed with a different result.",
                },
            )
        return {"status": "ok"}

    msg.status = "completed"
    msg.result = body.result
    msg.completed_at = datetime.now(timezone.utc)

    # Update last seen
    agent.openclaw_last_seen = datetime.now(timezone.utc)

    # Save result as assistant chat message and push via WebSocket
    # (works for both user-originated and agent-to-agent messages)
    if body.result and msg.conversation_id:
        from app.models.audit import ChatMessage
        from app.models.participant import Participant
        # Look up OpenClaw agent's participant_id
        part_r = await db.execute(select(Participant).where(Participant.type == "agent", Participant.ref_id == agent.id))
        participant = part_r.scalar_one_or_none()

        result_message_id = uuid.uuid5(msg.id, "gateway-report-result")
        result_message = await db.get(ChatMessage, result_message_id)
        if result_message is None:
            db.add(
                ChatMessage(
                    id=result_message_id,
                    agent_id=agent.id,
                    user_id=msg.sender_user_id or getattr(agent, "creator_id", agent.id),
                    role="assistant",
                    content=body.result,
                    conversation_id=msg.conversation_id,
                    participant_id=participant.id if participant else None,
                    mentions=[],
                )
            )

    runtime_completion = None
    if body.result and msg.sender_agent_id:
        try:
            runtime_completion = await complete_gateway_a2a_runtime(
                db,
                gateway_message=msg,
                target_agent=agent,
                result=body.result,
            )
        except A2ARuntimeError as exc:
            await db.rollback()
            raise HTTPException(
                status_code=409,
                detail={"code": exc.code, "message": str(exc)},
            ) from exc

        if runtime_completion is None:
            sender_result = await db.execute(
                select(Agent).where(Agent.id == msg.sender_agent_id)
            )
            sender_agent = sender_result.scalar_one_or_none()
            if sender_agent is not None and sender_agent.agent_type == "openclaw":
                reply_id = uuid.uuid5(msg.id, "gateway-report-reply")
                existing_reply = await db.get(GatewayMessage, reply_id)
                if existing_reply is None:
                    db.add(
                        GatewayMessage(
                            id=reply_id,
                            agent_id=sender_agent.id,
                            sender_agent_id=agent.id,
                            content=body.result,
                            status="pending",
                            conversation_id=(
                                msg.conversation_id
                                or f"gw_agent_{sender_agent.id}_{agent.id}"
                            ),
                        )
                    )

    await db.commit()

    # Push to WebSocket if user is connected
    if body.result and msg.conversation_id and msg.sender_user_id:
        try:
            from app.api.websocket import manager
            await manager.send_message(str(agent.id), {
                "type": "done",
                "role": "assistant",
                "content": body.result,
            })
        except Exception:
            pass  # User may have disconnected

    return {"status": "ok"}


# ─── Heartbeat ──────────────────────────────────────────

@router.post("/heartbeat")
async def heartbeat(
    x_api_key: str = Header(..., alias="X-Api-Key"),
    db: AsyncSession = Depends(get_db),
):
    """Pure heartbeat ping — keeps the OpenClaw agent marked as online."""
    agent = await _get_agent_by_key(x_api_key, db)
    agent.openclaw_last_seen = datetime.now(timezone.utc)
    agent.status = "running"
    await db.commit()
    return {"status": "ok", "agent_id": str(agent.id)}


# ─── Send message ───────────────────────────────────────

@router.post("/send-message")
async def send_message(
    body: GatewaySendMessageRequest,
    x_api_key: str = Header(..., alias="X-Api-Key"),
    db: AsyncSession = Depends(get_db),
):
    """OpenClaw agent sends a message to a person or another agent.

    Routes automatically based on target type:
    - Agent target: triggers LLM processing, reply returned via next poll
    - Human target: sends via available channel (feishu, etc.)
    """
    agent = await _get_agent_by_key(x_api_key, db)
    agent.openclaw_last_seen = datetime.now(timezone.utc)

    target_name = body.target.strip()
    content = body.content.strip()
    channel_hint = (body.channel or "").strip().lower()

    # 1. Try to find target as another Agent.
    from app.models.org import AgentAgentRelationship
    from sqlalchemy.orm import selectinload

    target_agent = None
    if not channel_hint or channel_hint == "agent":
        company_result = await db.execute(
            select(Agent).where(
                Agent.name == target_name,
                Agent.tenant_id == agent.tenant_id,
                Agent.id != agent.id,
                Agent.access_mode == "company",
            )
        )
        company_candidate = company_result.scalars().first()
        if company_candidate and can_auto_contact_company_agent(agent, company_candidate):
            target_agent = company_candidate

    rel_result = await db.execute(
        select(AgentAgentRelationship)
        .where(AgentAgentRelationship.agent_id == agent.id)
        .options(selectinload(AgentAgentRelationship.target_agent))
    )
    if not target_agent:
        for rel in rel_result.scalars().all():
            candidate = rel.target_agent
            if not candidate:
                continue
            status_info = await evaluate_agent_relationship_status(db, rel)
            if status_info["access_status"] != "active":
                continue
            if candidate.name.lower() == target_name.lower() or target_name.lower() in candidate.name.lower():
                target_agent = candidate
                break

    logger.info(f"[Gateway] send_message: target='{target_name}', found_agent={target_agent.name if target_agent else None}, agent_type={getattr(target_agent, 'agent_type', None) if target_agent else None}, channel_hint='{channel_hint}'")

    if target_agent and (not channel_hint or channel_hint == "agent"):
        conv_id = f"gw_agent_{agent.id}_{target_agent.id}"

        if getattr(target_agent, 'agent_type', None) == 'openclaw':
            # OpenClaw-to-OpenClaw: write to gateway_messages directly
            gw_msg = GatewayMessage(
                agent_id=target_agent.id,
                sender_agent_id=agent.id,
                content=content,
                status="pending",
                conversation_id=conv_id,
            )
            db.add(gw_msg)
            await db.commit()
            return {
                "status": "accepted",
                "target": target_agent.name,
                "type": "openclaw_agent",
                "message": f"Message sent to {target_agent.name}. Reply will appear in your next poll.",
            }
        else:
            try:
                intake = await enqueue_gateway_a2a_runtime(
                    db,
                    source_agent=agent,
                    target_agent=target_agent,
                    content=content,
                    message_id=body.message_id,
                )
            except A2ARuntimeError as exc:
                await db.rollback()
                raise HTTPException(
                    status_code=409,
                    detail={"code": exc.code, "message": str(exc)},
                ) from exc
            if intake is None:
                await db.rollback()
                raise HTTPException(
                    status_code=503,
                    detail={
                        "code": "runtime_disabled",
                        "message": "Durable Runtime is not enabled for native A2A.",
                    },
                )
            await db.commit()
            return {
                "status": "accepted",
                "target": target_agent.name,
                "type": "agent",
                "message": f"Message sent to {target_agent.name}. Reply will appear in your next poll.",
                "message_id": str(intake.gateway_message_id),
                "run_id": str(intake.target_run_id),
            }

    # 2. Try to find target as a human via the legacy gateway directory payload
    from app.models.org import AgentRelationship
    from sqlalchemy.orm import selectinload

    rel_result = await db.execute(
        select(AgentRelationship)
        .where(AgentRelationship.agent_id == agent.id)
        .options(selectinload(AgentRelationship.member))
    )
    rels = rel_result.scalars().all()

    target_member = None
    for r in rels:
        status_info = await evaluate_human_relationship_status(db, r, source_agent=agent)
        if r.member and status_info["access_status"] == "active" and r.member.name == target_name:
            target_member = r.member
            break
    # Fuzzy match if exact match fails
    if not target_member:
        for r in rels:
            status_info = await evaluate_human_relationship_status(db, r, source_agent=agent)
            if r.member and status_info["access_status"] == "active" and target_name.lower() in r.member.name.lower():
                target_member = r.member
                break

    if not target_member:
        await db.commit()
        raise HTTPException(
            status_code=404,
            detail=f"Target '{target_name}' not found. Check the gateway directory payload returned by poll."
        )

    # Send via feishu if available
    if (target_member.external_id or target_member.open_id) and (not channel_hint or channel_hint == "feishu"):
        from app.models.channel_config import ChannelConfig
        from app.services.feishu_service import feishu_service
        import json as _json

        config_result = await db.execute(
            select(ChannelConfig).where(ChannelConfig.agent_id == agent.id)
        )
        config = config_result.scalar_one_or_none()
        if not config:
            # Try to find any feishu config in the org
            config_result = await db.execute(
                select(ChannelConfig).where(ChannelConfig.channel == "feishu").limit(1)
            )
            config = config_result.scalar_one_or_none()

        if not config:
            await db.commit()
            raise HTTPException(status_code=400, detail="No Feishu channel configured")

        # Extract config values and release connection before Feishu HTTP calls
        _cfg_app_id = config.app_id
        _cfg_app_secret = config.app_secret
        await db.commit()
        await db.close()

        # Prefer user_id (tenant-stable, works across apps), fallback to open_id
        resp = None
        if target_member.external_id:
            resp = await feishu_service.send_message(
                _cfg_app_id, _cfg_app_secret,
                receive_id=target_member.external_id,
                msg_type="text",
                content=_json.dumps({"text": content}, ensure_ascii=False),
                receive_id_type="user_id",
            )
        if (resp is None or resp.get("code") != 0) and target_member.open_id:
            resp = await feishu_service.send_message(
                _cfg_app_id, _cfg_app_secret,
                receive_id=target_member.open_id,
                msg_type="text",
                content=_json.dumps({"text": content}, ensure_ascii=False),
                receive_id_type="open_id",
            )

        if resp and resp.get("code") == 0:
            return {
                "status": "sent",
                "target": target_member.name,
                "type": "human",
                "channel": "feishu",
            }
        else:
            raise HTTPException(
                status_code=502,
                detail=f"Feishu send failed: {resp.get('msg') if resp else 'no ID available'} (code {resp.get('code') if resp else 'N/A'})"
            )

    await db.commit()
    raise HTTPException(
        status_code=400,
        detail=f"No available channel to reach {target_member.name}. feishu_user_id={'yes' if target_member.external_id else 'no'}, feishu_open_id={'yes' if target_member.open_id else 'no'}"
    )


# ─── Setup guide ────────────────────────────────────────

@router.get("/setup-guide/{agent_id}")
async def get_setup_guide(
    agent_id: uuid.UUID,
    x_api_key: str = Header(..., alias="X-Api-Key"),
    accept_language: str | None = Header(None, alias="Accept-Language"),
    db: AsyncSession = Depends(get_db),
):
    """Return the pre-filled Skill file and Heartbeat instruction for this agent."""
    agent = await _get_agent_by_key(x_api_key, db)
    if agent.id != agent_id:
        raise HTTPException(status_code=403, detail="Key does not match this agent")

    # Note: we use the raw key from the header since the agent already authenticated
    base_url = "https://try.clawith.ai"
    is_zh = (accept_language or "").lower().startswith("zh")

    skill_content = f"""请在 OpenClaw Agent 中创建技能文件 skills/clawith_sync.md，内容如下；然后把「使用 clawith_sync 技能检查 Clawith inbox 并处理待办消息」加入 HEARTBEAT.md：

---
name: clawith_sync
description: Sync with Clawith platform — check inbox, submit results, and send messages.
---

# Clawith Sync

## 使用场景
在每次 heartbeat 时检查 Clawith 平台是否有新消息。
你也可以主动给 gateway 通讯录 payload 中返回的精确目标发送消息。

## 操作说明

### 1. 检查 inbox
发起 HTTP GET 请求：
- URL: {base_url}/api/gateway/poll
- Header: X-Api-Key: {x_api_key}

响应中包含 messages 数组。每条消息包括：
- id：消息 ID，回报结果时使用
- content：消息内容
- sender_user_name：发送消息的 Clawith 用户名
- sender_user_id：发送者 ID
- conversation_id：消息所属会话
- history：该会话的历史消息，用于理解上下文

为了兼容旧协议，响应中还包含 relationships 数组。请把它当作 gateway 通讯录 payload，用其中的精确 name 作为发送目标：
- name：人或 Agent 的名称
- type："human" 或 "agent"
- role：旧关系标签，不要把它当作访问规则
- channels：可用通信渠道，例如 ["feishu"] 或 ["agent"]

重要：回复前先阅读 history 理解上下文。不同 sender_user_name 代表不同用户，请按对应用户回复。

### 2. 回报处理结果
每处理完一条消息，发起 HTTP POST 请求：
- URL: {base_url}/api/gateway/report
- Header: X-Api-Key: {x_api_key}
- Header: Content-Type: application/json
- Body: {{"message_id": "<messages 中的 id>", "result": "<你的回复>"}}

### 3. 主动发送消息
如果需要主动联系某个人或 Agent，发起 HTTP POST 请求：
- URL: {base_url}/api/gateway/send-message
- Header: X-Api-Key: {x_api_key}
- Header: Content-Type: application/json
- Body: {{"target": "<gateway 通讯录 payload 中的精确 name>", "content": "<消息内容>"}}

系统会自动选择合适渠道。发给 Agent 时，回复会出现在下一次 poll 中；发给人类成员时，会通过可用渠道投递，例如飞书。
""" if is_zh else f"""---
name: clawith_sync
description: Sync with Clawith platform — check inbox, submit results, and send messages.
---

# Clawith Sync

## When to use
Check for new messages from the Clawith platform during every heartbeat cycle.
You can proactively send messages to exact targets returned in the gateway directory payload.

## Instructions

### 1. Check inbox
Make an HTTP GET request:
- URL: {base_url}/api/gateway/poll
- Header: X-Api-Key: {x_api_key}

The response contains a `messages` array. Each message includes:
- `id` — unique message ID (use this for reporting)
- `content` — the message text
- `sender_user_name` — name of the Clawith user who sent it
- `sender_user_id` — unique ID of the sender
- `conversation_id` — the conversation this message belongs to
- `history` — array of previous messages in this conversation for context

For compatibility, the response also contains a `relationships` array. Treat it as a gateway directory payload for exact target names:
- `name` — the person or agent name
- `type` — "human" or "agent"
- `role` — legacy relationship label; do not use it as an access rule
- `channels` — available communication channels (e.g. ["feishu"], ["agent"])

**IMPORTANT**: Use the `history` array to understand conversation context before replying.
Different `sender_user_name` values mean different people — address them accordingly.

### 2. Report results
For each completed message, make an HTTP POST request:
- URL: {base_url}/api/gateway/report
- Header: X-Api-Key: {x_api_key}
- Header: Content-Type: application/json
- Body: {{"message_id": "<id from the message>", "result": "<your response>"}}

### 3. Send a message to someone
To proactively contact a person or agent, make an HTTP POST request:
- URL: {base_url}/api/gateway/send-message
- Header: X-Api-Key: {x_api_key}
- Header: Content-Type: application/json
- Body: {{"target": "<exact name from the gateway directory payload>", "content": "<your message>"}}

The system auto-detects the best channel. For agents, the reply appears in your next poll.
For humans, the message is delivered via their available channel (e.g. Feishu).
"""

    heartbeat_line = (
        "- 使用 clawith_sync 技能检查 Clawith inbox 并处理待办消息"
        if is_zh
        else "- Check Clawith inbox using the clawith_sync skill and process any pending messages"
    )

    return {
        "skill_filename": "clawith_sync.md",
        "skill_content": skill_content,
        "heartbeat_addition": heartbeat_line,
    }
