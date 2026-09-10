"""Explicit request-local temporary files and source-side returned-file saves."""

from hashlib import sha256
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.execution_dependencies.a2a_temp_files import A2ATempFiles
from app.infrastructure.errors import AccessDenied, DomainError, InvalidInput
from app.modules.run.public import RunSnapshot
from app.modules.tool.public import (
    CallScope,
    DefinitionSpec,
    ExecutorBinding,
    ResolvedTool,
    ToolCall,
    ToolResult,
    canonical_json,
)


class TempInput(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    action: Literal["write", "import", "read", "return", "save"]
    name: str = Field(min_length=1, max_length=200)
    text: str | None = Field(default=None, max_length=65536)
    reference: str | None = Field(default=None, max_length=512)
    source_name: str | None = Field(default=None, max_length=200)
    expected_revision: str | None = Field(default=None, max_length=512)
    request_id: UUID | None = None
    path: str | None = Field(default=None, max_length=512)
    offset: int = Field(default=0, ge=0, le=4194304)


TEMP_FILE_DEFINITION = DefinitionSpec("a2a_file",
    "Use A2A request-local temporary files, never the receiving Agent's shared Workspace. Target and children may write UTF-8 text, import an explicitly delegated attachment, read, and return an exact revision. Import with request_id and source_name copies a downstream returned file into this Main's own temporary name, preserving binary bytes. Return freezes the file. The current source Main may read returned files using request_id and save them to its current output files/ path with expected_revision (null creates). Up to eight files, four MiB each, sixteen MiB total; binary reads show metadata, not extracted text.",
    canonical_json(TempInput.model_json_schema()), "product.a2a_file.v1", "builtin")


def _text_page(text: str, metadata: dict[str, object], offset: int) -> str:
    """Size the complete escaped JSON page while retaining code-point continuation offsets."""
    if offset > len(text):
        raise InvalidInput("Temporary text offset is beyond the file")
    def encode(end: int) -> str:
        return canonical_json({"file": metadata, "text": text[offset:end], "offset": offset,
            "offset_unit": "unicode_codepoints", "next_offset": end if end < len(text) else None,
            "representation": "raw_utf8"})
    end = min(len(text), offset + 32768)
    try:
        return encode(end)
    except InvalidInput:
        # A typed JSON candidate can exceed the byte budget even below the character cap.
        upper = end - 1
    lower, accepted_end = offset, offset
    accepted: str | None = None
    while lower <= upper:
        candidate_end = (lower + upper) // 2
        try:
            candidate = encode(candidate_end)
        except InvalidInput:
            upper = candidate_end - 1
        else:
            accepted, accepted_end = candidate, candidate_end
            lower = candidate_end + 1
    if accepted is None or (accepted_end == offset and offset < len(text)):
        raise InvalidInput("Temporary file metadata leaves no room for a text page")
    return accepted


class TempFileExecutor:
    def __init__(self, snapshot: RunSnapshot, step_id: str, files: A2ATempFiles) -> None:
        self.snapshot, self.step_id, self.files = snapshot, step_id, files

    async def execute(self, tool: ResolvedTool, call: ToolCall, scope: CallScope) -> ToolResult:
        try:
            if tool.definition.spec != TEMP_FILE_DEFINITION or call.name != TEMP_FILE_DEFINITION.name or (
                    scope.tenant_id, scope.agent_id, scope.run_id) != (self.snapshot.tenant_id, self.snapshot.agent_id, self.snapshot.workspace.run_id):
                raise AccessDenied("Temporary-file Tool does not match its captured Run")
            body = TempInput.model_validate_json(call.arguments_json)
            operation = sha256(f"{scope.run_id}\0{self.step_id}\0{call.id}".encode()).hexdigest()
            if body.action in ("write", "return") and body.request_id is not None:
                raise InvalidInput("Target operations use their own request, not an arbitrary request ID")
            if body.action == "write":
                if body.text is None:
                    raise InvalidInput("Write requires text")
                result = await self.files.write(scope, name=body.name, content=body.text.encode(), media_type="text/plain",
                    expected_revision=body.expected_revision, operation=operation)
                value = {"file": result.model_dump(mode="json")}
            elif body.action == "import":
                if body.request_id is not None:
                    if body.source_name is None or body.reference is not None:
                        raise InvalidInput("Nested import requires source_name and request_id, not an attachment reference")
                    result = await self.files.import_return(scope, request_id=body.request_id, source_name=body.source_name,
                        name=body.name, expected_revision=body.expected_revision, operation=operation)
                else:
                    if body.reference is None or body.source_name is not None:
                        raise InvalidInput("Import requires an authorized attachment reference")
                    result = await self.files.import_attachment(scope, name=body.name, reference=body.reference,
                        expected_revision=body.expected_revision, operation=operation)
                value = {"file": result.model_dump(mode="json")}
            elif body.action == "return":
                if body.expected_revision is None:
                    raise InvalidInput("Return requires an exact revision")
                result = await self.files.return_file(scope, name=body.name, expected_revision=body.expected_revision)
                value = {"file": result.model_dump(mode="json")}
            elif body.action == "save":
                if body.request_id is None or body.path is None:
                    raise InvalidInput("Save requires request_id and the current output files/ path")
                revision = await self.files.save(scope, request_id=body.request_id, name=body.name, path=body.path,
                    expected_revision=body.expected_revision, operation=operation)
                value = {"saved": True, "path": body.path, "revision": revision}
            else:
                content, result = await self.files.read(scope, name=body.name, request_id=body.request_id)
                try:
                    text = content.decode("utf-8")
                except UnicodeError:
                    value = {"file": result.model_dump(mode="json"), "text": None, "representation": "binary_metadata_only"}
                else:
                    return ToolResult(call.id, "success", _text_page(text, result.model_dump(mode="json"), body.offset))
            return ToolResult(call.id, "success", canonical_json(value))
        except (ValidationError, UnicodeError):
            return ToolResult(call.id, "error", canonical_json({"code": "invalid_input", "message": "Temporary file arguments are invalid"}))
        except DomainError as error:
            return ToolResult(call.id, "error", canonical_json({"code": error.code, "message": str(error)[:512]}))
        except (OSError, TimeoutError):
            return ToolResult(call.id, "uncertain", canonical_json({"message": "Temporary file I/O was not confirmed; inspect before another mutation."}))


def temp_file_binding(snapshot: RunSnapshot, step_id: str, files: A2ATempFiles) -> ExecutorBinding:
    return ExecutorBinding(TEMP_FILE_DEFINITION.executor_key, TempFileExecutor(snapshot, step_id, files),
        builtin=TEMP_FILE_DEFINITION, safe_parallel=False)
