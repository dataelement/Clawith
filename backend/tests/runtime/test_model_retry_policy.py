import pytest

from app.modules.model.public import ModelFailure
from app.modules.run import engine


def test_default_attempt_count_and_delays_remain_unchanged():
    assert engine._MODEL_RETRY_DELAYS == (0.25, 0.5)
    failure = ModelFailure("transport_failed", "Temporary error")
    assert [engine.RunRuntime._retryable(failure, number) for number in (1, 2, 3)] == [True, True, False]


@pytest.mark.parametrize("delays", [(), (0.1,), (0.1, 0.2, 0.4, 0.8)])
def test_attempt_limit_follows_the_single_policy_tuple(monkeypatch, delays):
    monkeypatch.setattr(engine, "_MODEL_RETRY_DELAYS", delays)
    failure = ModelFailure("rate_limited", "Temporary error")
    for number in range(1, len(delays) + 2):
        assert engine.RunRuntime._retryable(failure, number) is (number <= len(delays))
    assert not engine.RunRuntime._retryable(ModelFailure("invalid_input", "Invalid"), 1)
    assert not engine.RunRuntime._retryable(ModelFailure("transport_failed", "Invalid", True), 1)
