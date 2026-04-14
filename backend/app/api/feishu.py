        except Exception as e:
                logger.error(f"[Feishu] Failed to download post image {ik}: {e}")


async def _call_agent_llm(
    db: AsyncSession,
    agent_id: uuid.UUID,
    user_text: str,
    history: list[dict] | None = None,
    user_id=None,
    on_chunk=None,
    on_thinking=None,
    on_tool_call=None,
) -> str:
    """Call the agent's configured LLM model with conversation history.

    DEPRECATED: Use app.services.llm_caller.call_agent_llm instead.
    This function is kept for backward compatibility with existing imports.
    """
    from app.services.llm import call_agent_llm
    return await call_agent_llm(
        db=db,
        agent_id=agent_id,
        user_text=user_text,
        history=history,
        user_id=user_id,
        on_chunk=on_chunk,
        on_thinking=on_thinking,
    )


async def _get_feishu_token(config: ChannelConfig) -> str:
