"""Generate session titles using the enterprise utility LLM model."""

import logging
from uuid import UUID

from sqlalchemy import select
from app.database import async_session
from app.models.chat_session import ChatSession
from app.services.llm_client import chat_complete

logger = logging.getLogger(__name__)

TITLE_SYSTEM_PROMPT = (
    "Generate a concise title (max 8 words) for this conversation. "
    "Return only the title text, nothing else. No quotes, no punctuation at the end."
)


async def generate_session_title(
    session_id: str,
    user_message: str,
    assistant_response: str,
    utility_model,
    websocket=None,
) -> str | None:
    """Generate a title via LLM and update the session in DB.

    Args:
        session_id: The chat session UUID string.
        user_message: The first user message content.
        assistant_response: The first assistant response (will be truncated).
        utility_model: LLMModel ORM object for the utility model.
        websocket: Optional WebSocket to push title update.

    Returns:
        The generated title string, or None on failure.
    """
    try:
        messages = [
            {"role": "system", "content": TITLE_SYSTEM_PROMPT},
            {"role": "user", "content": user_message[:500]},
            {"role": "assistant", "content": assistant_response[:500]},
            {"role": "user", "content": "Based on this conversation, generate a title."},
        ]

        response = await chat_complete(
            provider=utility_model.provider,
            api_key=utility_model.api_key_encrypted,
            model=utility_model.model,
            messages=messages,
            base_url=utility_model.base_url,
        )

        raw = response.get("choices", [{}])[0].get("message", {}).get("content", "")
        title = raw.strip().strip('"').strip("'")[:80]
        if not title:
            return None

        async with async_session() as db:
            result = await db.execute(
                select(ChatSession).where(ChatSession.id == UUID(session_id))
            )
            session = result.scalar_one_or_none()
            if session and not session.title_edited:
                session.title = title
                await db.commit()
                logger.info(f"[SessionTitle] Generated title for {session_id}: {title}")

                if websocket:
                    try:
                        await websocket.send_json({
                            "type": "session_title_updated",
                            "session_id": session_id,
                            "title": title,
                        })
                    except Exception:
                        pass  # WebSocket may have closed

                return title

    except Exception as e:
        logger.warning(f"[SessionTitle] Failed to generate title for {session_id}: {e}")
        return None
