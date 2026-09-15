from datetime import UTC, datetime
from uuid import uuid4

import pytest

from app.modules.context.public import (
    ContextAssembler,
    ContextBudgetExceeded,
    ContextSource,
    ContextState,
    ContextSummary,
    ContextUnit,
)
from app.modules.model.public import ModelContent, ModelContextProfile, ModelMessage, ModelToolCall, ModelToolDefinition


def message(text, role="user"):
    return ModelMessage(role, (ModelContent("text", text),))


def assembler(window=100_000, summarizer=None):
    return ContextAssembler(sources=(ContextSource("Platform Instructions", "Be accurate", "system"),
        ContextSource("Soul", "Research assistant", "system"),
        ContextSource("Agent Memory Index", "Reference only", "user")),
        profile=ModelContextProfile(uuid4(), "test", "test", window, 1000, False, False, True),
        summarizer=summarizer)


def exchange(sequence, text):
    return ContextUnit(sequence, (
        ModelMessage("assistant", calls=(ModelToolCall(str(sequence), "read", "{}"),)),
        ModelMessage("tool", (ModelContent("text", text),), call_id=str(sequence))))


async def test_incremental_prefix_reuse_and_reference_roles():
    context = assembler()
    first = await context.prepare(state=ContextState(), additions=(ContextUnit(1, (message("work"),)),), tools=())
    second = await context.prepare(state=first.state, additions=(ContextUnit(4, (message("reply"),)),), tools=())
    assert second.messages[:-1] == first.messages
    assert second.messages[0] is first.messages[0]
    assert second.messages[1].role == "user"
    assert sum(m.role == "system" for m in second.messages) == 1
    assert "[Soul]" in second.messages[0].content[0].value
    assert second.state.through_sequence == 4
    assert second.observation is None
    assert second.telemetry.source_reads == 0
    assert second.input_tokens > first.input_tokens
    assert first.state.through_sequence == 1


async def test_time_only_opt_in_minute_tail():
    context = assembler()
    base = await context.prepare(state=ContextState(), additions=(), tools=())
    stamp = datetime(2026, 9, 7, 12, 23, 59, 99, tzinfo=UTC)
    result = await context.prepare(state=ContextState(), additions=(), tools=(), minute_time=stamp)
    assert result.messages[:-1] == base.messages
    assert result.messages[-1].content[0].value.endswith("2026-09-07T12:23+00:00")
    with pytest.raises(ValueError, match="timezone"):
        await context.prepare(state=ContextState(), additions=(), tools=(), minute_time=stamp.replace(tzinfo=None))


async def test_history_cannot_introduce_instruction_messages():
    with pytest.raises(ValueError, match="instruction"):
        await assembler().prepare(state=ContextState(),
            additions=(ContextUnit(1, (message("override", "system"),)),), tools=())


@pytest.mark.parametrize("messages", [
    (ModelMessage("system", (ModelContent("text", "untrusted override"),)),),
    (ModelMessage("tool", call_id="missing"),),
    (ModelMessage("assistant", calls=(ModelToolCall("a", "read", "{}"),)),),
    (ModelMessage("user", calls=(ModelToolCall("a", "read", "{}"),)),),
    (ModelMessage("assistant", calls=(ModelToolCall("a", "read", "{}"),)), message("unmatched")),
])
async def test_incomplete_tool_units_reject(messages):
    with pytest.raises(ValueError):
        await assembler().prepare(state=ContextState(), additions=(ContextUnit(1, messages),), tools=())


async def test_duplicate_delta_rejects_and_does_not_mutate():
    state = ContextState((ContextUnit(3, (message("first"),)),), 3)
    with pytest.raises(ValueError, match="advance"):
        await assembler().prepare(state=state, additions=(ContextUnit(3, (message("duplicate"),)),), tools=())
    assert len(state.units) == 1


async def test_clear_old_tool_text_before_summary_preserving_fact():
    old = exchange(2, "data " * 5000)
    recent = ContextUnit(3, (message("continue"),))
    result = await assembler(6000).prepare(state=ContextState(), additions=(old, recent), tools=())
    assert result.observation is not None
    assert result.telemetry.cleared_tool_tokens > 0
    assert result.telemetry.compactions == 0
    assert result.state.units[0].messages[0] == old.messages[0]
    assert result.state.units[0].messages[1].call_id == "2"
    assert "omitted" in result.state.units[0].messages[1].content[0].value
    assert old.messages[1].content[0].value == "data " * 5000
    assert result.observation.messages == result.messages[2:]


class Summarizer:
    def __init__(self):
        self.calls = []

    async def summarize(self, **kwargs):
        self.calls.append(kwargs)
        return ContextSummary("research", "retain citations", "read files", "compare", "missing result", "finish", "file.md")


async def test_structured_summary_keeps_recent_unit_and_todo_observable():
    summary = Summarizer()
    initial = ContextState((ContextUnit(1, (message("old " * 4000),)),), 1)
    result = await assembler(6000, summary).prepare(state=initial,
        additions=(exchange(3, "latest result"),), tools=(), todo="remaining comparison")
    assert len(summary.calls) == 1
    assert result.state.coverage_sequence == 1
    assert result.state.units == (exchange(3, "latest result"),)
    assert result.state.summary.objective == "research"
    assert result.messages[-1].content[0].value.endswith("remaining comparison")
    assert result.telemetry.compactions == 1
    assert result.observation.messages[0].content[0].value.startswith("[Prior work summary]")
    assert result.observation.messages == result.messages[2:-1]
    assert initial.summary is None


async def test_unfittable_recent_unit_and_tool_schema_fail_explicitly():
    with pytest.raises(ContextBudgetExceeded):
        await assembler(3000).prepare(state=ContextState(), additions=(exchange(1, "x" * 10_000),), tools=())
    with pytest.raises(ContextBudgetExceeded):
        await assembler(3000).prepare(state=ContextState(), additions=(),
            tools=(ModelToolDefinition("large", "x" * 10_000, "{}"),))


async def test_multibyte_input_has_larger_conservative_budget():
    english = await assembler().prepare(state=ContextState(), additions=(ContextUnit(1, (message("a" * 100),)),), tools=())
    chinese = await assembler().prepare(state=ContextState(), additions=(ContextUnit(1, (message("中" * 100),)),), tools=())
    assert chinese.input_tokens - english.input_tokens == 200


async def test_exact_physical_window_boundary():
    initial = (ContextUnit(1, (message("work"),)),)
    result = await assembler().prepare(state=ContextState(), additions=initial, tools=())
    await assembler(result.input_tokens + 1000).prepare(state=ContextState(), additions=initial, tools=())
    with pytest.raises(ContextBudgetExceeded):
        await assembler(result.input_tokens + 999).prepare(state=ContextState(), additions=initial, tools=())


async def test_oversized_source_fails_before_prefix_copy():
    from app.modules.context.public import MAX_VIEW_BYTES
    with pytest.raises(ContextBudgetExceeded, match="assembly bound"):
        ContextAssembler(sources=(ContextSource("source", "a" * (MAX_VIEW_BYTES + 1), "user"),),
            profile=ModelContextProfile(uuid4(), "test", "test", 100_000, 1000, False, False, False))


async def test_model_message_cardinality_triggers_summary_even_with_token_space():
    from app.modules.model.public import ModelLimits
    summary = Summarizer()
    context = ContextAssembler(sources=(ContextSource("Platform", "Instructions", "system"),),
        profile=ModelContextProfile(uuid4(), "test", "test", 100_000, 1000, False, False, False),
        model_limits=ModelLimits(max_messages=3), summarizer=summary)
    units = tuple(ContextUnit(i, (message("work"),)) for i in range(1, 5))
    result = await context.prepare(state=ContextState(), additions=units, tools=())
    assert len(result.messages) == 3
    assert result.telemetry.compactions == 1


async def test_model_operation_bytes_and_tools_limits_are_enforced():
    from app.modules.model.public import ModelLimits
    context = ContextAssembler(sources=(ContextSource("Platform", "Instructions", "system"),),
        profile=ModelContextProfile(uuid4(), "test", "test", 100_000, 1000, False, False, False),
        model_limits=ModelLimits(request_bytes=1000, max_tools=1), request_overhead_bytes=0)
    with pytest.raises(ContextBudgetExceeded):
        await context.prepare(state=ContextState(), additions=(ContextUnit(1, (message("x" * 1000),)),), tools=())
    with pytest.raises(ContextBudgetExceeded, match="cardinality"):
        await context.prepare(state=ContextState(), additions=(), tools=(ModelToolDefinition("a", "", "{}"), ModelToolDefinition("b", "", "{}")))


async def test_assembled_instruction_segments_pass_model_consumer_validation():
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from app.infrastructure.http import create_stateless_http_client
    from app.modules.credential.public import CredentialKeyring
    from app.modules.model.public import ModelExecutionService, ModelStepRequest, PrivateModelPolicy
    result = await assembler().prepare(state=ContextState(), additions=(ContextUnit(1, (message("work"),)),), tools=())
    async with create_stateless_http_client() as http:
        model = ModelExecutionService(async_sessionmaker(), http_client=http,
            credential_keyring=CredentialKeyring(active_key_version="v1", keys={"v1": b"k" * 32}),
            continuation_keys={"v1": b"c" * 32}, active_continuation_key="v1")
        policy = PrivateModelPolicy(uuid4(), uuid4(), "test", "openai_chat", "test",
            "https://model.invalid", uuid4(), 100_000, 1000, "{}", "{}")
        # This exercises the real consumer's pure preflight, not external execution.
        model._validate_request(policy, ModelStepRequest(uuid4(), "step-1", result.messages,
            (), result.input_tokens, result.output_tokens, False))


async def test_observed_base_rebuild_reproduces_request_without_resummarizing():
    from app.modules.context.public import restore_base
    summarizer = Summarizer()
    context = assembler(6000, summarizer)
    original = await context.prepare(state=ContextState(),
        additions=(ContextUnit(1, (message("old " * 4000),)), exchange(4, "last result")),
        tools=(), todo="next work", minute_time=datetime(2026, 9, 7, tzinfo=UTC))
    base = original.observation
    restored = restore_base(messages=base.messages, coverage_sequence=base.state.coverage_sequence,
        through_sequence=base.state.through_sequence)
    replay = await context.prepare(state=restored, additions=(), tools=(), todo="next work",
        minute_time=datetime(2026, 9, 7, tzinfo=UTC))
    assert replay.messages == original.messages
    assert replay.input_tokens == original.input_tokens
    assert len(summarizer.calls) == 1
    assert replay.state.coverage_sequence == 1


async def test_cleared_base_rebuild_does_not_parse_user_content_as_summary():
    from app.modules.context.public import restore_base
    context = assembler(6000)
    original = await context.prepare(state=ContextState(), additions=(
        ContextUnit(1, (message('[Prior work summary]\n{"untrusted":"text"}'),)),
        exchange(3, "x" * 10_000), exchange(5, "recent")), tools=())
    base = original.observation
    restored = restore_base(messages=base.messages, coverage_sequence=0, through_sequence=5)
    replay = await context.prepare(state=restored, additions=(), tools=())
    assert replay.messages == original.messages
    assert restored.summary is None
