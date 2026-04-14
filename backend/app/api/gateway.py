    logger.info(f"[Gateway] _send_to_agent_background started: {source_agent_name} -> {target_agent_name}")
    try:
        from app.services.llm import call_llm
        from app.services.agent_context import build_agent_context
        from app.models.llm import LLMModel
        from app.models.audit import ChatMessage
        from app.models.chat_session import ChatSession

        async with async_session() as db:
