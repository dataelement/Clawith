import base64
import json
from dataclasses import replace
from uuid import uuid4

import pytest

from app.infrastructure.errors import InvalidInput
from app.modules.tool.public import DefinitionSpec, ToolDefinition, ToolOutputPart, ToolResult, tool_result_content


def definition(source="mcp"):
    return ToolDefinition(uuid4(), uuid4(), DefinitionSpec("read", "Read", '{"type":"object"}',
        "mcp.v1" if source == "mcp" else "read.v1", source, uuid4() if source == "mcp" else None,
        "upstream" if source == "mcp" else None))


def result(content, status="success"):
    return ToolResult("call", status, json.dumps(content, ensure_ascii=False))


def test_explicit_captured_format_enables_images_without_tool_name_inference():
    ordinary = definition("builtin")
    body = result({"content":[{"type":"image","mimeType":"image/png","data":"aW1hZ2U="}]})
    assert tool_result_content(ordinary, body) == (ToolOutputPart("text", body.content_json),)
    declared = replace(ordinary, spec=replace(ordinary.spec, result_format="content_blocks"))
    assert tool_result_content(declared, body) == (ToolOutputPart("image", "data:image/png;base64,aW1hZ2U="),)
    with pytest.raises(InvalidInput):
        replace(ordinary.spec, result_format="unknown")


def test_mcp_text_and_images_keep_order_without_repeating_image_base64_in_text():
    first, second = base64.b64encode(b"first image").decode(), base64.b64encode(b"second image").decode()
    original = result({"content": [{"type":"text", "text":"Before ✓"},
        {"type":"image", "mimeType":"image/png", "data":first},
        {"type":"text", "text":"Between"}, {"type":"image", "mimeType":"image/jpeg", "data":second}]})
    saved = original.content_json
    parts = tool_result_content(definition(), original)
    assert parts == (ToolOutputPart("text", "Before ✓"), ToolOutputPart("image", f"data:image/png;base64,{first}"),
        ToolOutputPart("text", "Between"), ToolOutputPart("image", f"data:image/jpeg;base64,{second}"))
    assert all(first not in part.value and second not in part.value for part in parts if part.kind == "text")
    assert original.content_json == saved


def test_block_and_result_metadata_and_unsupported_content_are_not_dropped():
    data = base64.b64encode(b"image").decode()
    audio = {"type":"audio", "data":"YXVkaW8=", "mimeType":"audio/wav"}
    resource = {"type":"resource", "resource":{"uri":"asset:one", "text":"document"}}
    link = {"type":"resource_link", "uri":"https://example.test/file", "name":"Reference"}
    extension = {"type":"future_content", "value":{"opaque":True}}
    parts = tool_result_content(definition(), result({"content": [
        {"type":"text", "text":"Title", "annotations":{"priority":1}},
        {"type":"image", "data":data, "mimeType":"image/png", "_meta":{"caption":"Chart"}},
        audio, resource, link, extension], "structuredContent":{"answer":42}, "_meta":{"trace":"reference"}}))
    assert parts[0] == ToolOutputPart("text", "Title")
    assert json.loads(parts[1].value) == {"type":"text", "metadata":{"annotations":{"priority":1}}}
    assert parts[2].kind == "image"
    assert json.loads(parts[3].value) == {"type":"image", "metadata":{"_meta":{"caption":"Chart"}}}
    assert [json.loads(part.value) for part in parts[4:8]] == [audio, resource, link, extension]
    assert json.loads(parts[8].value) == {"type":"mcp_metadata", "metadata":{"structuredContent":{"answer":42}, "_meta":{"trace":"reference"}}}
    assert all(data not in part.value for part in parts if part.kind == "text")


@pytest.mark.parametrize("source", ["builtin", "product", "external"])
def test_non_mcp_json_is_not_guessed_to_be_media(source):
    raw = result({"content":[{"type":"image", "data":"not base64", "mimeType":"image/png"}]})
    assert tool_result_content(definition(source), raw) == (ToolOutputPart("text", raw.content_json),)


@pytest.mark.parametrize("status", ["error", "uncertain"])
def test_normalized_mcp_transport_diagnostics_remain_available(status):
    raw = result({"message":"Check the selected account", "details":{"code":"unavailable"}}, status)
    assert tool_result_content(definition(), raw) == (ToolOutputPart("text", raw.content_json),)


@pytest.mark.parametrize("content", [
    {}, {"content":None}, {"content":["not a block"]}, {"content":[{}]}, {"content":[], "structuredContent":42},
    {"content":[{"type":"text", "text":42}]},
    {"content":[{"type":"image", "data":"YQ==", "mimeType":"text/html"}]},
    {"content":[{"type":"image", "data":"private-invalid-base64", "mimeType":"image/png"}]},
    {"content":[{"type":"image", "data":"", "mimeType":"image/png"}]},
    {"content":[{"type":"image", "data":"YQ==", "mimeType":"image/png\r\nInjected:x"}]},
])
def test_malformed_mcp_source_is_an_explicit_safe_error(content):
    with pytest.raises(InvalidInput) as error:
        tool_result_content(definition(), result(content))
    assert "private-invalid-base64" not in str(error.value)


def test_content_and_complete_result_bounds():
    item = {"type":"text", "text":"x", "annotations":{"priority":1}}
    parts = tool_result_content(definition(), result({"content":[item]*128, "structuredContent":{"ok":True}}))
    assert len(parts) == 257
    with pytest.raises(InvalidInput):
        tool_result_content(definition(), result({"content":[item]*129}))
    raw = result({"content":[]})
    object.__setattr__(raw, "content_json", json.dumps({"content":[{"type":"text", "text":"中"*100000}]}, ensure_ascii=False))
    with pytest.raises(InvalidInput):
        tool_result_content(definition(), raw)


def test_empty_result_and_empty_text_are_explicit_and_mime_case_is_normalized():
    assert json.loads(tool_result_content(definition(), result({"content":[]}))[0].value) == {"type":"mcp_content", "content":[]}
    assert tool_result_content(definition(), result({"content":[{"type":"text", "text":""}]})) == (ToolOutputPart("text", ""),)
    assert tool_result_content(definition(), result({"content":[{"type":"image", "mimeType":"IMAGE/PNG", "data":"YQ=="}]}))[0].value == "data:image/png;base64,YQ=="
