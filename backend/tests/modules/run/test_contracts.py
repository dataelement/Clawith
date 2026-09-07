import json
from dataclasses import replace

import pytest

from app.modules.model.public import ModelContent, ModelMessage, ModelStepResult, ModelToolCall, ModelUsage
from app.modules.run import contracts
from app.modules.run.contracts import (
    MAX_INPUT_BYTES,
    MAX_RECORD_BYTES,
    ContextBasePayload,
    InitialInputPayload,
    InputContent,
    InputReference,
    InvalidHistory,
    ModelInputPayload,
    ModelStepPayload,
    RelatedInputPayload,
    TerminalOutcomePayload,
    ToolResultPayload,
    WaitingPayload,
    decode_history,
    encode_history,
)
from app.modules.tool.public import ToolResult


def model_payload():
    return ModelStepPayload("step-1", 17, ModelStepResult("研究完成 ✓", (
        ModelToolCall("call-1", "read_file", '{ "path" : "项目/文件.md" }'),
    ), "tool_calls", ModelUsage(100, 5, 70, 10, 3), "interaction-1", True))


@pytest.mark.parametrize("payload", [
    InitialInputPayload(InputContent("查阅附件", (InputReference("attachment:one", "输入.pdf", "application/pdf"),))),
    RelatedInputPayload(InputContent("继续 ✓")), model_payload(),
    ToolResultPayload("step-1", "read_file", ToolResult("call-1", "success", '{ "text": "内容 ✓" }')),
    WaitingPayload("step-1", "question-1", "请选择文件", 17),
    WaitingPayload("step-1", "child-result", "", 17),
    *(TerminalOutcomePayload(status, "输出", "原因") for status in ("Completed", "Failed", "Cancelled", "Interrupted")),
])
def test_exact_roundtrip_through_real_json_storage(payload):
    record = encode_history(payload)
    persisted = json.loads(json.dumps(record.payload, ensure_ascii=False))
    assert decode_history(record.kind, record.version, persisted) == payload


def test_usage_none_zero_and_exact_tool_argument_format_are_preserved():
    payload = model_payload()
    payload = replace(payload, result=replace(payload.result, usage=ModelUsage(None, 0, None, 0, None)))
    record = encode_history(payload)
    assert decode_history(record.kind, record.version, record.payload) == payload
    assert record.payload["result"]["calls"][0]["arguments_json"] == '{ "path" : "项目/文件.md" }'


def test_encoder_returns_detached_mutable_storage_data():
    payload = InitialInputPayload(InputContent("original", (InputReference("file:1"),)))
    first = encode_history(payload)
    first.payload["input"]["references"][0]["reference"] = "changed"
    first.payload["input"]["text"] = "changed"
    second = encode_history(payload)
    assert second.payload["input"]["text"] == "original"
    assert payload.input.references[0].reference == "file:1"


@pytest.mark.parametrize("kind,version", [("future", 1), ("initial_input", 2), ("initial_input", True), ("initial_input", "1")])
def test_unknown_kind_and_version_never_fallback(kind, version):
    with pytest.raises(InvalidHistory):
        decode_history(kind, version, {})


@pytest.mark.parametrize("field", ["extra", "coverage_sequence"])
def test_closed_model_payload_rejects_extra_fields_and_projection_cursor_substitution(field):
    record = encode_history(model_payload())
    record.payload[field] = 42
    if field == "coverage_sequence":
        del record.payload["read_through_sequence"]
    with pytest.raises(InvalidHistory):
        decode_history(record.kind, record.version, record.payload)


@pytest.mark.parametrize("mutation", ["extra_usage", "negative_usage", "boolean_sequence", "missing_interaction", "duplicate_call", "wrong_status"])
def test_malformed_normalized_model_result_is_rejected(mutation):
    record = encode_history(model_payload())
    result = record.payload["result"]
    if mutation == "extra_usage":
        result["usage"]["invented_counter"] = 1
    elif mutation == "negative_usage":
        result["usage"]["input_tokens"] = -1
    elif mutation == "boolean_sequence":
        record.payload["read_through_sequence"] = True
    elif mutation == "missing_interaction":
        del result["interaction_id"]
    elif mutation == "duplicate_call":
        result["calls"].append(dict(result["calls"][0]))
    else:
        result["finish_reason"] = "invented"
    with pytest.raises(InvalidHistory):
        decode_history(record.kind, record.version, record.payload)


@pytest.mark.parametrize("arguments", ['{"x":NaN}', '{"x":Infinity}', '[]', '{bad', '{"x":' + '[' * 40 + '0' + ']' * 40 + '}'])
def test_embedded_json_requires_finite_bounded_object(arguments):
    payload = model_payload()
    changed = replace(payload, result=replace(payload.result, calls=(ModelToolCall("call", "read", arguments),)))
    with pytest.raises(InvalidHistory):
        encode_history(changed)


def test_invalid_authoritative_values_are_not_leaked():
    raw = {"input": {"text": 123, "references": [], "unexpected": "private-source-value"}}
    with pytest.raises(InvalidHistory) as error:
        decode_history("initial_input", 1, raw)
    assert "private-source-value" not in str(error.value)
    with pytest.raises(InvalidHistory):
        encode_history(InitialInputPayload(InputContent("\ud800")))


def test_whole_record_byte_boundary_includes_kind_version_and_wrapper():
    empty = encode_history(TerminalOutcomePayload("Completed"))
    overhead = len(json.dumps({"kind": empty.kind, "version": empty.version, "payload": empty.payload},
        ensure_ascii=False, separators=(",", ":")).encode())
    size = MAX_RECORD_BYTES - overhead
    for length in (size - 1, size):
        encode_history(TerminalOutcomePayload("Completed", "x" * length))
    with pytest.raises(InvalidHistory):
        encode_history(TerminalOutcomePayload("Completed", "x" * (size + 1)))


def test_input_utf8_bound_and_reference_count():
    overhead = len(json.dumps({"text": "", "references": []}, separators=(",", ":")).encode())
    count = (MAX_INPUT_BYTES - overhead) // 3
    encode_history(InitialInputPayload(InputContent("中" * count)))
    with pytest.raises(InvalidHistory):
        encode_history(InitialInputPayload(InputContent("中" * (count + 1))))
    encode_history(InitialInputPayload(InputContent("", tuple(InputReference(str(i)) for i in range(64)))))
    with pytest.raises(InvalidHistory):
        encode_history(InitialInputPayload(InputContent("", tuple(InputReference(str(i)) for i in range(65)))))


def test_unknown_terminal_status_and_invalid_tool_result_are_not_accepted():
    with pytest.raises(InvalidHistory):
        decode_history("terminal_outcome", 1, {"status": "Waiting", "output": "", "reason": None})
    with pytest.raises(InvalidHistory):
        decode_history("tool_result", 1, {"step_id": "step", "tool_name": "tool", "result": {
            "call_id": "call", "status": "success", "content_json": '[]'}})


def test_non_json_and_cyclic_payloads_fail_bounded():
    cyclic = {}
    cyclic["self"] = cyclic
    for payload in (cyclic, {"x": float("nan")}, {1: "value"}, {"bytes": b"data"}):
        with pytest.raises(InvalidHistory):
            decode_history("initial_input", 1, payload)


@pytest.mark.parametrize("value", [
    [None] * 7 + [[object()] * 5],
    {"wide": {str(index): object() for index in range(5)}, "pending": None},
])
def test_nested_width_reserves_pending_node_budget_before_expanding(monkeypatch, value):
    monkeypatch.setattr(contracts, "MAX_NODES", 12)
    # Invalid children would raise a different error if traversal expanded the over-budget branch.
    with pytest.raises(InvalidHistory, match="structural bounds"):
        contracts._check_tree(value)


def test_node_reservation_accepts_exact_budget_and_rejects_next_node(monkeypatch):
    monkeypatch.setattr(contracts, "MAX_NODES", 12)
    contracts._check_tree([None] * 11)
    contracts._check_tree([None] * 6 + [[None] * 4])
    contracts._check_tree({"nested": [None] * 9})
    with pytest.raises(InvalidHistory, match="structural bounds"):
        contracts._check_tree([None] * 12)


@pytest.mark.parametrize("payload", [
    [10**50, 10**50], [False] * 13, [None] * 14, [1e100] * 10, "\x00" * 12, "中" * 21,
])
def test_oversized_primitives_are_rejected_before_whole_json_serialization(monkeypatch, payload):
    monkeypatch.setattr(contracts, "MAX_RECORD_BYTES", 64)
    def forbidden_dump(*args, **kwargs):
        pytest.fail("Oversized data reached JSON serialization")
    monkeypatch.setattr(contracts.json, "dumps", forbidden_dump)
    with pytest.raises(InvalidHistory, match="byte limit"):
        contracts._json(payload)


@pytest.mark.parametrize("kind", ["references", "calls"])
def test_oversized_typed_collections_are_rejected_before_iteration(kind):
    class UniterableTuple(tuple):
        def __iter__(self):
            pytest.fail("Oversized typed collection was transformed before checking its count")
    if kind == "references":
        payload = InitialInputPayload(InputContent("", UniterableTuple((InputReference("file"),) * 65)))
    else:
        model = model_payload()
        payload = replace(model, result=replace(model.result,
            calls=UniterableTuple((ModelToolCall("call", "read", '{}'),) * 129)))
    with pytest.raises(InvalidHistory, match="count"):
        encode_history(payload)


def test_model_call_count_at_existing_limit_is_accepted():
    model = model_payload()
    payload = replace(model, result=replace(model.result,
        calls=tuple(ModelToolCall(str(index), "read", '{}') for index in range(128))))
    record = encode_history(payload)
    assert decode_history(record.kind, record.version, record.payload) == payload


def context_base():
    return ContextBasePayload((
        ModelMessage("system", (ModelContent("text", "固定规则"),), cache_boundary=True),
        ModelMessage("user", (ModelContent("text", "说明"), ModelContent("image", "attachment:image"))),
        ModelMessage("assistant", (ModelContent("text", "摘要 ✓"),),
            (ModelToolCall("call", "read_file", '{ "path": "文件" }'),),
            interaction_id="interaction", requires_continuation=True),
        ModelMessage("tool", (ModelContent("text", "清理后的结果"),), call_id="call", is_error=True),
    ), 17, 23)


@pytest.mark.parametrize("value", [
    context_base(), ContextBasePayload((), 0, 0),
    ModelInputPayload("step", None, 0, (), None),
    ModelInputPayload("step", 18, 23, ("read_file", "search_tools"), "2026-09-07T12:34+08:00"),
    ModelInputPayload("step", 18, 23, ("read_file",), "2026-09-07T04:34Z"),
])
def test_context_base_and_model_input_exact_storage_roundtrip(value):
    record = encode_history(value)
    restored = decode_history(record.kind, record.version, json.loads(json.dumps(record.payload)))
    assert restored == value
    assert record.version == 1


def test_context_base_detaches_nested_messages_and_retains_exact_json():
    value = context_base()
    encoded = encode_history(value)
    assert encoded.payload["messages"][2]["calls"][0]["arguments_json"] == '{ "path": "文件" }'
    encoded.payload["messages"][1]["content"][0]["value"] = "changed"
    assert value.messages[1].content[0].value == "说明"
    assert encode_history(value).payload["messages"][1]["content"][0]["value"] == "说明"


@pytest.mark.parametrize("mutation", ["role", "content_kind", "extra", "boolean", "arguments", "duplicate", "coverage"])
def test_context_base_rejects_malformed_message_fields(mutation):
    record = encode_history(context_base())
    message = record.payload["messages"][2]
    if mutation == "role":
        message["role"] = "developer"
    elif mutation == "content_kind":
        message["content"][0]["kind"] = "unknown"
    elif mutation == "extra":
        message["credential"] = "private-value"
    elif mutation == "boolean":
        message["requires_continuation"] = 1
    elif mutation == "arguments":
        message["calls"][0]["arguments_json"] = '{"value":NaN}'
    elif mutation == "duplicate":
        message["calls"].append(dict(message["calls"][0]))
    else:
        record.payload["coverage_sequence"] = -1
    with pytest.raises(InvalidHistory) as error:
        decode_history(record.kind, record.version, record.payload)
    assert "private-value" not in str(error.value)


@pytest.mark.parametrize("minute", [
    "2026-09-07T12:34:00+08:00", "2026-09-07T12:34:01Z", "2026-09-07T12:34",
    "2026-09-07T12:34.1Z", "2026-02-30T12:34Z", "2026-09-07T24:00Z",
    "2026-09-07T12:34+01:60", "2026-09-07T12:34+24:00", "private-value", "",
])
def test_model_input_time_requires_valid_minute_and_timezone(minute):
    with pytest.raises(InvalidHistory):
        encode_history(ModelInputPayload("step", None, 0, (), minute))


@pytest.mark.parametrize("changes", [
    {"step_id": ""}, {"base_sequence": 0}, {"base_sequence": True}, {"base_sequence": 2**63},
    {"read_through_sequence": -1}, {"read_through_sequence": True},
    {"visible_tool_names": ("tool", "tool")}, {"visible_tool_names": ("x" * 65,)},
    {"visible_tool_names": ("",)},
])
def test_model_input_validates_closed_request_references(changes):
    with pytest.raises(InvalidHistory):
        encode_history(replace(ModelInputPayload("step", None, 0, (), None), **changes))


@pytest.mark.parametrize("value", [context_base(), ModelInputPayload("step", None, 0, (), None)])
def test_new_history_records_reject_unknown_versions_and_extra_fields(value):
    record = encode_history(value)
    with pytest.raises(InvalidHistory):
        decode_history(record.kind, 2, record.payload)
    record.payload["invented"] = "private-value"
    with pytest.raises(InvalidHistory):
        decode_history(record.kind, 1, record.payload)


@pytest.mark.parametrize("kind", ["messages", "content", "calls", "tools"])
def test_context_request_collection_bounds_precede_transformation(kind):
    class UniterableTuple(tuple):
        def __iter__(self):
            pytest.fail("oversized collection must not be transformed")
    if kind == "messages":
        value = ContextBasePayload(UniterableTuple((ModelMessage("user"),) * 2049), 0, 0)
    elif kind == "content":
        value = ContextBasePayload((ModelMessage("user", UniterableTuple((ModelContent("text", ""),) * 20000)),), 0, 0)
    elif kind == "calls":
        value = ContextBasePayload((ModelMessage("assistant", calls=UniterableTuple((ModelToolCall("c", "t", '{}'),) * 129)),), 0, 0)
    else:
        value = ModelInputPayload("step", None, 0, UniterableTuple(("tool",) * 129), None)
    with pytest.raises(InvalidHistory):
        encode_history(value)


def test_context_base_whole_record_byte_bound_and_visible_tool_limit():
    empty = encode_history(ContextBasePayload((ModelMessage("user", (ModelContent("text", ""),)),), 0, 0))
    overhead = len(json.dumps({"kind": empty.kind, "version": 1, "payload": empty.payload},
        ensure_ascii=False, separators=(",", ":")).encode())
    for size in (MAX_RECORD_BYTES - overhead, MAX_RECORD_BYTES - overhead + 1):
        value = ContextBasePayload((ModelMessage("user", (ModelContent("text", "x" * size),)),), 0, 0)
        if size + overhead > MAX_RECORD_BYTES:
            with pytest.raises(InvalidHistory):
                encode_history(value)
        else:
            assert encode_history(value).kind == "context_base"
    names = tuple(f"tool_{index}" for index in range(128))
    value = ModelInputPayload("step", 1, 2, names, "2026-09-07T01:02-05:30")
    record = encode_history(value)
    assert decode_history(record.kind, 1, record.payload) == value


@pytest.mark.parametrize("coverage,through", [(0, 0), (0, 10), (4, 4), (4, 10)])
def test_context_base_distinguishes_summary_coverage_from_full_source_boundary(coverage, through):
    value = replace(context_base(), coverage_sequence=coverage, through_sequence=through)
    record = encode_history(value)
    assert decode_history(record.kind, 1, record.payload) == value


@pytest.mark.parametrize("through", [-1, True, 1.5, 2**63, 16])
def test_context_base_rejects_invalid_or_earlier_full_source_boundary(through):
    with pytest.raises(InvalidHistory):
        encode_history(replace(context_base(), through_sequence=through))
