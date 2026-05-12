"""Unified LLM failover error classification.

Provides error classification for failover decisions across all execution paths.
"""

from __future__ import annotations

from enum import Enum

from .client import LLMError


class FailoverErrorType(Enum):
    """Classification of LLM errors for failover decisions."""

    RETRYABLE = "retryable"  # Network timeout, 429, 5xx, transient errors
    NON_RETRYABLE = "non_retryable"  # Auth, validation, schema errors
    UNKNOWN = "unknown"


def classify_error(error: Exception) -> FailoverErrorType:
    """Classify an exception as retryable or non-retryable.

    Retryable errors:
    - Network timeout / connection errors
    - Provider 429 (rate limit)
    - Provider 5xx (server errors)
    - Explicit transient provider errors

    Non-retryable errors:
    - Auth errors (401, 403)
    - Validation errors (400, 422)
    - Schema errors
    - Content policy violations
    """
    error_msg = str(error).lower()

    # Non-retryable: authentication and authorization
    if any(kw in error_msg for kw in ["auth", "unauthorized", "forbidden", "invalid api key", "api key invalid"]):
        return FailoverErrorType.NON_RETRYABLE

    # Non-retryable: validation and schema
    if any(kw in error_msg for kw in ["validation", "invalid request", "schema", "bad request"]):
        return FailoverErrorType.NON_RETRYABLE

    # Non-retryable: content policy
    if any(kw in error_msg for kw in ["content policy", "content_filter", "safety", "moderation"]):
        return FailoverErrorType.NON_RETRYABLE

    # Retryable: rate limiting
    if any(kw in error_msg for kw in ["rate limit", "429", "too many requests"]):
        return FailoverErrorType.RETRYABLE

    # Retryable: server errors
    if any(kw in error_msg for kw in ["500", "502", "503", "504", "server error", "internal error"]):
        return FailoverErrorType.RETRYABLE

    # Retryable: network and timeout
    if any(kw in error_msg for kw in ["timeout", "connection", "network", "unreachable", "refused", "reset", "dns"]):
        return FailoverErrorType.RETRYABLE

    # Retryable: transient errors
    if any(kw in error_msg for kw in ["temporary", "transient", "unavailable", "overloaded", "busy"]):
        return FailoverErrorType.RETRYABLE

    # LLMError with specific patterns
    if isinstance(error, (LLMError, Exception)):
        # Check the error message for HTTP status codes
        if any(code in error_msg for code in ["401", "403", "400", "422"]):
            return FailoverErrorType.NON_RETRYABLE
        if any(code in error_msg for code in ["429", "500", "502", "503", "504", "408"]):
            return FailoverErrorType.RETRYABLE
        
        # If it's an error result string, it's likely retryable by default
        if error_msg.startswith("[llm error]") or error_msg.startswith("[llm call error]") or error_msg.startswith("[error]"):
            return FailoverErrorType.RETRYABLE

    return FailoverErrorType.UNKNOWN


__all__ = [
    "FailoverErrorType",
    "classify_error",
]
