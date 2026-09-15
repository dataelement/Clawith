"""Main-only message Tool; the initiating product owns acceptance and destination."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.infrastructure.errors import AccessDenied, DomainError, InvalidInput
from app.modules.run.public import InputContent, InputReference, RunSnapshot
from app.modules.tool.public import (
    CallScope,
    DefinitionSpec,
    ExecutorBinding,
    ResolvedTool,
    ToolCall,
    ToolResult,
    canonical_json,
)


class MessageReference(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)
    reference: str = Field(min_length=1, max_length=512)
    name: str | None = Field(default=None, max_length=512)
    media_type: str | None = Field(default=None, max_length=256)


class MessageFile(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)
    path: str = Field(min_length=1, max_length=512, pattern=r"^files/")
    expected_revision: str = Field(min_length=1, max_length=256)
    subject: Literal["output", "agent"] = "output"


@dataclass(frozen=True, slots=True)
class WorkspaceMessageFile:
    path: str
    expected_revision: str
    subject: Literal["output", "agent"]


class MessageInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)
    text: str = Field(default="", max_length=65536)
    references: list[MessageReference] = Field(default_factory=list, max_length=8)
    files: list[MessageFile] = Field(default_factory=list, max_length=8)


SEND_MESSAGE = DefinitionSpec("send_message",
    "Send text and optional files to the current conversation. Use references for authorized input attachments, or files for exact Workspace files/ paths and their current revisions from output or Agent space. File bytes are captured immutably before acceptance (up to 8 files, 4 MiB each, 16 MiB total). Sending does not end work; finishing does not send a message. Use need_input for essential missing information.",
    canonical_json(MessageInput.model_json_schema()), "product.send_message.v1", "builtin")

MessageSender = Callable[[RunSnapshot, str, str, InputContent, tuple[WorkspaceMessageFile, ...]], Awaitable[dict[str, object]]]


class MessageExecutor:
    def __init__(self, snapshot: RunSnapshot, step_id: str, sender: MessageSender) -> None:
        self.snapshot, self.step_id, self.sender = snapshot, step_id, sender

    async def execute(self, tool: ResolvedTool, call: ToolCall, scope: CallScope) -> ToolResult:
        try:
            if (self.snapshot.role != "main" or tool.definition.spec != SEND_MESSAGE or call.name != SEND_MESSAGE.name
                    or (scope.tenant_id, scope.agent_id, scope.run_id) != (
                        self.snapshot.tenant_id, self.snapshot.agent_id, self.snapshot.workspace.run_id)):
                raise AccessDenied("Message Tool is unavailable in this execution")
            body = MessageInput.model_validate_json(call.arguments_json)
            if not body.text.strip() and not body.references and not body.files:
                raise InvalidInput("A message requires text or files")
            if len(body.references) + len(body.files) > 8:
                raise InvalidInput("A message accepts at most eight files")
            result = await self.sender(self.snapshot, self.step_id, call.id,
                InputContent(body.text, tuple(InputReference(item.reference, item.name, item.media_type) for item in body.references)),
                tuple(WorkspaceMessageFile(item.path, item.expected_revision, item.subject) for item in body.files))
            return ToolResult(call.id, "success", canonical_json(result))
        except ValidationError:
            return ToolResult(call.id, "error", canonical_json({"code": "invalid_input", "message": "Message fields are invalid"}))
        except DomainError as error:
            return ToolResult(call.id, "error", canonical_json({"code": error.code, "message": str(error)[:1024]}))

    def binding(self) -> ExecutorBinding:
        return ExecutorBinding(SEND_MESSAGE.executor_key, self, builtin=SEND_MESSAGE)
