"""Bounded document extraction through an authorized reader and isolated workers."""

import asyncio
import hashlib
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path

import psutil

from app.execution_dependencies.attachment_tools import AttachmentBlob, AttachmentBlobReader
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

MAX_BYTES = 4 * 1024 * 1024
MAX_OUTPUT = 256 * 1024
WORKER = Path(__file__).with_name("document_worker.py")

READ_DOCUMENT_DEFINITION = DefinitionSpec("read_document",
    "Extract embedded text and tables from an authorized PDF, DOCX, XLSX, PPTX or UTF-8 text file. Reference an attachment, files/ in the current Workspace, or temporary:filename in your current A2A work. Follow next_offset for more extracted text; truncated means an extraction limit was reached. No OCR, audio transcription, or automatic Workspace import.",
    '{"type":"object","properties":{"reference":{"type":"string","minLength":1,"maxLength":512},'
    '"content_offset":{"type":"integer","minimum":0,"maximum":262144}},"required":["reference"],"additionalProperties":false}',
    "read_document.v1", "builtin")


class DocumentFailure(InvalidInput):
    def __init__(self, code: str) -> None:
        messages = {
            "parser_busy":"Document parsers are busy; retry later.",
            "parser_closed":"Document parsing is unavailable while the application closes.",
            "parser_timeout":"Document extraction exceeded its time budget; use a smaller document.",
            "resource_limit":"Document extraction exceeded its memory budget; use a smaller document.",
            "archive_limit":"The Office archive exceeds its expanded-size or member-count limits.",
            "input_limit":"The document exceeds the four-MiB input or metadata limit.",
            "output_limit":"The document parser exceeded its output limit.",
            "unsupported_format":"Use PDF, DOCX, XLSX, PPTX or UTF-8 text; OCR and audio are not supported.",
            "invalid_offset":"The requested offset is outside the extracted text.",
            "invalid_document":"The file cannot be parsed as its declared document format.",
            "invalid_request":"The document extraction request is invalid.",
            "invalid_worker_response":"The document parser returned an invalid response.",
            "worker_failed":"The document parser stopped without a valid result.",
            "resource_monitor_unavailable":"The document parser resource monitor is unavailable.",
        }
        super().__init__(messages[code])
        self.code = code


def _format(blob: AttachmentBlob) -> str:
    extension = Path(blob.name).suffix.lower().lstrip(".")
    if extension in {"pdf","docx","xlsx","pptx"}:
        return extension
    types = {"application/pdf":"pdf",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document":"docx",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet":"xlsx",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation":"pptx"}
    if blob.media_type in types:
        return types[blob.media_type]
    if blob.media_type.startswith("text/") or blob.media_type in {"application/json","application/xml"} or Path(blob.name).name == ".env" or extension in {
            "txt","md","csv","json","xml","yaml","yml","js","ts","py","html","css","sh","log","env","sql","ini","cfg","conf","toml"}:
        return "text"
    raise DocumentFailure("unsupported_format")


async def _stop(process: asyncio.subprocess.Process) -> None:
    if process.stdin is not None:
        process.stdin.close()
    if process.returncode is None:
        try:
            process.kill()
        except ProcessLookupError:
            pass
    async def discard_output() -> None:
        if process.stdout is not None:
            while await process.stdout.read(65536):
                pass
    async def close_input() -> None:
        if process.stdin is not None:
            try:
                await process.stdin.wait_closed()
            except (BrokenPipeError, ConnectionResetError):
                pass
    async def reap() -> None:
        await asyncio.gather(process.wait(), discard_output(), close_input())
    waiting = asyncio.create_task(reap())
    interrupted = False
    while not waiting.done():
        try:
            await asyncio.shield(waiting)
        except asyncio.CancelledError:
            interrupted = True
    waiting.result()
    if interrupted:
        raise asyncio.CancelledError


@dataclass
class _Worker:
    process: asyncio.subprocess.Process
    monitor: asyncio.Task[None]
    exchange: asyncio.Task[bytes]
    cleanup: asyncio.Task[None] | None = None


class DocumentParser:
    """Application-owned parser admission; close after its calling Runtime is drained."""

    def __init__(self, *, max_parallel: int = 2, max_waiting: int = 32,
            timeout_seconds: float = 15, wait_timeout_seconds: float = 10) -> None:
        if not 1 <= max_parallel <= 2 or not 0 <= max_waiting <= 32 or not 0 < timeout_seconds <= 60 or not 0 < wait_timeout_seconds <= 60:
            raise InvalidInput("Document parser bounds are invalid")
        self._slots = asyncio.Semaphore(max_parallel)
        self._capacity, self._admitted = max_parallel + max_waiting, 0
        self._timeout, self._wait_timeout = timeout_seconds, wait_timeout_seconds
        self._workers: dict[int, _Worker] = {}
        self._closed = False

    @property
    def active_pids(self) -> tuple[int, ...]:
        return tuple(self._workers)

    async def extract(self, reader: AttachmentBlobReader, scope: CallScope, *, reference: str, offset: int) -> dict:
        if self._closed or self._admitted >= self._capacity:
            raise DocumentFailure("parser_busy")
        self._admitted += 1
        acquired = False
        try:
            blob = await reader(scope, reference=reference)
            if len(blob.content) > MAX_BYTES or len(blob.name.encode()) > 512 or len(blob.media_type.encode()) > 256:
                raise DocumentFailure("input_limit")
            kind = _format(blob)
            try:
                async with asyncio.timeout(self._wait_timeout):
                    await self._slots.acquire()
                    acquired = True
            except TimeoutError:
                raise DocumentFailure("parser_busy") from None
            if self._closed:
                raise DocumentFailure("parser_closed")
            result = await self._run(blob.content, kind, offset)
            result.update({"format":kind,"reference":reference,"name":blob.name,
                "source_sha256":hashlib.sha256(blob.content).hexdigest(),"source_bytes":len(blob.content)})
            return result
        finally:
            if acquired:
                self._slots.release()
            self._admitted -= 1

    async def _run(self, content: bytes, kind: str, offset: int) -> dict:
        spawn = asyncio.create_task(asyncio.create_subprocess_exec(sys.executable, "-I", str(WORKER),
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
            env={"PATH":os.defpath,"LANG":"C.UTF-8","OPENBLAS_NUM_THREADS":"1","OMP_NUM_THREADS":"1"},
            limit=MAX_OUTPUT))
        try:
            process = await asyncio.shield(spawn)
        except asyncio.CancelledError:
            while not spawn.done():
                try:
                    await asyncio.wait((spawn,))
                except asyncio.CancelledError:
                    continue
            if not spawn.cancelled() and spawn.exception() is None:
                await _stop(spawn.result())
            raise
        except OSError:
            raise DocumentFailure("worker_failed") from None
        if self._closed:
            await _stop(process)
            raise DocumentFailure("parser_closed")
        monitor = asyncio.create_task(self._monitor(process))
        exchange = asyncio.create_task(self._exchange(process, content, kind, offset))
        worker = _Worker(process, monitor, exchange)
        self._workers[process.pid] = worker
        try:
            async with asyncio.timeout(self._timeout):
                done, _ = await asyncio.wait((exchange, monitor), return_when=asyncio.FIRST_COMPLETED)
                if monitor in done:
                    await monitor
                raw = await exchange
                await process.wait()
            if process.returncode != 0:
                raise DocumentFailure("worker_failed")
            return _decode(raw, offset)
        except asyncio.CancelledError:
            current = asyncio.current_task()
            if self._closed and current is not None and not current.cancelling():
                raise DocumentFailure("parser_closed") from None
            raise
        except TimeoutError:
            raise DocumentFailure("parser_timeout") from None
        finally:
            await self._cleanup(worker)

    async def _cleanup(self, worker: _Worker) -> None:
        if worker.cleanup is None:
            worker.cleanup = asyncio.create_task(self._release(worker))
        interrupted = False
        while not worker.cleanup.done():
            try:
                await asyncio.shield(worker.cleanup)
            except asyncio.CancelledError:
                interrupted = True
        worker.cleanup.result()
        if interrupted:
            raise asyncio.CancelledError

    async def _release(self, worker: _Worker) -> None:
        worker.monitor.cancel()
        worker.exchange.cancel()
        await asyncio.gather(worker.monitor, worker.exchange, return_exceptions=True)
        await _stop(worker.process)
        self._workers.pop(worker.process.pid, None)

    async def _monitor(self, process: asyncio.subprocess.Process) -> None:
        try:
            watched = psutil.Process(process.pid)
        except psutil.NoSuchProcess:
            return
        except psutil.AccessDenied:
            raise DocumentFailure("resource_monitor_unavailable") from None
        while process.returncode is None:
            try:
                if watched.memory_info().rss > 512 * 1024 * 1024:
                    raise DocumentFailure("resource_limit")
            except psutil.NoSuchProcess:
                return
            except psutil.AccessDenied:
                raise DocumentFailure("resource_monitor_unavailable") from None
            await asyncio.sleep(0.05)

    async def _exchange(self, process: asyncio.subprocess.Process, content: bytes, kind: str, offset: int) -> bytes:
        assert process.stdin is not None and process.stdout is not None
        async def write() -> None:
            assert process.stdin is not None
            try:
                process.stdin.write(json.dumps({"version":1,"format":kind,"content_offset":offset}).encode() + b"\n" + content)
                await process.stdin.drain()
            except (BrokenPipeError, ConnectionResetError):
                pass
            finally:
                process.stdin.close()
        writer = asyncio.create_task(write())
        try:
            output = bytearray()
            while block := await process.stdout.read(16384):
                if len(output) + len(block) > MAX_OUTPUT:
                    raise DocumentFailure("output_limit")
                output.extend(block)
            await writer
            return bytes(output)
        finally:
            writer.cancel()
            await asyncio.gather(writer, return_exceptions=True)

    async def close(self) -> None:
        self._closed = True
        interrupted = False
        for worker in tuple(self._workers.values()):
            try:
                await self._cleanup(worker)
            except asyncio.CancelledError:
                interrupted = True
        if interrupted:
            raise asyncio.CancelledError


def _decode(raw: bytes, offset: int) -> dict:
    try:
        result = json.loads(raw)
        if not isinstance(result, dict) or type(result.get("version")) is not int or result["version"] != 1:
            raise ValueError()
        if result.get("status") == "error":
            if set(result) != {"version","status","code"} or result["code"] not in {
                    "archive_limit","input_limit","invalid_request","invalid_offset","unsupported_format","resource_limit","invalid_document"}:
                raise ValueError()
            raise DocumentFailure(result["code"])
        if set(result) != {"version","status","text","content_offset","next_offset","offset_unit",
                "extracted_codepoints","truncated","reason","units_processed","ocr"}:
            raise ValueError()
        if (result["status"] != "success" or not isinstance(result["text"], str) or len(result["text"]) > 16000
                or type(result["content_offset"]) is not int or result["content_offset"] != offset or result["offset_unit"] != "unicode_codepoints"
                or type(result["extracted_codepoints"]) is not int or not 0 <= result["extracted_codepoints"] <= 262144
                or type(result["truncated"]) is not bool or result["ocr"] is not False
                or result["reason"] not in (None,"text_limit","structure_limit","page_limit")
                or result["truncated"] != (result["reason"] is not None)
                or type(result["units_processed"]) is not int or not 0 <= result["units_processed"] <= 100):
            raise ValueError()
        next_offset = result["next_offset"]
        end = offset + len(result["text"])
        if end > result["extracted_codepoints"] or next_offset != (end if end < result["extracted_codepoints"] else None):
            raise ValueError()
        if next_offset is not None and type(next_offset) is not int:
            raise ValueError()
        return result
    except (ValueError, TypeError, UnicodeError):
        raise DocumentFailure("invalid_worker_response") from None


class DocumentExecutor:
    def __init__(self, reader: AttachmentBlobReader, parser: DocumentParser) -> None:
        self._reader, self._parser = reader, parser

    async def execute(self, tool: ResolvedTool, call: ToolCall, scope: CallScope) -> ToolResult:
        if tool.definition.tenant_id != scope.tenant_id or tool.definition.spec != READ_DOCUMENT_DEFINITION or call.name != "read_document":
            raise InvalidInput("Document extraction does not match its captured Tool binding")
        try:
            arguments = json_object(call.arguments_json)
            reference, offset = arguments.get("reference"), arguments.get("content_offset",0)
            if set(arguments) - {"reference","content_offset"} or not isinstance(reference,str) or not reference or len(reference.encode()) > 512 or type(offset) is not int or not 0 <= offset <= 262144:
                raise InvalidInput("Document extraction requires a reference and a valid content_offset")
            result = await self._parser.extract(self._reader, scope, reference=reference, offset=offset)
            return ToolResult(call.id,"success",canonical_json(result, maximum=262144))
        except DomainError as exc:
            return ToolResult(call.id,"error",canonical_json({"code":exc.code,"message":str(exc)[:1024]}))


def document_tool_binding(reader: AttachmentBlobReader, *, parser: DocumentParser) -> ExecutorBinding:
    return ExecutorBinding(READ_DOCUMENT_DEFINITION.executor_key, DocumentExecutor(reader, parser),
        safe_parallel=True, builtin=READ_DOCUMENT_DEFINITION)
