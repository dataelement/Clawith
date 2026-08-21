"""LLM failover error-classification regressions."""

import pytest

from app.services.llm.client import LLMVisibleStreamInterrupted
from app.services.llm.failover import (
    FailoverErrorType,
    classify_error,
    is_retryable_classification,
)


@pytest.mark.parametrize(
    "message",
    [
        "HTTP 402 Payment Required",
        "Payment Required",
        "Insufficient Balance",
        "billing quota exhausted",
    ],
)
def test_payment_and_billing_failures_are_non_retryable(message: str) -> None:
    classification = classify_error(RuntimeError(message))

    assert classification is FailoverErrorType.NON_RETRYABLE
    assert is_retryable_classification(classification) is False


def test_unknown_provider_failure_keeps_retryable_semantics() -> None:
    classification = classify_error(RuntimeError("provider returned an unrecognized failure"))

    assert classification is FailoverErrorType.UNKNOWN
    assert is_retryable_classification(classification) is True


def test_visible_stream_interruption_is_never_retried_or_failed_over() -> None:
    classification = classify_error(
        LLMVisibleStreamInterrupted(
            "Provider stream interrupted after visible output was published"
        )
    )

    assert classification is FailoverErrorType.NON_RETRYABLE
    assert is_retryable_classification(classification) is False


@pytest.mark.parametrize(
    "message",
    [
        "billing service unavailable HTTP 503",
        "billing gateway timeout",
    ],
)
def test_transient_billing_failures_remain_retryable(message: str) -> None:
    classification = classify_error(RuntimeError(message))

    assert classification is FailoverErrorType.RETRYABLE
    assert is_retryable_classification(classification) is True
