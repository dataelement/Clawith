from types import SimpleNamespace
from app.services.llm.utils import convert_chat_messages_to_llm_format

def test_convert_chat_messages_preserves_reasoning_content():
    message = SimpleNamespace(
        id="message-1",
        role="assistant",
        content="I found the answer.",
        thinking="I should inspect the evidence first.",
    )

    result = convert_chat_messages_to_llm_format([message])

    assert result == [
        {
            "role": "assistant",
            "content": "I found the answer.",
            "reasoning_content": "I should inspect the evidence first.",
        }
    ]