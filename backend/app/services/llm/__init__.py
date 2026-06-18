"""LLM service module - unified LLM calling interface.

This module provides:
- call_llm: Basic LLM call with tool support
- call_llm_with_failover: LLM call with automatic failover
- call_agent_llm: Agent chat LLM call
- call_agent_llm_with_tools: Agent LLM call with tools for background tasks

Example:
    from app.services.llm import call_llm, call_llm_with_failover

    # Basic call
    reply = await call_llm(model, messages, agent_name, role_description)

    # With failover
    reply = await call_llm_with_failover(
        primary_model=primary,
        fallback_model=fallback,
        messages=messages,
        ...
    )
"""

from .client import LLMClient, LLMResponse, LLMError, LLMMessage
from .failover import classify_error, FailoverErrorType
from .utils import create_llm_client, get_max_tokens, get_model_api_key, get_provider_base_url, get_provider_manifest

# Lazy re-export of ``.caller`` to break the
# ``agent_tools`` → ``llm.finish`` → ``llm/__init__`` → ``llm.caller``
# → ``agent_tools`` cycle that surfaces whenever a caller imports
# ``app.services.agent_tools`` before any other ``llm`` submodule
# (e.g. ``tests/test_mcp_recovery.py``, ``tests/test_custom_image_tool.py``).
# Symbols are still accessible via ``from app.services.llm import call_llm``;
# they just resolve on first attribute access instead of at package load time.
_LAZY_CALLER_NAMES = frozenset({
    "call_llm",
    "call_llm_with_failover",
    "call_agent_llm",
    "call_agent_llm_with_tools",
    "FailoverGuard",
    "is_retryable_error",
})


def __getattr__(name: str):
    if name in _LAZY_CALLER_NAMES:
        from . import caller as _caller
        value = getattr(_caller, name)
        globals()[name] = value  # cache for subsequent lookups
        return value
    raise AttributeError(f"module 'app.services.llm' has no attribute {name!r}")

__all__ = [
    # Core caller functions
    "call_llm",
    "call_llm_with_failover",
    "call_agent_llm",
    "call_agent_llm_with_tools",
    # Failover utilities
    "FailoverGuard",
    "is_retryable_error",
    "classify_error",
    "FailoverErrorType",
    # Client classes
    "LLMClient",
    "LLMResponse",
    "LLMError",
    "LLMMessage",
    # Utilities
    "create_llm_client",
    "get_max_tokens",
    "get_model_api_key",
    "get_provider_base_url",
    "get_provider_manifest",
]
