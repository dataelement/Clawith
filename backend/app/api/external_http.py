"""External HTTP channel for business-system integrations."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import secrets
import time
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from loguru import logger
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.events import get_redis
from app.core.permissions import check_agent_access, is_agent_creator
from app.core.security import get_current_user
from app.database import async_session, get_db
from app.models.audit import ChatMessage
from app.models.channel_config import ChannelConfig
from app.models.user import User
from app.schemas.schemas import ChannelConfigOut
from app.services.channel_session import find_or_create_channel_session
from app.services.channel_user_service import channel_user_service
from app.services.llm.utils import convert_chat_messages_to_llm_format

router = APIRouter(tags=["external-http"])

CHANNEL_TYPE = "external_http"
DEFAULT_MAX_PAYLOAD_BYTES = 64 * 1024
DEFAULT_SYNC_TIMEOUT_SECONDS = 120
EXTERNAL_USER_ID_MAX_LENGTH = 100
EXTERNAL_USER_NAME_MAX_LENGTH = 100
EXTERNAL_CONVERSATION_ID_MAX_LENGTH = 200 - len(f"{CHANNEL_TYPE}:")


class ExternalHttpChannelConfigIn(BaseModel):
    require_hmac: bool = False
    sync_timeout_seconds: int = Field(DEFAULT_SYNC_TIMEOUT_SECONDS, ge=5, le=300)
    max_payload_bytes: int = Field(DEFAULT_MAX_PAYLOAD_BYTES, ge=1024, le=1024 * 1024)
    regenerate_api_key: bool = False
    regenerate_signing_secret: bool = False


class ExternalHttpMessageIn(BaseModel):
    content: str = Field(min_length=1, max_length=60000)
    external_user_id: str = Field(default="external", min_length=1, max_length=EXTERNAL_USER_ID_MAX_LENGTH)
    external_user_name: str | None = Field(default=None, max_length=EXTERNAL_USER_NAME_MAX_LENGTH)
    conversation_id: str | None = Field(default=None, max_length=EXTERNAL_CONVERSATION_ID_MAX_LENGTH)
    metadata: dict[str, Any] | None = None
    mode: str = Field(default="sync", pattern="^(sync|async)$")

    @field_validator("external_user_id", mode="before")
    @classmethod
    def normalize_external_user_id(cls, value: str) -> str:
        stripped = (value or "").strip()
        if not stripped:
            raise ValueError("external_user_id cannot be blank")
        return stripped


def _hash_secret(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _new_api_key() -> str:
    return f"ext-{secrets.token_urlsafe(32)}"


def _new_signing_secret() -> str:
    return secrets.token_urlsafe(32)


def _extract_api_key(request: Request) -> str:
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth.split(" ", 1)[1].strip()
    return (request.headers.get("x-api-key") or "").strip()


def _safe_extra(config: ChannelConfig) -> dict:
    extra = dict(config.extra_config or {})
    extra.pop("api_key_hash", None)
    return extra


def _serialize_config(
    config: ChannelConfig,
    *,
    api_key: str | None = None,
    signing_secret: str | None = None,
    webhook_url: str | None = None,
) -> dict:
    payload = ChannelConfigOut.model_validate(config).model_dump()
    payload["extra_config"] = _safe_extra(config)
    payload["app_secret"] = None
    payload["encrypt_key"] = None
    if api_key:
        payload["api_key"] = api_key
    if signing_secret:
        payload["signing_secret"] = signing_secret
    if webhook_url:
        payload["webhook_url"] = webhook_url
    return payload


async def _public_message_url(request: Request, db: AsyncSession, agent_id: uuid.UUID) -> str:
    from app.services.platform_service import platform_service

    public_base = await platform_service.get_public_base_url(db, request)
    return f"{public_base.rstrip('/')}/api/channel/external-http/{agent_id}/message"


def _verify_api_key(config: ChannelConfig, request: Request) -> None:
    expected_hash = (config.extra_config or {}).get("api_key_hash") or ""
    api_key = _extract_api_key(request)
    if not api_key or not expected_hash:
        raise HTTPException(status_code=401, detail="Missing external HTTP channel API key")
    if not hmac.compare_digest(_hash_secret(api_key), expected_hash):
        raise HTTPException(status_code=401, detail="Invalid external HTTP channel API key")


def _verify_hmac_signature(config: ChannelConfig, request: Request, body: bytes) -> None:
    extra = config.extra_config or {}
    if not extra.get("require_hmac"):
        return

    signing_secret = config.encrypt_key or ""
    if not signing_secret:
        raise HTTPException(status_code=401, detail="External HTTP channel signing secret is not configured")

    timestamp = request.headers.get("x-timestamp", "")
    signature = request.headers.get("x-signature-sha256", "")
    if not timestamp or not signature:
        raise HTTPException(status_code=401, detail="Missing HMAC signature headers")

    try:
        ts_value = int(timestamp)
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid HMAC timestamp") from None

    if abs(int(time.time()) - ts_value) > 300:
        raise HTTPException(status_code=401, detail="Expired HMAC timestamp")

    signed_payload = timestamp.encode("utf-8") + b"." + body
    expected = hmac.new(signing_secret.encode("utf-8"), signed_payload, hashlib.sha256).hexdigest()
    provided = signature.removeprefix("sha256=").strip()
    if not hmac.compare_digest(expected, provided):
        raise HTTPException(status_code=401, detail="Invalid HMAC signature")


async def _record_and_count_hits(config: ChannelConfig) -> int:
    try:
        redis = await get_redis()
        now = time.time()
        token_key = (config.extra_config or {}).get("api_key_hash") or str(config.agent_id)
        key = f"external_http:rate:{token_key}"
        member = f"{now}:{secrets.token_hex(4)}"
        async with redis.pipeline(transaction=True) as pipe:
            pipe.zremrangebyscore(key, 0, now - 60)
            pipe.zadd(key, {member: now})
            pipe.zcard(key)
            pipe.expire(key, 120)
            _, _, count, _ = await pipe.execute()
        return int(count)
    except Exception as exc:
        logger.warning(f"[ExternalHTTP] Rate limiter unavailable: {exc}")
        return 1


def _llm_text(message: ExternalHttpMessageIn) -> str:
    if not message.metadata:
        return message.content
    metadata_text = json.dumps(message.metadata, ensure_ascii=False, indent=2, default=str)
    return f"{message.content}\n\n[External HTTP metadata]\n{metadata_text}"


async def _process_external_http_message(
    *,
    agent_id: uuid.UUID,
    message: ExternalHttpMessageIn,
    request_id: str,
) -> dict:
    from app.api.feishu import _call_llm_with_config, _load_agent_and_model
    from app.models.agent import DEFAULT_CONTEXT_WINDOW_SIZE
    from app.models.chat_session import ChatSession
    from app.services.activity_logger import log_activity

    async with async_session() as db:
        agent, model, fallback_model = await _load_agent_and_model(db, agent_id)
        if not agent:
            raise HTTPException(status_code=404, detail="Agent not found")

        ctx_size = agent.context_window_size or DEFAULT_CONTEXT_WINDOW_SIZE
        external_user_id = message.external_user_id.strip()
        external_name = (message.external_user_name or "").strip() or f"External User {external_user_id[:8]}"
        platform_user = await channel_user_service.resolve_channel_user(
            db=db,
            agent=agent,
            channel_type=CHANNEL_TYPE,
            external_user_id=external_user_id,
            extra_info={
                "name": external_name,
                "external_id": external_user_id,
            },
        )

        external_conv = (message.conversation_id or "").strip() or external_user_id
        external_conv_id = f"{CHANNEL_TYPE}:{external_conv}"
        session = await find_or_create_channel_session(
            db=db,
            agent_id=agent_id,
            user_id=platform_user.id,
            external_conv_id=external_conv_id,
            source_channel=CHANNEL_TYPE,
            first_message_title=message.content,
        )
        session_id = str(session.id)

        history_r = await db.execute(
            select(ChatMessage)
            .where(ChatMessage.agent_id == agent_id, ChatMessage.conversation_id == session_id)
            .order_by(ChatMessage.created_at.desc())
            .limit(ctx_size)
        )
        history = convert_chat_messages_to_llm_format(reversed(history_r.scalars().all()))

        content_for_llm = _llm_text(message)
        db.add(
            ChatMessage(
                agent_id=agent_id,
                user_id=platform_user.id,
                role="user",
                content=content_for_llm,
                conversation_id=session_id,
            )
        )
        session.last_message_at = datetime.now(timezone.utc)
        await db.commit()
        platform_user_id = platform_user.id

    reply_text = await _call_llm_with_config(
        agent,
        model,
        fallback_model,
        agent_id,
        content_for_llm,
        history=history,
        user_id=platform_user_id,
        session_id=session_id,
    )

    async with async_session() as db:
        db.add(
            ChatMessage(
                agent_id=agent_id,
                user_id=platform_user_id,
                role="assistant",
                content=reply_text,
                conversation_id=session_id,
            )
        )
        session_r = await db.execute(select(ChatSession).where(ChatSession.id == uuid.UUID(session_id)))
        session = session_r.scalar_one_or_none()
        if session:
            session.last_message_at = datetime.now(timezone.utc)
        await db.commit()

    await log_activity(
        agent_id,
        "chat_reply",
        f"Replied to external HTTP message: {reply_text[:80]}",
        detail={
            "channel": CHANNEL_TYPE,
            "request_id": request_id,
            "external_user_id": external_user_id,
            "conversation_id": message.conversation_id,
            "user_text": message.content[:500],
            "reply": reply_text[:500],
        },
    )

    return {
        "ok": True,
        "request_id": request_id,
        "session_id": session_id,
        "reply": reply_text,
    }


@router.post("/agents/{agent_id}/external-http-channel", status_code=status.HTTP_201_CREATED)
async def configure_external_http_channel(
    agent_id: uuid.UUID,
    request: Request,
    data: ExternalHttpChannelConfigIn = ExternalHttpChannelConfigIn(),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    agent, _ = await check_agent_access(db, current_user, agent_id)
    if not is_agent_creator(current_user, agent):
        raise HTTPException(status_code=403, detail="Only creator can configure channel")

    result = await db.execute(
        select(ChannelConfig).where(
            ChannelConfig.agent_id == agent_id,
            ChannelConfig.channel_type == CHANNEL_TYPE,
        )
    )
    config = result.scalar_one_or_none()

    generated_api_key = None
    generated_signing_secret = None
    extra = {
        "require_hmac": data.require_hmac,
        "sync_timeout_seconds": data.sync_timeout_seconds,
        "max_payload_bytes": data.max_payload_bytes,
        "auth_scheme": "bearer",
        "signature": "HMAC-SHA256 over '<x-timestamp>.<raw-body>' in X-Signature-SHA256",
    }

    if config:
        old_extra = config.extra_config or {}
        if data.regenerate_api_key or not old_extra.get("api_key_hash"):
            generated_api_key = _new_api_key()
            extra["api_key_hash"] = _hash_secret(generated_api_key)
        else:
            extra["api_key_hash"] = old_extra.get("api_key_hash")

        if data.regenerate_signing_secret or (data.require_hmac and not config.encrypt_key):
            generated_signing_secret = _new_signing_secret()
            config.encrypt_key = generated_signing_secret

        config.app_id = CHANNEL_TYPE
        config.app_secret = None
        config.extra_config = extra
        config.is_configured = True
        config.is_connected = True
        await db.flush()
    else:
        generated_api_key = _new_api_key()
        extra["api_key_hash"] = _hash_secret(generated_api_key)
        generated_signing_secret = _new_signing_secret() if data.require_hmac else None
        config = ChannelConfig(
            agent_id=agent_id,
            channel_type=CHANNEL_TYPE,
            app_id=CHANNEL_TYPE,
            app_secret=None,
            encrypt_key=generated_signing_secret,
            extra_config=extra,
            is_configured=True,
            is_connected=True,
        )
        db.add(config)
        await db.flush()

    webhook_url = await _public_message_url(request, db, agent_id)
    await db.commit()
    return _serialize_config(
        config,
        api_key=generated_api_key,
        signing_secret=generated_signing_secret,
        webhook_url=webhook_url,
    )


@router.get("/agents/{agent_id}/external-http-channel")
async def get_external_http_channel(
    agent_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await check_agent_access(db, current_user, agent_id)
    result = await db.execute(
        select(ChannelConfig).where(
            ChannelConfig.agent_id == agent_id,
            ChannelConfig.channel_type == CHANNEL_TYPE,
        )
    )
    config = result.scalar_one_or_none()
    if not config:
        raise HTTPException(status_code=404, detail="External HTTP channel not configured")
    return _serialize_config(config)


@router.get("/agents/{agent_id}/external-http-channel/webhook-url")
async def get_external_http_message_url(
    agent_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    return {"webhook_url": await _public_message_url(request, db, agent_id)}


@router.delete("/agents/{agent_id}/external-http-channel", status_code=status.HTTP_204_NO_CONTENT)
async def delete_external_http_channel(
    agent_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    agent, _ = await check_agent_access(db, current_user, agent_id)
    if not is_agent_creator(current_user, agent):
        raise HTTPException(status_code=403, detail="Only creator can remove channel")
    result = await db.execute(
        select(ChannelConfig).where(
            ChannelConfig.agent_id == agent_id,
            ChannelConfig.channel_type == CHANNEL_TYPE,
        )
    )
    config = result.scalar_one_or_none()
    if not config:
        raise HTTPException(status_code=404, detail="External HTTP channel not configured")
    await db.delete(config)
    await db.commit()


@router.post("/channel/external-http/{agent_id}/message")
async def external_http_message(
    agent_id: uuid.UUID,
    request: Request,
):
    async with async_session() as db:
        result = await db.execute(
            select(ChannelConfig).where(
                ChannelConfig.agent_id == agent_id,
                ChannelConfig.channel_type == CHANNEL_TYPE,
                ChannelConfig.is_configured == True,  # noqa: E712
            )
        )
        config = result.scalar_one_or_none()
        if not config:
            raise HTTPException(status_code=404, detail="External HTTP channel not configured")

        _verify_api_key(config, request)

        max_payload = int((config.extra_config or {}).get("max_payload_bytes") or DEFAULT_MAX_PAYLOAD_BYTES)
        body = await request.body()
        if len(body) > max_payload:
            raise HTTPException(status_code=413, detail="Payload too large")

        _verify_hmac_signature(config, request, body)

        hit_count = await _record_and_count_hits(config)
        from app.models.agent import Agent

        agent_r = await db.execute(select(Agent).where(Agent.id == agent_id))
        agent = agent_r.scalar_one_or_none()
        rate_limit = (agent.webhook_rate_limit if agent else None) or 5
        timeout_seconds = int((config.extra_config or {}).get("sync_timeout_seconds") or DEFAULT_SYNC_TIMEOUT_SECONDS)
        if hit_count > rate_limit:
            raise HTTPException(status_code=429, detail="Rate limit exceeded")

    try:
        payload = ExternalHttpMessageIn.model_validate_json(body)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Invalid request body: {exc}") from None

    request_id = str(uuid.uuid4())
    if payload.mode == "async":
        task = asyncio.create_task(
            _process_external_http_message(agent_id=agent_id, message=payload, request_id=request_id)
        )

        def _log_background_result(done_task: asyncio.Task) -> None:
            try:
                done_task.result()
            except Exception as exc:
                logger.error(f"[ExternalHTTP] Async request {request_id} failed: {exc}")

        task.add_done_callback(_log_background_result)
        return {"ok": True, "status": "accepted", "request_id": request_id}

    try:
        return await asyncio.wait_for(
            _process_external_http_message(agent_id=agent_id, message=payload, request_id=request_id),
            timeout=timeout_seconds,
        )
    except asyncio.TimeoutError:
        return Response(
            content=json.dumps({"ok": False, "request_id": request_id, "error": "Timed out"}),
            media_type="application/json",
            status_code=504,
        )
