"""DingTalk emotion reaction service — "thinking" indicator on user messages."""

import asyncio
from loguru import logger
from app.services.dingtalk_token import dingtalk_token_manager


async def add_thinking_reaction(
    app_key: str,
    app_secret: str,
    message_id: str,
    conversation_id: str,
) -> bool:
    """Add "🤔思考中" reaction to a user message. Fire-and-forget, never raises."""
    import httpx

    if not message_id or not conversation_id or not app_key:
        return False

    try:
        token = await dingtalk_token_manager.get_token(app_key, app_secret)
        if not token:
            logger.warning("[DingTalk Reaction] Failed to get access token")
            return False

        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.post(
                "https://api.dingtalk.com/v1.0/robot/emotion/reply",
                headers={
                    "x-acs-dingtalk-access-token": token,
                    "Content-Type": "application/json",
                },
                json={
                    "robotCode": app_key,
                    "openMsgId": message_id,
                    "openConversationId": conversation_id,
                    "emotionType": 2,
                    "emotionName": "🤔思考中",
                    "textEmotion": {
                        "emotionId": "2659900",
                        "emotionName": "🤔思考中",
                        "text": "🤔思考中",
                        "backgroundId": "im_bg_1",
                    },
                },
            )
            if resp.status_code == 200:
                logger.info(f"[DingTalk Reaction] Thinking reaction added for msg {message_id[:16]}")
                return True
            else:
                logger.warning(f"[DingTalk Reaction] Add failed: {resp.status_code} {resp.text[:200]}")
                return False
    except Exception as e:
        logger.warning(f"[DingTalk Reaction] Add thinking reaction error: {e}")
        return False


async def recall_thinking_reaction(
    app_key: str,
    app_secret: str,
    message_id: str,
    conversation_id: str,
) -> None:
    """Recall "🤔思考中" reaction with retry (0ms, 1500ms, 5000ms). Fire-and-forget."""
    import httpx

    if not message_id or not conversation_id or not app_key:
        return

    delays = [0, 1.5, 5.0]

    for delay in delays:
        if delay > 0:
            await asyncio.sleep(delay)

        try:
            token = await dingtalk_token_manager.get_token(app_key, app_secret)
            if not token:
                continue

            async with httpx.AsyncClient(timeout=5) as client:
                resp = await client.post(
                    "https://api.dingtalk.com/v1.0/robot/emotion/recall",
                    headers={
                        "x-acs-dingtalk-access-token": token,
                        "Content-Type": "application/json",
                    },
                    json={
                        "robotCode": app_key,
                        "openMsgId": message_id,
                        "openConversationId": conversation_id,
                        "emotionType": 2,
                        "emotionName": "🤔思考中",
                        "textEmotion": {
                            "emotionId": "2659900",
                            "emotionName": "🤔思考中",
                            "text": "🤔思考中",
                            "backgroundId": "im_bg_1",
                        },
                    },
                )
                if resp.status_code == 200:
                    logger.info(f"[DingTalk Reaction] Thinking reaction recalled for msg {message_id[:16]}")
                    return
                else:
                    logger.warning(f"[DingTalk Reaction] Recall attempt failed: {resp.status_code}")
        except Exception as e:
            logger.warning(f"[DingTalk Reaction] Recall error: {e}")

    logger.warning(f"[DingTalk Reaction] All recall attempts failed for msg {message_id[:16]}")
