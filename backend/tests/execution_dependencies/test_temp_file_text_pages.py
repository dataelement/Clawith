"""The actual temporary Tool serializes bounded, lossless Unicode pages."""

import json
from hashlib import sha256
from uuid import uuid4

import pytest
from modules.run.test_snapshot import snapshot

from app.execution_dependencies.temp_file_tools import TEMP_FILE_DEFINITION, TempFileExecutor
from app.modules.a2a.public import TempFileView
from app.modules.tool.public import CallScope, ResolvedTool, ToolCall, ToolDefinition


@pytest.mark.parametrize("text", ["中文页面" * 10000, "🙂🦀🚀" * 14000, "\x00\x01\n\r\t\"\\" * 10000])
async def test_complete_json_budget_pages_reconstruct_multibyte_and_escaped_text(text):
    snap = snapshot()
    scope = CallScope(snap.tenant_id, snap.agent_id, snap.workspace.run_id)
    metadata = TempFileView(name="带转义\"文件.txt", media_type="text/plain", byte_size=len(text.encode()),
        sha256=sha256(text.encode()).hexdigest(), revision='"\\\x01' * 160)
    reads = []
    class Files:
        async def read(self, actual_scope, *, name, request_id):
            assert actual_scope == scope and name == metadata.name and request_id is None
            reads.append(name)
            return text.encode(), metadata
    tool = ResolvedTool(ToolDefinition(uuid4(), snap.tenant_id, TEMP_FILE_DEFINITION), None)
    executor = TempFileExecutor(snap, "step", Files())
    offset, fragments = 0, []
    while offset is not None:
        previous = offset
        result = await executor.execute(tool, ToolCall(str(len(reads)), "a2a_file", json.dumps({
            "action": "read", "name": metadata.name, "offset": offset})), scope)
        assert result.status == "success", result.content_json
        assert len(result.content_json.encode()) <= 65536
        body = json.loads(result.content_json)
        assert body["file"] == metadata.model_dump(mode="json")
        assert body["offset"] == previous and body["offset_unit"] == "unicode_codepoints"
        fragments.append(body["text"])
        offset = body["next_offset"]
        assert offset is None or offset == previous + len(body["text"]) > previous
    assert len(reads) > 1 and "".join(fragments) == text
    empty = await executor.execute(tool, ToolCall("eof", "a2a_file", json.dumps({
        "action": "read", "name": metadata.name, "offset": len(text)})), scope)
    assert empty.status == "success"
    assert json.loads(empty.content_json)["text"] == "" and json.loads(empty.content_json)["next_offset"] is None
    invalid = await executor.execute(tool, ToolCall("past-eof", "a2a_file", json.dumps({
        "action": "read", "name": metadata.name, "offset": len(text) + 1})), scope)
    assert invalid.status == "error" and "beyond" in invalid.content_json
