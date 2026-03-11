"""Webhook receiver endpoint for external trigger integration.

Provides a public POST endpoint that external services (GitHub, Grafana, etc.)
can send events to, which triggers the corresponding agent.
"""

import hashlib
import hmac
import json
import logging
import time

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select

from app.database import async_session
from app.models.trigger import AgentTrigger

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/webhooks", tags=["webhooks"])

# In-memory rate limiter: token -> list of timestamps
_rate_hits: dict[str, list[float]] = {}
RATE_LIMIT = 5       # max hits per minute per token
MAX_PAYLOAD_SIZE = 65536  # 64KB max payload


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
    now = time.time()
    hits = _rate_hits.get(token, [])
    hits = [t for t in hits if now - t < 60]  # keep last 60 seconds

    # We'll check per-agent rate limit after finding the trigger below.
    # For now, apply a generous global ceiling to prevent memory abuse.
    if len(hits) >= 60:  # hard ceiling: 60/min regardless of config
        logger.warning(f"Webhook hard rate limit exceeded for token {token[:8]}...")
        return JSONResponse({"ok": True}, status_code=429)
    hits.append(now)
    _rate_hits[token] = hits

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
                AgentTrigger.is_enabled == True,
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
        from app.models.agent import Agent
        agent_result = await db.execute(select(Agent).where(Agent.id == target.agent_id))
        agent_obj = agent_result.scalar_one_or_none()
        agent_rate_limit = (agent_obj.webhook_rate_limit if agent_obj else None) or RATE_LIMIT
        # Re-check hits against agent-specific limit (hits already collected above)
        if len(hits) > agent_rate_limit:  # > because we already appended current hit
            logger.warning(f"Webhook per-agent rate limit ({agent_rate_limit}/min) for token {token[:8]}...")
            # Log audit entry so user can see dropped webhooks
            try:
                from app.models.audit import AuditLog
                db.add(AuditLog(
                    agent_id=target.agent_id,
                    action="webhook_rate_limited",
                    details={
                        "trigger_name": target.name,
                        "limit": agent_rate_limit,
                        "token_prefix": token[:8],
                    },
                ))
                await db.commit()
            except Exception:
                pass
            return JSONResponse({"ok": True}, status_code=429)

        cfg = target.config or {}

        # HMAC signature verification (optional)
        secret = cfg.get("secret")
        if secret:
            sig_header = request.headers.get("x-hub-signature-256", "")
            expected_sig = "sha256=" + hmac.new(
                secret.encode(), body, hashlib.sha256
            ).hexdigest()
            if not hmac.compare_digest(sig_header, expected_sig):
                logger.warning(f"Webhook signature mismatch for trigger {target.name}")
                # Still return 200 to not leak info
                return JSONResponse({"ok": True})

        # Parse payload
        try:
            payload_str = body.decode("utf-8")
            # Try to pretty-format JSON for readability
            try:
                payload_obj = json.loads(payload_str)
                payload_str = json.dumps(payload_obj, ensure_ascii=False, indent=2)
            except json.JSONDecodeError:
                pass  # Keep as raw string
        except Exception:
            payload_str = repr(body[:2000])

        # Store payload and set pending flag
        new_config = {**cfg, "_webhook_pending": True, "_webhook_payload": payload_str[:8000]}
        from sqlalchemy import update
        await db.execute(
            update(AgentTrigger)
            .where(AgentTrigger.id == target.id)
            .values(config=new_config)
        )
        await db.commit()

        logger.info(f"Webhook received for trigger {target.name} (agent {target.agent_id})")

    return JSONResponse({"ok": True})
