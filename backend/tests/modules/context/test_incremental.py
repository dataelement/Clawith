"""Old immutable view content is reused without repeated validation or serialization."""

from dataclasses import replace
from uuid import uuid4

import pytest
from pydantic import TypeAdapter

from app.modules.context import public as context
from app.modules.model.public import ModelContent, ModelContextProfile, ModelMessage, ModelToolDefinition


def unit(sequence, text):
    return context.ContextUnit(sequence, (ModelMessage("user", (ModelContent("text", text),)),))


def assembler(summarizer=None, limit=1_000_000):
    return context.ContextAssembler(sources=(context.ContextSource("Platform", "fixed instruction", "system"),),
        profile=ModelContextProfile(uuid4(), "test", "test", limit, 1000, False, False, False), summarizer=summarizer)


async def test_growing_history_only_validates_and_serializes_new_units(monkeypatch):
    current = assembler()
    prepared = await current.prepare(state=context.ContextState(), additions=tuple(unit(i, "old" * 100) for i in range(1, 101)), tools=())
    old_ids = {id(message) for message in prepared.messages}
    validated = []
    serialized = []
    validate = context._validate_unit
    serialize = context.asdict
    def validate_unit(value):
        validated.append(value.sequence)
        return validate(value)
    def asdict(value):
        if isinstance(value, ModelMessage):
            serialized.append(id(value))
        return serialize(value)
    monkeypatch.setattr(context, "_validate_unit", validate_unit)
    monkeypatch.setattr(context, "asdict", asdict)
    for sequence in range(101, 111):
        prepared = await current.prepare(state=prepared.state, additions=(unit(sequence, "new"),), tools=())
        assert prepared.telemetry.validated_units == 1
        assert prepared.telemetry.serialized_messages == 1
        assert prepared.telemetry.reused_units == sequence - 1
    assert validated == list(range(101, 111))
    assert len(serialized) == 10 and not old_ids.intersection(serialized)
    assert prepared.input_tokens == context._tokens(prepared.messages, ())
    assert prepared._encoding.payload == TypeAdapter(context.ContextState).dump_json(prepared.state)
    assert prepared._encoding.digest == context.context_state_hash(prepared.state)


async def test_replaced_state_with_same_sequence_cannot_reuse_cached_content():
    current = assembler()
    first = await current.prepare(state=context.ContextState(), additions=(unit(1, "short"),), tools=())
    changed = replace(first.state, units=(unit(1, "different" * 100),))
    second = await current.prepare(state=changed, additions=(), tools=())
    assert second.telemetry.reused_units == 0
    assert second.telemetry.validated_units == 1
    assert second.input_tokens > first.input_tokens
    assert second.messages[-1].content[0].value == "different" * 100


async def test_tool_definition_change_invalidates_only_tool_cost():
    current = assembler()
    first = await current.prepare(state=context.ContextState(), additions=(unit(1, "work"),), tools=())
    small = (ModelToolDefinition("read", "short", "{}"),)
    second = await current.prepare(state=first.state, additions=(), tools=small)
    large = (replace(small[0], description="larger" * 100),)
    third = await current.prepare(state=second.state, additions=(), tools=large)
    assert third.input_tokens > second.input_tokens > first.input_tokens
    assert third.telemetry.validated_units == third.telemetry.serialized_messages == 0
    assert third.input_tokens == context._tokens(third.messages, large)


async def test_caches_are_per_assembler_and_prefixes_do_not_cross_scopes():
    a = assembler()
    first = await a.prepare(state=context.ContextState(), additions=(unit(1, "work"),), tools=())
    b = context.ContextAssembler(sources=(context.ContextSource("Other", "different source", "user"),),
        profile=ModelContextProfile(uuid4(), "test", "test", 1_000_000, 1000, False, False, False))
    second = await b.prepare(state=first.state, additions=(), tools=())
    assert second.telemetry.reused_units == 0
    assert second.messages[0] != first.messages[0]


async def test_mutable_nested_content_is_rejected_before_cache_population():
    current = assembler()
    invalid = context.ContextUnit(1, [ModelMessage("user", (ModelContent("text", "mutable container"),))])
    with pytest.raises(TypeError, match="immutable"):
        await current.prepare(state=context.ContextState(), additions=(invalid,), tools=())


async def test_summary_reset_reuses_only_the_new_observed_base():
    class Summary:
        async def summarize(self, **kwargs):
            return context.ContextSummary("objective", "", "progress", "", "", "continue", "")
    current = assembler(Summary(), 6000)
    first = await current.prepare(state=context.ContextState(), additions=(unit(1, "old " * 2000), unit(2, "recent")), tools=())
    assert first.telemetry.compactions == 1
    second = await current.prepare(state=first.state, additions=(unit(3, "new"),), tools=())
    assert second.telemetry.validated_units == second.telemetry.serialized_messages == 1
    assert second.telemetry.reused_units == 1
    assert second.input_tokens == context._tokens(second.messages, ())
    assert second.state.coverage_sequence == 1
    assert second._encoding.payload == TypeAdapter(context.ContextState).dump_json(second.state)


async def test_prepared_encoding_matches_v1_for_unicode_escapes_and_tool_exchange():
    from app.modules.model.public import ModelToolCall
    current = assembler()
    exchange = context.ContextUnit(1, (ModelMessage("assistant", calls=(ModelToolCall("call", "read", '{"text":"中\\n"}'),)),
        ModelMessage("tool", (ModelContent("text", 'result: 中\n\t"\\\u2028'),), call_id="call", is_error=True)))
    prepared = await current.prepare(state=context.ContextState(), additions=(exchange,), tools=())
    assert prepared._encoding.payload == TypeAdapter(context.ContextState).dump_json(prepared.state)
    assert prepared._encoding.digest == context.context_state_hash(prepared.state)


async def test_cleared_tool_base_is_reused_without_reencoding_omitted_content():
    from app.modules.model.public import ModelToolCall
    current = assembler(limit=6000)
    exchange = context.ContextUnit(1, (ModelMessage("assistant", calls=(ModelToolCall("call", "read", "{}"),)),
        ModelMessage("tool", (ModelContent("text", "old output " * 1500),), call_id="call")))
    first = await current.prepare(state=context.ContextState(), additions=(exchange, unit(2, "recent")), tools=())
    assert first.telemetry.cleared_tool_tokens > 0
    second = await current.prepare(state=first.state, additions=(unit(3, "delta"),), tools=())
    assert second.telemetry.validated_units == second.telemetry.serialized_messages == 1
    assert second.telemetry.reused_units == 2
    assert second._encoding.payload == TypeAdapter(context.ContextState).dump_json(second.state)


async def test_local_assembly_telemetry_excludes_separate_summary_wait(monkeypatch):
    class Summary:
        async def summarize(self, **kwargs):
            return context.ContextSummary("objective", "", "progress", "", "", "continue", "")
    current = assembler(Summary(), 6000)
    ticks = iter((0.0, 1.0, 10.0, 11.0))
    monkeypatch.setattr(context, "perf_counter", lambda: next(ticks))
    prepared = await current.prepare(state=context.ContextState(), additions=(unit(1, "old " * 2000), unit(2, "recent")), tools=())
    assert prepared.telemetry.compaction_seconds == 9
    assert prepared.telemetry.assembly_seconds == 2
