"""Shared LLM provider configuration and utilities.

Centralizes provider URLs and provider-specific API parameters
so they don't need to be duplicated across websocket.py, scheduler.py,
task_executor.py, agent_tools.py, and feishu.py.

This module also exports the unified LLM client classes from llm_client.py
for convenient access.
"""

from app.core.security import decrypt_data
from app.config import get_settings
from app.models.llm import LLMModel

# Re-export all client classes and functions from llm_client.py
from app.services.llm_client import (
    AnthropicClient,
    GeminiClient,
    LLMClient,
    LLMError,
    LLMMessage,
    LLMResponse,
    LLMStreamChunk,
    OpenAICompatibleClient,
    OpenAIResponsesClient,
    PROVIDER_ALIASES,
    PROVIDER_REGISTRY,
    ProviderSpec,
    PROVIDER_URLS,
    TOOL_CHOICE_PROVIDERS,
    MAX_TOKENS_BY_PROVIDER as _MAX_TOKENS_BY_PROVIDER,
    MAX_TOKENS_BY_MODEL as _MAX_TOKENS_BY_MODEL,
    chat_complete,
    chat_stream,
    create_llm_client,
    get_max_tokens,
    get_provider_manifest,
    get_provider_base_url,
    get_provider_spec,
    normalize_provider,
)

# Keep ANTHROPIC_API_PROVIDERS for backward compatibility
ANTHROPIC_API_PROVIDERS = {"anthropic"}

# Keep the original PROVIDER_URLS reference (already exported from llm_client)


def get_model_api_key(model: LLMModel) -> str:
    """Decrypt the model's API key, with backward compatibility for plaintext keys."""
    raw = model.api_key_encrypted or ""
    if not raw:
        return ""
    try:
        settings = get_settings()
        return decrypt_data(raw, settings.SECRET_KEY)
    except ValueError:
        return raw


def get_tool_params(provider: str) -> dict:
    """Return provider-specific tool calling parameters.

    Qwen and OpenAI support `tool_choice` and `parallel_tool_calls`.
    Anthropic uses a different tool calling format, so we skip these params.

    Note: This function is kept for backward compatibility.
    The new client classes handle this internally.
    """
    if provider in TOOL_CHOICE_PROVIDERS:
        return {
            "tool_choice": "auto",
            "parallel_tool_calls": True,
        }
    return {}


# Keep backward compatibility aliases
__all__ = [
    # Original utilities
    "get_tool_params",
    "get_provider_base_url",
    "get_max_tokens",
    # New client classes
    "LLMClient",
    "OpenAICompatibleClient",
    "OpenAIResponsesClient",
    "GeminiClient",
    "AnthropicClient",
    "LLMMessage",
    "LLMResponse",
    "LLMStreamChunk",
    "LLMError",
    # New functions
    "create_llm_client",
    "chat_complete",
    "chat_stream",
    # Constants
    "ProviderSpec",
    "PROVIDER_ALIASES",
    "PROVIDER_REGISTRY",
    "PROVIDER_URLS",
    "ANTHROPIC_API_PROVIDERS",
    "TOOL_CHOICE_PROVIDERS",
    # Registry helpers
    "normalize_provider",
    "get_provider_spec",
    "get_provider_manifest",
]
