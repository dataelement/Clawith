import json
from dataclasses import replace

import pytest

from app.modules.model.public import ModelStepResult, ModelToolCall, ModelUsage
from app.modules.run import contracts
from app.modules.run.contracts import (
    MAX_INPUT_BYTES,
    MAX_RECORD_BYTES,
    InitialInputPayload,
    InputContent,
    InputReference,
    InvalidHistory,
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
