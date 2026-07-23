"""Webhook receiver endpoint for external trigger integration.

Provides a public POST endpoint that external services (GitHub, Grafana, etc.)
can send events to, which triggers the corresponding agent.
"""

import hashlib
import hmac
import json
import time

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from loguru import logger
from sqlalchemy import select

from app.core.events import get_redis
from app.database import async_session
from app.models.agent import Agent
from app.models.audit import AuditLog
from app.models.trigger import AgentTrigger
from app.services.trigger_runtime import enqueue_webhook_execution

router = APIRouter(prefix="/api/webhooks", tags=["webhooks"])

RATE_LIMIT = 5  # max hits per minute per token
MAX_PAYLOAD_SIZE = 65536  # 64KB max payload


async def _record_and_count_hits(token: str) -> int:
    """Record the current hit in Redis and return the rolling 60-second count."""
    redis = await get_redis()
    now = time.time()
    key = f"webhook:rate:{token}"
    member = f"{now}:{hashlib.sha1(f'{token}:{now}'.encode()).hexdigest()[:8]}"
    async with redis.pipeline(transaction=True) as pipe:
        pipe.zremrangebyscore(key, 0, now - 60)
        pipe.zadd(key, {member: now})
        pipe.zcard(key)
        pipe.expire(key, 120)
        _, _, count, _ = await pipe.execute()
    return int(count)


@router.post("/t/{token}")
async def receive_webhook(token: str, request: Request):
    """Receive a webhook POST from an external service.

    Public endpoint — no authentication required.
    Security is provided by:
    - Unique, unguessable URL token
    - Optional HMAC signature verification
    - Rate limiting (5 requests/minute per token)
    - Payload size limit (64KB)
    """
    # Rate limiting — use per-agent limit if available
    hit_count = await _record_and_count_hits(token)

    # We'll check per-agent rate limit after finding the trigger below.
    # For now, apply a generous global ceiling to prevent memory abuse.
    if hit_count >= 60:  # hard ceiling: 60/min regardless of config
        logger.warning(f"Webhook hard rate limit exceeded for token {token[:8]}...")
        return JSONResponse({"ok": True}, status_code=429)

    # Payload size check
    body = await request.body()
    if len(body) > MAX_PAYLOAD_SIZE:
        logger.warning(f"Webhook payload too large for token {token[:8]}...: {len(body)} bytes")
        return JSONResponse({"ok": True}, status_code=413)

    # Look up trigger
    async with async_session() as db:
        result = await db.execute(
            select(AgentTrigger).where(
                AgentTrigger.type == "webhook",
                AgentTrigger.is_enabled,
            )
        )
        triggers = result.scalars().all()

        # Find the trigger matching this token
        target = None
        for trigger in triggers:
            cfg = trigger.config or {}
            if cfg.get("token") == token:
                target = trigger
                break

        if not target:
            # Return 200 OK to avoid leaking whether the token exists
            return JSONResponse({"ok": True})

        # Per-agent rate limit check
        agent_result = await db.execute(
            select(Agent).where(
                Agent.id == target.agent_id,
                Agent.deleted_at.is_(None),
            )
        )
        agent_obj = agent_result.scalar_one_or_none()
        if agent_obj is None:
            return JSONResponse({"ok": True})
        agent_rate_limit = (agent_obj.webhook_rate_limit if agent_obj else None) or RATE_LIMIT

        # Retrieve all needed scalar fields and expunge from db session to prevent MissingGreenlet errors.
        target_name = target.name
        target_agent_id = target.agent_id
        target_config = target.config or {}
        db.expunge(target)
        if agent_obj:
            db.expunge(agent_obj)

        # Re-check hits against agent-specific limit (hits already collected above)
        if hit_count > agent_rate_limit:  # > because current hit is already counted
            logger.warning(f"Webhook per-agent rate limit ({agent_rate_limit}/min) for token {token[:8]}...")
            # Log audit entry so user can see dropped webhooks
            try:
                db.add(
                    AuditLog(
                        agent_id=target_agent_id,
                        action="webhook_rate_limited",
                        details={
                            "trigger_name": target_name,
                            "limit": agent_rate_limit,
                            "token_prefix": token[:8],
                        },
                    )
                )
                await db.commit()
            except Exception:
                pass
            return JSONResponse({"ok": True}, status_code=429)

        # HMAC signature verification (optional)
        secret = target_config.get("secret")
        if secret:
            sig_header = request.headers.get("x-hub-signature-256", "")
            expected_sig = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
            if not hmac.compare_digest(sig_header, expected_sig):
                logger.warning(f"Webhook signature mismatch for trigger {target_name}")
                # Still return 200 to not leak info
                return JSONResponse({"ok": True})

        # Parse payload
        try:
            payload_str = body.decode("utf-8")
            # Try to pretty-format JSON for readability
            payload_obj = None
            try:
                payload_obj = json.loads(payload_str)
                payload_str = json.dumps(payload_obj, ensure_ascii=False, indent=2)
            except json.JSONDecodeError:
                payload_obj = None
        except Exception:
            payload_obj = None
            payload_str = repr(body[:2000])

        execution, created = await enqueue_webhook_execution(
            db,
            trigger=target,
            body=body,
            payload_text=payload_str,
            payload_obj=payload_obj if isinstance(payload_obj, dict) else None,
            request_headers={k.lower(): v for k, v in request.headers.items()},
        )
        if not created:
            logger.info(f"Webhook duplicate ignored for trigger {target_name}")
            return JSONResponse({"ok": True})
        if execution is not None and execution.status == "failed":
            logger.error(
                "Webhook Runtime intake failed for trigger {}: {}",
                target_name,
                execution.last_error,
            )
            return JSONResponse(
                {"ok": False, "error": "runtime_unavailable"},
                status_code=503,
            )

        logger.info(f"Webhook queued for trigger {target_name} (agent {target_agent_id})")

    return JSONResponse({"ok": True})
