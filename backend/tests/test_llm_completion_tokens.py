"""Regression tests: OpenAI/Azure reasoning models must get
``max_completion_tokens`` instead of ``max_tokens`` on Chat Completions.

OpenAI's API reference states ``max_tokens`` "is not compatible with
o-series models", and Azure's reasoning guide says reasoning models
(GPT-5 included) only work with ``max_completion_tokens``; the o-series
additionally has no parallel tool calls on Azure. Older models and other
OpenAI-compatible providers keep the legacy field names.
"""

from app.services.llm.client import LLMMessage, create_llm_client

_MESSAGES = [LLMMessage(role="user", content="hi")]
_TOOLS = [{"type": "function", "function": {"name": "f", "parameters": {}}}]


def _payload(provider: str, model: str, tools=None, max_tokens: int = 16384):
    client = create_llm_client(provider=provider, model=model, api_key="test")
    return client._build_payload(_MESSAGES, tools, None, max_tokens)


def test_openai_gpt5_model_gets_max_completion_tokens():
    payload = _payload("openai", "gpt-5.4-mini")
    assert payload["max_completion_tokens"] == 16384
    assert "max_tokens" not in payload


def test_azure_o_series_gets_max_completion_tokens():
    payload = _payload("azure", "o3")
    assert payload["max_completion_tokens"] == 16384
    assert "max_tokens" not in payload


def test_azure_o_series_omits_parallel_tool_calls():
    payload = _payload("azure", "o3", tools=_TOOLS, max_tokens=100)
    assert "parallel_tool_calls" not in payload


def test_openai_gpt5_keeps_parallel_tool_calls():
    # Only the o-series lacks parallel tool calls; GPT-5 keeps them.
    payload = _payload("openai", "gpt-5.5", tools=_TOOLS, max_tokens=100)
    assert payload["parallel_tool_calls"] is True
    assert payload["max_completion_tokens"] == 100


def test_older_openai_model_keeps_max_tokens():
    payload = _payload("openai", "gpt-4o", tools=_TOOLS, max_tokens=100)
    assert payload["max_tokens"] == 100
    assert "max_completion_tokens" not in payload
    assert payload["parallel_tool_calls"] is True


def test_other_provider_with_same_model_name_keeps_max_tokens():
    # The rename is scoped to the first-party OpenAI/Azure endpoints;
    # a GPT-5-named model on another compatible provider is untouched.
    payload = _payload("deepseek", "gpt-5.4-mini", max_tokens=100)
    assert payload["max_tokens"] == 100
    assert "max_completion_tokens" not in payload
