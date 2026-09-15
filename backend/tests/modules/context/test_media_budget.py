import asyncio
from dataclasses import replace
from uuid import uuid4

import pytest

from app.modules.context.public import (
    ContextAssembler,
    ContextBudgetExceeded,
    ContextSource,
    ContextState,
    ContextUnit,
    ModelPreparationFailure,
)
from app.modules.model.public import (
    ModelContent,
    ModelContextProfile,
    ModelFailure,
    ModelLimits,
    ModelMessage,
    ModelToolCall,
    ModelToolDefinition,
)

IMAGE = "data:image/png;base64," + "a" * 50000


def image_unit(sequence=1, value=IMAGE):
    return ContextUnit(sequence, (ModelMessage("user", (ModelContent("image", value),)),))


def assembler(counter=None, *, supports=True, limits=None, context_limit=10000):
    return ContextAssembler(sources=(ContextSource("Platform", "accurate", "system"),),
        profile=ModelContextProfile(uuid4(), "fixture", "fixture", context_limit, 1000, supports, False, False),
        token_counter=counter, model_limits=limits or ModelLimits(), request_overhead_bytes=0)


class Counter:
    def __init__(self):
        self.calls = []

    async def __call__(self, messages, tools):
        self.calls.append((messages, tools))
        return 200 + sum(len(part.value) for message in messages for part in message.content if part.kind == "text") + sum(len(tool.description) for tool in tools)


async def test_image_uses_whole_request_count_not_base64_bytes_and_identical_request_reuses():
    counter = Counter()
    current = assembler(counter)
    first = await current.prepare(state=ContextState(), additions=(image_unit(),), tools=())
    assert first.input_tokens < 1000
    assert first.messages[-1].content[0].value == IMAGE
    second = await current.prepare(state=first.state, additions=(), tools=())
    assert second.input_tokens == first.input_tokens
    assert len(counter.calls) == 1
    assert second.telemetry.token_counting_calls == 0
    extra = ContextUnit(2, (ModelMessage("user", (ModelContent("text", "new question"),)),))
    third = await current.prepare(state=second.state, additions=(extra,), tools=())
    assert len(counter.calls) == 2 and third.input_tokens > second.input_tokens
    fourth = await current.prepare(state=third.state, additions=(), tools=(ModelToolDefinition("tool", "changed", "{}"),))
    assert len(counter.calls) == 3 and fourth.input_tokens > third.input_tokens


async def test_text_only_view_never_calls_network_counter():
    async def forbidden(*args):
        pytest.fail("Text-only preparation called remote token metadata")
    prepared = await assembler(forbidden).prepare(state=ContextState(), additions=(ContextUnit(1,
        (ModelMessage("user", (ModelContent("text", "work"),)),)),), tools=())
    assert prepared.telemetry.token_counting_calls == 0


@pytest.mark.parametrize("supports,counter", [(False, Counter()), (True, None)])
async def test_image_requires_fixed_model_support_and_authoritative_counter(supports, counter):
    with pytest.raises(ModelPreparationFailure) as error:
        await assembler(counter, supports=supports).prepare(state=ContextState(), additions=(image_unit(),), tools=())
    assert error.value.failure.unrecoverable


async def test_transport_byte_bound_is_checked_before_image_counter():
    counter = Counter()
    with pytest.raises(ContextBudgetExceeded):
        await assembler(counter, limits=ModelLimits(request_bytes=4000)).prepare(state=ContextState(), additions=(image_unit(),), tools=())
    assert counter.calls == []


async def test_counter_failure_is_preserved_without_internal_retry():
    calls = 0
    failure = ModelFailure("rate_limited", "try later", False)
    async def broken(*args):
        nonlocal calls
        calls += 1
        raise ModelPreparationFailure(failure)
    with pytest.raises(ModelPreparationFailure) as error:
        await assembler(broken).prepare(state=ContextState(), additions=(image_unit(),), tools=())
    assert error.value.failure is failure and calls == 1


async def test_counter_deadline_normalizes_timeout_and_cancels_actual_work():
    stopped = asyncio.Event()
    async def blocked(*args):
        try:
            await asyncio.Future()
        finally:
            stopped.set()
    with pytest.raises(ModelPreparationFailure) as error:
        await assembler(blocked, limits=ModelLimits(timeout_seconds=.01)).prepare(state=ContextState(), additions=(image_unit(),), tools=())
    assert error.value.failure.code == "transport_failed" and stopped.is_set()


async def test_old_tool_image_is_explicitly_cleared_but_recent_image_preserved():
    counted = []
    async def count(messages, tools):
        images = sum(part.kind == "image" for message in messages for part in message.content)
        counted.append(images)
        return images * 3000 + 100
    old = ContextUnit(1, (ModelMessage("assistant", calls=(ModelToolCall("read", "image", "{}"),)),
        ModelMessage("tool", (ModelContent("image", IMAGE),), call_id="read")))
    latest = image_unit(2)
    prepared = await assembler(count, context_limit=6000).prepare(state=ContextState(), additions=(old, latest), tools=())
    assert counted == [2, 1]
    assert prepared.state.units[-1] == latest
    assert "omitted" in prepared.state.units[0].messages[-1].content[0].value
    assert old.messages[-1].content[0].kind == "image"
    assert prepared.telemetry.token_counting_calls == 2


async def test_replaced_image_with_same_sequence_cannot_reuse_old_count():
    counter = Counter()
    current = assembler(counter)
    first = await current.prepare(state=ContextState(), additions=(image_unit(),), tools=())
    changed = replace(first.state, units=(image_unit(value=IMAGE + "bbbb"),))
    await current.prepare(state=changed, additions=(), tools=())
    assert len(counter.calls) == 2


async def test_latest_image_is_not_silently_removed_to_fit_tokens():
    calls = 0
    async def too_many(messages, tools):
        nonlocal calls
        calls += 1
        return 999999
    latest = image_unit()
    with pytest.raises(ContextBudgetExceeded):
        await assembler(too_many).prepare(state=ContextState(), additions=(latest,), tools=())
    assert calls == 1
    assert latest.messages[0].content[0].value == IMAGE


async def test_count_cache_is_not_shared_between_assemblers():
    counter = Counter()
    a, b = assembler(counter), assembler(counter)
    first = await a.prepare(state=ContextState(), additions=(image_unit(),), tools=())
    await b.prepare(state=first.state, additions=(), tools=())
    assert len(counter.calls) == 2


async def test_successful_summary_is_not_repeated_after_later_count_failure():
    from app.modules.context.public import ContextSummary
    summary_calls = 0
    failed_once = False
    class Summary:
        async def summarize(self, **kwargs):
            nonlocal summary_calls
            summary_calls += 1
            return ContextSummary("objective", "", "progress", "", "", "continue", "")
    async def counter(messages, tools):
        nonlocal failed_once
        text = "".join(part.value for message in messages for part in message.content if part.kind == "text")
        if "[Prior work summary]" in text:
            if not failed_once:
                failed_once = True
                raise ModelPreparationFailure(ModelFailure("rate_limited", "later", False))
            return 2000
        return 7000 if "older source" in text else 1000
    current = ContextAssembler(sources=(ContextSource("Platform", "accurate", "system"),),
        profile=ModelContextProfile(uuid4(), "fixture", "fixture", 6000, 1000, True, False, False),
        token_counter=counter, summarizer=Summary())
    initial = ContextState()
    additions = (ContextUnit(1, (ModelMessage("user", (ModelContent("text", "older source"),)),)), image_unit(2))
    with pytest.raises(ModelPreparationFailure):
        await current.prepare(state=initial, additions=additions, tools=())
    assert summary_calls == 1
    prepared = await current.prepare(state=initial, additions=additions, tools=())
    assert summary_calls == 1 and prepared.telemetry.compactions == 1
    assert prepared.input_tokens == 2000
    assert prepared.state.units[-1] == additions[-1]
    await current.prepare(state=prepared.state, additions=(), tools=())
    assert current._summary_key is None and current._summary_result is None
