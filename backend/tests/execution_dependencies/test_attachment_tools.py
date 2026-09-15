import asyncio
import base64
import hashlib
import io
import json
import struct
import threading
import zlib
from uuid import uuid4

import pytest
from PIL import Image

from app.execution_dependencies import attachment_tools as module
from app.execution_dependencies.attachment_tools import (
    READ_ATTACHMENT_DEFINITION,
    SAVE_ATTACHMENT_DEFINITION,
    AttachmentBlob,
    AttachmentPreviewExecutor,
    AttachmentSaveExecutor,
    attachment_save_binding,
)
from app.infrastructure.errors import AccessDenied, InvalidInput
from app.modules.tool.public import CallScope, ResolvedTool, ToolCall, ToolDefinition, tool_result_content
from app.modules.workspace.public import FileConflict, FileMutationUncertain


def fixture(blob):
    scope = CallScope(uuid4(), uuid4(), uuid4())
    tool = ResolvedTool(ToolDefinition(uuid4(), scope.tenant_id, READ_ATTACHMENT_DEFINITION), None)
    async def reader(actual_scope, *, reference):
        assert actual_scope == scope and reference == "attachment:owned"
        return blob
    return scope, tool, reader


async def test_unicode_text_pages_reconstruct_whole_attachment():
    text = "中🙂\\\n" * 12000
    scope, tool, reader = fixture(AttachmentBlob("notes.txt", "text/plain", text.encode()))
    executor = AttachmentPreviewExecutor(reader, cpu_slots=asyncio.Semaphore(2))
    offset, fragments = 0, []
    while offset is not None:
        result = await executor.execute(tool, ToolCall("call", "read_attachment", json.dumps({
            "reference":"attachment:owned","content_offset":offset})), scope)
        assert result.status == "success"
        body = json.loads(result.content_json)
        assert body["attachment"]["offset_unit"] == "unicode_codepoints"
        fragments.append(body["content"][0]["text"])
        offset = body["attachment"]["next_offset"]
    assert "".join(fragments) == text


async def test_image_preview_is_bounded_traceable_and_enters_explicit_model_image_content():
    output = io.BytesIO()
    with Image.new("RGB", (1600, 1200), "red") as image:
        image.save(output, format="PNG")
    original = output.getvalue()
    scope, tool, reader = fixture(AttachmentBlob("photo.png", "image/png", original))
    result = await AttachmentPreviewExecutor(reader, cpu_slots=asyncio.Semaphore(2)).execute(tool,
        ToolCall("call", "read_attachment", '{"reference":"attachment:owned"}'), scope)
    assert result.status == "success" and len(result.content_json.encode()) < 262144
    body = json.loads(result.content_json)
    metadata = body["attachment"]
    assert metadata["preview"] and metadata["source_sha256"] == hashlib.sha256(original).hexdigest()
    assert (metadata["source_width"],metadata["source_height"]) == (1600,1200)
    image_part = next(part for part in tool_result_content(tool.definition, result) if part.kind == "image")
    with Image.open(io.BytesIO(base64.b64decode(image_part.value.split(",",1)[1]))) as preview:
        assert preview.width <= 1024 and preview.height <= 1024
    assert output.getvalue() == original


async def test_pixel_header_limit_is_checked_before_full_decode_and_transparency_is_preserved():
    buffer = io.BytesIO()
    with Image.new("RGBA", (1,1), (255,0,0,0)) as image:
        image.save(buffer, format="PNG")
    raw = buffer.getvalue()
    dimensions = struct.pack(">II", 5000, 4000)
    ihdr = dimensions + raw[24:29]
    oversized = raw[:16] + ihdr + struct.pack(">I", zlib.crc32(b"IHDR" + ihdr)) + raw[33:]
    scope, tool, reader = fixture(AttachmentBlob("huge.png", "image/png", oversized))
    result = await AttachmentPreviewExecutor(reader, cpu_slots=asyncio.Semaphore(2)).execute(tool,
        ToolCall("call", "read_attachment", '{"reference":"attachment:owned"}'), scope)
    assert result.status == "error" and "pixel bound" in result.content_json
    scope, tool, reader = fixture(AttachmentBlob("clear.png", "image/png", raw))
    result = await AttachmentPreviewExecutor(reader, cpu_slots=asyncio.Semaphore(2)).execute(tool,
        ToolCall("call", "read_attachment", '{"reference":"attachment:owned"}'), scope)
    part = next(part for part in tool_result_content(tool.definition, result) if part.kind == "image")
    with Image.open(io.BytesIO(base64.b64decode(part.value.split(",",1)[1]))) as preview:
        assert all(value >= 250 for value in preview.getpixel((0,0)))


@pytest.mark.parametrize("size,expected", [(4*1024*1024,"success"),(4*1024*1024+1,"error")])
async def test_original_file_bound_is_independent_of_small_return_page(size, expected):
    scope, tool, reader = fixture(AttachmentBlob("large.txt", "text/plain", b"x" * size))
    result = await AttachmentPreviewExecutor(reader, cpu_slots=asyncio.Semaphore(2)).execute(tool,
        ToolCall("call", "read_attachment", '{"reference":"attachment:owned"}'), scope)
    assert result.status == expected


@pytest.mark.parametrize("arguments", [
    {"reference":"attachment:owned","content_offset":-1},
    {"reference":"attachment:owned","content_offset":True},
    {"reference":"attachment:owned","other":True},
])
async def test_invalid_model_arguments_are_explicit_tool_errors(arguments):
    scope, tool, reader = fixture(AttachmentBlob("text", "text/plain", b"data"))
    result = await AttachmentPreviewExecutor(reader, cpu_slots=asyncio.Semaphore(2)).execute(tool,
        ToolCall("call", "read_attachment", json.dumps(arguments)), scope)
    assert result.status == "error"


async def test_owner_denial_and_wrong_tenant_do_not_read_blob():
    scope, tool, _ = fixture(AttachmentBlob("text", "text/plain", b"data"))
    calls = []
    async def denied(actual, *, reference):
        calls.append(reference)
        raise AccessDenied("Attachment is not authorized for this Run")
    executor = AttachmentPreviewExecutor(denied, cpu_slots=asyncio.Semaphore(2))
    call = ToolCall("call", "read_attachment", '{"reference":"attachment:owned"}')
    assert (await executor.execute(tool, call, scope)).status == "error"
    with pytest.raises(InvalidInput):
        await executor.execute(tool, call, CallScope(uuid4(), scope.agent_id, scope.run_id))
    assert calls == ["attachment:owned"]


async def test_cancelled_worker_keeps_shared_cpu_slot_until_actual_thread_finishes(monkeypatch):
    scope, tool, reader = fixture(AttachmentBlob("text", "text/plain", b"data"))
    entered, release = threading.Event(), threading.Event()
    lock = threading.Lock()
    active, peak, starts = 0, 0, 0
    preview = module._preview
    def slow(*args):
        nonlocal active, peak, starts
        with lock:
            active += 1
            starts += 1
            peak = max(peak, active)
            if starts == 2:
                entered.set()
        release.wait(3)
        try:
            return preview(*args)
        finally:
            with lock:
                active -= 1
    monkeypatch.setattr(module, "_preview", slow)
    executor = AttachmentPreviewExecutor(reader, cpu_slots=asyncio.Semaphore(2))
    tasks = [asyncio.create_task(executor.execute(tool, ToolCall(str(i), "read_attachment", '{"reference":"attachment:owned"}'), scope)) for i in range(3)]
    try:
        assert await asyncio.to_thread(entered.wait, 2)
        tasks[0].cancel()
        await asyncio.sleep(0)
        tasks[0].cancel()
        await asyncio.sleep(0)
        assert not tasks[0].done() and starts == 2
    finally:
        release.set()
        results = await asyncio.gather(*tasks, return_exceptions=True)
    assert isinstance(results[0], asyncio.CancelledError)
    assert peak == 2 and active == 0


async def test_save_delegates_original_binary_only_on_explicit_call_without_preview_copy():
    binary = b"\x00\xff\xfeoriginal-office-or-pdf-bytes"
    scope, read_tool, reader = fixture(AttachmentBlob("original.bin", "application/octet-stream", binary))
    saved = {}
    # The injected application port owns byte retrieval and Workspace mutation.
    async def saver(actual_scope, *, reference, path, expected_revision):
        assert actual_scope == scope and reference == "attachment:owned"
        assert path == "files/original.bin" and expected_revision is None
        blob = await reader(actual_scope, reference=reference)
        saved[path] = blob.content
        return "stored-revision"
    binding = attachment_save_binding(saver)
    assert binding.builtin == SAVE_ATTACHMENT_DEFINITION and not binding.safe_parallel
    assert saved == {}
    read_result = await AttachmentPreviewExecutor(reader, cpu_slots=asyncio.Semaphore(2)).execute(read_tool,
        ToolCall("read", "read_attachment", '{"reference":"attachment:owned"}'), scope)
    assert read_result.status == "error" and saved == {}
    tool = ResolvedTool(ToolDefinition(uuid4(), scope.tenant_id, SAVE_ATTACHMENT_DEFINITION), None)
    result = await binding.executor.execute(tool, ToolCall("save", "save_attachment", json.dumps({
        "reference":"attachment:owned","path":"files/original.bin","expected_revision":None})), scope)
    assert result.status == "success"
    assert saved == {"files/original.bin":binary}
    assert json.loads(result.content_json) == {"reference":"attachment:owned","path":"files/original.bin",
        "revision":"stored-revision","saved_original":True}


@pytest.mark.parametrize("error,status", [(AccessDenied("Attachment is not authorized"),"error"),
    (FileConflict("new-revision"),"error"),(FileMutationUncertain("files/file"),"uncertain")])
async def test_save_preserves_application_authorization_conflict_and_uncertain_outcomes(error, status):
    scope, _, _ = fixture(AttachmentBlob("file", "text/plain", b"data"))
    calls = []
    async def saver(actual, **arguments):
        calls.append((actual, arguments))
        raise error
    tool = ResolvedTool(ToolDefinition(uuid4(), scope.tenant_id, SAVE_ATTACHMENT_DEFINITION), None)
    result = await AttachmentSaveExecutor(saver).execute(tool, ToolCall("save", "save_attachment", json.dumps({
        "reference":"attachment:owned","path":"files/file","expected_revision":"old-revision"})), scope)
    assert result.status == status and len(calls) == 1
    assert calls[0][1]["expected_revision"] == "old-revision"
    if isinstance(error, FileConflict):
        assert json.loads(result.content_json)["current_revision"] == "new-revision"


@pytest.mark.parametrize("arguments", [
    {"reference":"attachment:owned","path":"files/file"},
    {"reference":"attachment:owned","path":"files/file","expected_revision":False},
    {"reference":"attachment:owned","path":"files/file","expected_revision":None,"content":"injected"},
])
async def test_save_requires_explicit_revision_and_never_accepts_replacement_content(arguments):
    scope, _, _ = fixture(AttachmentBlob("file", "text/plain", b"data"))
    async def saver(actual, **values):
        raise AssertionError("Invalid model arguments must not invoke the application saver")
    tool = ResolvedTool(ToolDefinition(uuid4(), scope.tenant_id, SAVE_ATTACHMENT_DEFINITION), None)
    result = await AttachmentSaveExecutor(saver).execute(tool, ToolCall("save", "save_attachment", json.dumps(arguments)), scope)
    assert result.status == "error"


async def test_utf8_pdf_representation_is_raw_bytes_decoding_not_document_extraction():
    raw = b"%PDF-1.7\n1 0 obj\n<< /Type /Catalog >>\nendobj"
    scope, tool, reader = fixture(AttachmentBlob("example.pdf", "application/pdf", raw))
    result = await AttachmentPreviewExecutor(reader, cpu_slots=asyncio.Semaphore(2)).execute(tool,
        ToolCall("read", "read_attachment", '{"reference":"attachment:owned"}'), scope)
    data = json.loads(result.content_json)
    assert data["content"][0]["text"] == raw.decode()
    assert data["attachment"]["representation"] == "raw_utf8"
    assert data["attachment"]["document_text_extracted"] is False
