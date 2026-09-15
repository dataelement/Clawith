from dataclasses import replace

from app.execution_dependencies.resources import ContextStatistics
from app.modules.context.public import ContextTelemetry


def test_context_statistics_retains_unknown_counts_without_inventing_zero():
    statistics = ContextStatistics()
    measured = ContextTelemetry(assembly_seconds=0.1, input_tokens=120, source_reads=2,
        cleared_tool_tokens=12, compactions=1, coverage_sequence=8,
        token_counting_calls=1, token_counting_seconds=0.5)
    statistics.observe(measured)
    statistics.observe(replace(measured, cleared_tool_tokens=None, coverage_sequence=10))
    values = statistics.snapshot()
    assert values["preparations"] == 2
    assert values["input_tokens"] == 240
    assert values["cleared_tool_tokens"] == 12
    assert values["cleared_tool_tokens_unknown"] == 1
    assert values["token_counting_calls"] == 2
    assert values["token_counting_seconds"] == 1
    assert values["last_coverage_sequence"] == 10
    values["preparations"] = -1
    assert statistics.snapshot()["preparations"] == 2


def test_context_statistics_cardinality_does_not_grow_with_preparations():
    statistics = ContextStatistics()
    measured = ContextTelemetry(0, 1, 0, None, 0, 0)
    statistics.observe(measured)
    keys = set(statistics.snapshot())
    for sequence in range(1000):
        statistics.observe(replace(measured, coverage_sequence=sequence))
    assert set(statistics.snapshot()) == keys
    assert statistics.snapshot()["preparations"] == 1001
