"""Model-visible boundaries for one shared LangGraph Thread."""

from collections.abc import Mapping, Sequence

from app.services.agent_runtime.state import JsonObject
from app.services.llm.finish import FINISH_PROTOCOL_REMINDER


def model_visible_thread_messages(
    messages: Sequence[Mapping[str, object]],
    *,
    current_run_id: str,
) -> tuple[JsonObject, ...]:
    """Keep current-Run state and prior tool facts, not unpublished drafts."""
    copied = tuple(dict(message) for message in messages)
    current_start = next(
        (
            index
            for index, message in enumerate(copied)
            if message.get("runtime_input") == "current"
            and message.get("runtime_run_id") == current_run_id
        ),
        None,
    )
    if current_start is None:
        return copied

    visible: list[JsonObject] = []
    for index, message in enumerate(copied):
        if index >= current_start:
            visible.append(message)
            continue
        role = message.get("role")
        if role == "assistant" and not message.get("tool_calls"):
            # Accepted terminal replies are loaded from the product Session
            # snapshot. Thread-only plain assistant messages are candidates,
            # including drafts that never passed verification or delivery.
            continue
        if role == "user" and (
            message.get("runtime_intent") == "repair"
            or message.get("content") == FINISH_PROTOCOL_REMINDER
        ):
            continue
        visible.append(message)
    return tuple(visible)


__all__ = ["model_visible_thread_messages"]
