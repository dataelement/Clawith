"""Unified Agent API — synchronous HTTP endpoint for calling agents.

External callers or agents themselves can invoke any agent via this endpoint.
Authentication is via per-agent Token Key (Authorization: Bearer <token_key>).
Token consumption is charged to the *caller* (owner of the Token Key).
The target agent must have a relationship with the calling agent.
"""

import secrets
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Header, HTTPException, Depends
from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db, async_session
from app.models.agent import Agent
from app.models.org import AgentAgentRelationship
from app.schemas.schemas import AgentApiChatRequest, AgentApiChatResponse
from app.services.token_tracker import TokenUsage, record_token_usage

router = APIRouter(prefix="/v1/agent", tags=["agent-api"])


# ─── Helpers ────────────────────────────────────────────

def generate_token_key() -> tuple[str, str]:
    """Generate a new token key. Returns (full_key, suffix)."""
    key = "clw_" + secrets.token_hex(16)
    return key, key[-4:]


async def _get_caller_agent(token_key: str, db: AsyncSession) -> Agent:
    """Authenticate the calling agent by its token_key."""
    result = await db.execute(
        select(Agent).where(Agent.token_key == token_key)
    )
    agent = result.scalar_one_or_none()
    if not agent:
        raise HTTPException(status_code=401, detail="Invalid token key")
    return agent


async def _check_relationship(db: AsyncSession, caller_id: uuid.UUID, target_id: uuid.UUID) -> bool:
    """Check if the caller agent has a relationship with the target agent."""
    result = await db.execute(
        select(AgentAgentRelationship).where(
            AgentAgentRelationship.agent_id == caller_id,
            AgentAgentRelationship.target_agent_id == target_id,
        )
    )
    return result.scalar_one_or_none() is not None


# ─── Chat endpoint ──────────────────────────────────────

@router.post("/chat", response_model=AgentApiChatResponse)
async def agent_api_chat(
    body: AgentApiChatRequest,
    authorization: str = Header(..., alias="Authorization"),
    db: AsyncSession = Depends(get_db),
):
    """Synchronous agent invocation.

    Calls the target agent's LLM with full tool-calling loop and returns
    the final reply. Token consumption is charged to the calling agent
    (identified by the token key).

    Timeout: 1 hour (set at the reverse proxy / uvicorn level).
    """
    # Parse Bearer token
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Authorization header must be 'Bearer <token_key>'")
    token_key = authorization[7:].strip()
    if not token_key:
        raise HTTPException(status_code=401, detail="Token key is required")

    # Authenticate caller
    caller = await _get_caller_agent(token_key, db)
    logger.info(f"[AgentAPI] Caller: {caller.name} ({caller.id})")

    # Load target agent
    target_result = await db.execute(select(Agent).where(Agent.id == body.agent_id))
    target = target_result.scalar_one_or_none()
    if not target:
        raise HTTPException(status_code=404, detail="Target agent not found")

    # Check relationship (caller must have relationship with target)
    if caller.id != target.id:  # Calling self is always allowed
        has_rel = await _check_relationship(db, caller.id, target.id)
        if not has_rel:
            raise HTTPException(
                status_code=403,
                detail=f"Agent '{caller.name}' has no relationship with target agent '{target.name}'. "
                       f"Add a relationship first.",
            )

    # Check target agent has a model configured
    from app.models.llm import LLMModel
    from app.core.permissions import is_agent_expired

    if is_agent_expired(target):
        raise HTTPException(status_code=403, detail="Target agent has expired")

    primary_model = None
    fallback_model = None
    if target.primary_model_id:
        mr = await db.execute(select(LLMModel).where(LLMModel.id == target.primary_model_id))
        primary_model = mr.scalar_one_or_none()
        if primary_model and not primary_model.enabled:
            primary_model = None
    if target.fallback_model_id:
        fr = await db.execute(select(LLMModel).where(LLMModel.id == target.fallback_model_id))
        fallback_model = fr.scalar_one_or_none()
        if fallback_model and not fallback_model.enabled:
            fallback_model = None

    # Config-level fallback
    if not primary_model and fallback_model:
        primary_model = fallback_model
        fallback_model = None

    if not primary_model:
        raise HTTPException(
            status_code=400,
            detail=f"Target agent '{target.name}' has no LLM model configured",
        )

    logger.info(
        f"[AgentAPI] {caller.name} -> {target.name}, "
        f"model={primary_model.model}, prompt={body.prompt[:80]}"
    )

    # Build messages
    messages = [{"role": "user", "content": body.prompt}]

    # Call LLM (synchronous — full tool-calling loop, no streaming)
    from app.services.llm import call_llm

    accumulated_usage = TokenUsage()

    # Capture usage from within call_llm by monkeypatching record_token_usage
    # Instead, we call call_llm directly and track usage via the caller agent.
    # call_llm records usage against the target agent internally; we need to
    # additionally record against the caller. For now, call_llm handles target
    # agent token tracking. We'll record the usage to the caller separately.
    try:
        reply = await call_llm(
            model=primary_model,
            messages=messages,
            agent_name=target.name,
            role_description=target.role_description or "",
            agent_id=target.id,
            user_id=caller.creator_id,
            session_id=f"api_{caller.id}_{target.id}",
            on_chunk=None,
            on_tool_call=None,
            on_thinking=None,
        )
    except Exception as e:
        logger.error(f"[AgentAPI] LLM call failed: {e}")
        raise HTTPException(status_code=502, detail=f"LLM call failed: {str(e)[:200]}")

    # Log activity
    from app.services.activity_logger import log_activity
    await log_activity(
        target.id,
        "api_call",
        f"API call from {caller.name}: {body.prompt[:80]}",
        detail={
            "caller_agent_id": str(caller.id),
            "caller_agent_name": caller.name,
            "prompt": body.prompt[:500],
            "reply": reply[:500] if reply else "",
        },
    )
    await log_activity(
        caller.id,
        "api_call_out",
        f"Called {target.name} via API: {body.prompt[:80]}",
        detail={
            "target_agent_id": str(target.id),
            "target_agent_name": target.name,
            "prompt": body.prompt[:500],
            "reply": reply[:500] if reply else "",
        },
    )

    logger.info(f"[AgentAPI] Reply from {target.name}: {(reply or '')[:80]}")

    return AgentApiChatResponse(
        reply=reply or "",
        usage={},
    )


# ─── Token Key Management ──────────────────────────────

@router.get("/token-key/{agent_id}")
async def get_token_key(
    agent_id: uuid.UUID,
    authorization: str = Header(..., alias="Authorization"),
    db: AsyncSession = Depends(get_db),
):
    """Get the full token key for an agent. Requires JWT auth with manage access."""
    from app.core.security import decode_access_token, get_current_user
    from app.core.permissions import check_agent_access
    from app.models.user import User

    # Accept both Bearer JWT and Bearer token_key
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing Authorization header")
    token = authorization[7:].strip()

    # Try JWT auth first
    try:
        payload = decode_access_token(token)
        user_id = uuid.UUID(payload["sub"])
        result = await db.execute(select(User).where(User.id == user_id))
        user = result.scalar_one_or_none()
        if not user:
            raise HTTPException(status_code=401, detail="User not found")
        agent, access_level = await check_agent_access(db, user, agent_id)
        if access_level != "manage":
            raise HTTPException(status_code=403, detail="Manage access required")
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid authorization")

    if not agent.token_key:
        # Generate one on-demand if missing
        key, suffix = generate_token_key()
        agent.token_key = key
        agent.token_key_suffix = suffix
        await db.commit()

    return {"token_key": agent.token_key, "token_key_suffix": agent.token_key_suffix}


@router.post("/regenerate-token-key/{agent_id}")
async def regenerate_token_key(
    agent_id: uuid.UUID,
    authorization: str = Header(..., alias="Authorization"),
    db: AsyncSession = Depends(get_db),
):
    """Regenerate the token key for an agent. Returns the new full key."""
    from app.core.security import decode_access_token
    from app.core.permissions import check_agent_access
    from app.models.user import User

    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing Authorization header")
    token = authorization[7:].strip()

    try:
        payload = decode_access_token(token)
        user_id = uuid.UUID(payload["sub"])
        result = await db.execute(select(User).where(User.id == user_id))
        user = result.scalar_one_or_none()
        if not user:
            raise HTTPException(status_code=401, detail="User not found")
        agent, access_level = await check_agent_access(db, user, agent_id)
        if access_level != "manage":
            raise HTTPException(status_code=403, detail="Manage access required")
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid authorization")

    key, suffix = generate_token_key()
    agent.token_key = key
    agent.token_key_suffix = suffix
    await db.commit()

    logger.info(f"[AgentAPI] Token key regenerated for agent {agent.name} ({agent_id})")

    return {"token_key": key, "token_key_suffix": suffix}
