"""Shared LLM provider configuration and utilities.

Centralizes provider URLs and provider-specific API parameters
so they don't need to be duplicated across websocket.py, scheduler.py,
task_executor.py, agent_tools.py, and feishu.py.

This module also exports the unified LLM client classes from client.py
for convenient access.
"""

from app.core.security import decrypt_data
from app.config import get_settings
from app.database import async_session
from app.models.llm import LLMModel

# Re-export all client classes and functions from client.py
from .client import (
    AnthropicClient,
    CodexOAuthClient,
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

# Keep the original PROVIDER_URLS reference (already exported from client)


def get_model_api_key(model: LLMModel) -> str:
    """Decrypt the model's API key, with backward compatibility for plaintext keys.

    Returns an empty string for OAuth-backed models (they have no static key).
    """
    raw = model.api_key_encrypted or ""
    if not raw:
        return ""
    try:
        settings = get_settings()
        return decrypt_data(raw, settings.SECRET_KEY)
    except ValueError:
        return raw


def get_llm_client_for_model(
    model: LLMModel,
    *,
    timeout: float | None = None,
    session_factory=None,
) -> LLMClient:
    """Create the correct LLMClient for a model, dispatching on auth_type.

    Static-key providers continue to pull the decrypted `api_key_encrypted`.
    OAuth-backed providers (auth_type='codex_oauth') bypass the static key and
    delegate token lifecycle to CodexOAuthClient, which reads/refreshes tokens
    from the llm_models row via the provided async session factory.
    """
    effective_timeout = float(timeout if timeout is not None else (getattr(model, "request_timeout", None) or 120.0))

    if getattr(model, "auth_type", "static") == "codex_oauth":
        return create_llm_client(
            provider=model.provider or "codex-oauth",
            api_key="",
            model=model.model,
            base_url=model.base_url,
            timeout=effective_timeout,
            model_id=model.id,
            session_factory=session_factory or async_session,
        )

    return create_llm_client(
        provider=model.provider,
        api_key=get_model_api_key(model),
        model=model.model,
        base_url=model.base_url,
        timeout=effective_timeout,
    )


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
    "get_model_api_key",
    "get_llm_client_for_model",
    # New client classes
    "LLMClient",
    "OpenAICompatibleClient",
    "OpenAIResponsesClient",
    "GeminiClient",
    "AnthropicClient",
    "CodexOAuthClient",
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
