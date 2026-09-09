"""Explicit attachment previews over an application-authorized immutable blob reader."""

import asyncio
import base64
import hashlib
import io
from dataclasses import dataclass, field
from typing import Protocol

from PIL import Image, ImageOps, UnidentifiedImageError

from app.infrastructure.errors import DomainError, InvalidInput
from app.modules.tool.public import (
    CallScope,
    DefinitionSpec,
    ExecutorBinding,
    ResolvedTool,
    ToolCall,
    ToolResult,
    canonical_json,
    json_object,
)
from app.modules.workspace.public import FileConflict, FileMutationUncertain

MAX_ATTACHMENT_BYTES = 4 * 1024 * 1024
MAX_IMAGE_PIXELS = 16_000_000
TEXT_PAGE_CHARACTERS = 32768


@dataclass(frozen=True, slots=True)
class AttachmentBlob:
    name: str
    media_type: str
    content: bytes = field(repr=False)


class AttachmentBlobReader(Protocol):
    async def __call__(self, scope: CallScope, *, reference: str) -> AttachmentBlob: ...


class AttachmentSaver(Protocol):
    async def __call__(self, scope: CallScope, *, reference: str, path: str,
        expected_revision: str | None) -> str: ...


READ_ATTACHMENT_DEFINITION = DefinitionSpec("read_attachment",
    "Read an authorized attachment. Non-image content is raw UTF-8 text, paginated by Unicode code points; this does not extract PDF or Office document text. Images return a labelled reduced preview, not the original file.",
    '{"type":"object","properties":{"reference":{"type":"string","minLength":1,"maxLength":512},'
    '"content_offset":{"type":"integer","minimum":0}},"required":["reference"],"additionalProperties":false}',
    "read_attachment.v1", "builtin", result_format="content_blocks")

SAVE_ATTACHMENT_DEFINITION = DefinitionSpec("save_attachment",
    "Explicitly save an authorized attachment's original bytes under files/ in this Run's current Workspace. Pass expected_revision null to create a new file, or its current revision to replace it. This does not save a reduced preview.",
    '{"type":"object","properties":{"reference":{"type":"string","minLength":1,"maxLength":512},'
    '"path":{"type":"string","minLength":1,"maxLength":512,"pattern":"^files/"},'
    '"expected_revision":{"type":["string","null"],"minLength":1,"maxLength":256}},'
    '"required":["reference","path","expected_revision"],"additionalProperties":false}',
    "save_attachment.v1", "builtin")


def _rgb_preview(source: Image.Image) -> Image.Image:
    with ImageOps.exif_transpose(source) as oriented:
        oriented.thumbnail((1024, 1024))
        with oriented.convert("RGBA") as rgba, rgba.getchannel("A") as alpha:
            preview = Image.new("RGB", rgba.size, "white")
            preview.paste(rgba, mask=alpha)
            return preview


def _preview(blob: AttachmentBlob, call_id: str, reference: str, offset: int) -> ToolResult:
    if len(blob.content) > MAX_ATTACHMENT_BYTES or len(blob.name.encode()) > 512 or len(blob.media_type.encode()) > 256:
        raise InvalidInput("Attachment exceeds its preview input bound")
    metadata: dict[str, object] = {"name":blob.name,"media_type":blob.media_type,"reference":reference,
        "source_sha256":hashlib.sha256(blob.content).hexdigest(),"source_bytes":len(blob.content)}
    if not blob.media_type.lower().startswith("image/"):
        try:
            text = blob.content.decode("utf-8")
        except UnicodeError:
            raise InvalidInput("This file is not UTF-8 text or a supported image; use a file-type-specific tool") from None
        if offset > len(text):
            raise InvalidInput("Attachment text offset is beyond the file")
        end = min(len(text), offset + TEXT_PAGE_CHARACTERS)
        metadata.update({"preview":False,"content_offset":offset,"offset_unit":"unicode_codepoints",
            "next_offset":end if end < len(text) else None,"total_codepoints":len(text),
            "representation":"raw_utf8","document_text_extracted":False})
        return ToolResult(call_id, "success", canonical_json({"content":[{"type":"text","text":text[offset:end]}],
            "attachment":metadata}, maximum=262144))
    if offset != 0:
        raise InvalidInput("Image previews require content_offset zero")
    try:
        with Image.open(io.BytesIO(blob.content)) as source:
            width, height = source.size
            if width * height > MAX_IMAGE_PIXELS or width <= 0 or height <= 0:
                raise InvalidInput("Image exceeds the preview pixel bound")
            with _rgb_preview(source) as preview:
                metadata.update({"preview":True,"source_width":width,"source_height":height,
                    "frame_index":0,"encoding":"jpeg","content_offset":0,"next_offset":None,
                    "offset_unit":"image_frame"})
                for edge in (1024, 768, 512, 384, 256):
                    preview.thumbnail((edge, edge))
                    for quality in (80, 60, 40, 20):
                        buffer = io.BytesIO()
                        preview.save(buffer, format="JPEG", quality=quality)
                        encoded = buffer.getvalue()
                        if len(encoded) > 180000:
                            continue
                        metadata.update({"preview_width":preview.width,"preview_height":preview.height,
                            "jpeg_quality":quality})
                        return ToolResult(call_id, "success", canonical_json({"content":[{
                            "type":"text","text":"Reduced first-frame image preview; the original attachment is unchanged."},
                            {"type":"image","mimeType":"image/jpeg","data":base64.b64encode(encoded).decode()}],
                            "attachment":metadata}, maximum=262144))
    except (UnidentifiedImageError, Image.DecompressionBombError, OSError, ValueError):
        raise InvalidInput("Attachment image cannot be previewed safely") from None
    raise InvalidInput("Image preview cannot fit the Tool Result bound")


class AttachmentPreviewExecutor:
    def __init__(self, reader: AttachmentBlobReader, *, cpu_slots: asyncio.Semaphore) -> None:
        self._reader, self._cpu_slots = reader, cpu_slots

    async def execute(self, tool: ResolvedTool, call: ToolCall, scope: CallScope) -> ToolResult:
        if tool.definition.tenant_id != scope.tenant_id or tool.definition.spec != READ_ATTACHMENT_DEFINITION or call.name != "read_attachment":
            raise InvalidInput("Attachment preview does not match its captured Tool binding")
        try:
            arguments = json_object(call.arguments_json)
            reference, offset = arguments.get("reference"), arguments.get("content_offset", 0)
            if set(arguments) - {"reference", "content_offset"} or not isinstance(reference, str) or not reference or len(reference.encode()) > 512 or type(offset) is not int or offset < 0:
                raise InvalidInput("Attachment preview requires a reference and nonnegative content_offset")
            blob = await self._reader(scope, reference=reference)
            async with self._cpu_slots:
                worker = asyncio.create_task(asyncio.to_thread(_preview, blob, call.id, reference, offset))
                try:
                    return await asyncio.shield(worker)
                except asyncio.CancelledError:
                    while not worker.done():
                        try:
                            await asyncio.wait((worker,))
                        except asyncio.CancelledError:
                            continue
                    if not worker.cancelled():
                        worker.exception()
                    raise
        except DomainError as exc:
            return ToolResult(call.id, "error", canonical_json({"message":str(exc)}))


def attachment_preview_binding(reader: AttachmentBlobReader, *, cpu_slots: asyncio.Semaphore) -> ExecutorBinding:
    return ExecutorBinding(READ_ATTACHMENT_DEFINITION.executor_key,
        AttachmentPreviewExecutor(reader, cpu_slots=cpu_slots), safe_parallel=True, builtin=READ_ATTACHMENT_DEFINITION)


class AttachmentSaveExecutor:
    def __init__(self, saver: AttachmentSaver) -> None:
        self._saver = saver

    async def execute(self, tool: ResolvedTool, call: ToolCall, scope: CallScope) -> ToolResult:
        if tool.definition.tenant_id != scope.tenant_id or tool.definition.spec != SAVE_ATTACHMENT_DEFINITION or call.name != "save_attachment":
            raise InvalidInput("Attachment save does not match its captured Tool binding")
        try:
            arguments = json_object(call.arguments_json)
            if set(arguments) != {"reference", "path", "expected_revision"}:
                raise InvalidInput("Attachment save requires reference, path and expected_revision")
            reference, path, expected = arguments["reference"], arguments["path"], arguments["expected_revision"]
            try:
                for value in (reference, path):
                    if not isinstance(value, str) or not value or len(value.encode()) > 512:
                        raise InvalidInput("Attachment reference and destination path must be bounded strings")
                if expected is not None and (not isinstance(expected, str) or not expected or len(expected.encode()) > 256):
                    raise InvalidInput("Expected revision must be a revision string or null")
            except UnicodeError:
                raise InvalidInput("Attachment save arguments must be valid UTF-8") from None
            assert isinstance(reference, str) and isinstance(path, str)
            revision = await self._saver(scope, reference=reference, path=path, expected_revision=expected)
            return ToolResult(call.id, "success", canonical_json({"reference":reference,"path":path,
                "revision":revision,"saved_original":True}))
        except FileConflict as exc:
            return ToolResult(call.id, "error", canonical_json({"code":exc.code,
                "message":"Destination file changed; inspect it before saving again.","current_revision":exc.current_revision}))
        except FileMutationUncertain:
            return ToolResult(call.id, "uncertain", canonical_json({"message":"The destination may have changed; inspect it before saving again."}))
        except DomainError as exc:
            return ToolResult(call.id, "error", canonical_json({"code":exc.code,"message":str(exc)[:1024]}))


def attachment_save_binding(saver: AttachmentSaver) -> ExecutorBinding:
    return ExecutorBinding(SAVE_ATTACHMENT_DEFINITION.executor_key, AttachmentSaveExecutor(saver),
        safe_parallel=False, builtin=SAVE_ATTACHMENT_DEFINITION)
