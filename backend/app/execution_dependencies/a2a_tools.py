"""Immediate cross-Agent request acceptance through the A2A product owner."""

from dataclasses import asdict
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.execution_dependencies.other_product_inputs import OtherProductInputs
from app.infrastructure.errors import AccessDenied, DomainError, InvalidInput
from app.infrastructure.transactions import transaction
from app.modules.a2a.public import A2AService
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


class AttachmentReference(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    reference: str = Field(min_length=1, max_length=512)
    name: str | None = Field(default=None, max_length=512)
    media_type: str | None = Field(default=None, max_length=256)


class AgentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    action: Literal["send", "answer", "inspect", "wait", "takeover"] = "send"
    target_agent_id: UUID | None = None
    intent: Literal["notify", "consult", "task_delegate"] | None = None
    text: str = Field(default="", max_length=65536)
    references: list[AttachmentReference] = Field(default_factory=list, max_length=32)
    request_id: UUID | None = None
    waiting_reference: str | None = Field(default=None, max_length=512)
    content_offset: int = Field(default=0, ge=0, le=17000000)


A2A_DEFINITION = DefinitionSpec("send_message_to_agent",
    "Send explicit work or information to another authorized Agent. notify is one-way; consult and task_delegate return acceptance now and deliver a result later. Use wait for a specified request when no independent work remains; it releases execution without asking a person. Use answer for its input question, inspect for a result, and takeover from a new Main in the same conversation after the previous recipient ends. The target remains independent; acceptance is not completion.",
    canonical_json(AgentRequest.model_json_schema()), "product.a2a.v1", "builtin")


class A2AExecutor:
    def __init__(self, snapshot: RunSnapshot, step_id: str, other: OtherProductInputs) -> None:
        self.snapshot, self.step_id, self.other = snapshot, step_id, other

    async def execute(self, tool: ResolvedTool, call: ToolCall, scope: CallScope) -> ToolResult:
        try:
            if (self.snapshot.role != "main" or tool.definition.spec != A2A_DEFINITION or call.name != A2A_DEFINITION.name
                    or (scope.tenant_id, scope.agent_id, scope.run_id) != (
                        self.snapshot.tenant_id, self.snapshot.agent_id, self.snapshot.workspace.run_id)):
                raise AccessDenied("Cross-Agent work is unavailable in this execution")
            body = AgentRequest.model_validate_json(call.arguments_json)
            if body.action == "send":
                if body.target_agent_id is None or body.intent is None or not body.text.strip():
                    raise InvalidInput("Send requires a target Agent, intent and text")
                result = await self.other.submit_a2a(self.snapshot, self.step_id, call.id,
                    target_agent_id=body.target_agent_id, intent=body.intent,
                    input=InputContent(body.text, tuple(InputReference(item.reference, item.name, item.media_type) for item in body.references)))
                output = {"accepted": result.error is None, "request_id": str(result.request.id), "error": result.error}
            else:
                if body.references and body.action != "answer":
                    raise InvalidInput("Attachment delegation requires a send or answer")
                if body.request_id is None:
                    raise InvalidInput("A request identity is required")
                changed = None
                async with transaction(self.other.database.execution_sessions) as tx:
                    service = A2AService(tx)
                    if body.action == "inspect":
                        part = await service.read_result(tenant_id=scope.tenant_id, source_run_id=scope.run_id,
                            request_id=body.request_id, content_offset=body.content_offset)
                        files = await service.returned_files_for_source(tenant_id=scope.tenant_id, source_run_id=scope.run_id,
                            request_id=body.request_id)
                        output = {"result": asdict(part) if part else None, "files": [asdict(file) for file in files]}
                    elif body.action == "wait":
                        request, should_wait, changed = await service.prepare_wait(tenant_id=scope.tenant_id,
                            source_run_id=scope.run_id, request_id=body.request_id, step_id=self.step_id, call_id=call.id)
                        output = {"request_id": str(request.id), "ready": not should_wait, "result": request.result}
                        if should_wait:
                            output["wait_for_a2a"] = True
                    elif body.action == "takeover":
                        request = await service.takeover(tenant_id=scope.tenant_id, source_run_id=scope.run_id,
                            request_id=body.request_id, step_id=self.step_id, call_id=call.id)
                        output = {"accepted": True, "request_id": str(request.id), "result": request.result}
                    else:
                        if not body.text.strip() or body.waiting_reference is None:
                            raise InvalidInput("Answer requires text and the target's waiting reference")
                        changed = await service.answer(tenant_id=scope.tenant_id, source_run_id=scope.run_id,
                            request_id=body.request_id, step_id=self.step_id, call_id=call.id,
                            waiting_reference=body.waiting_reference,
                            input=InputContent(body.text, tuple(InputReference(item.reference, item.name, item.media_type) for item in body.references)),
                            attachment_authorizer=self.other.attachment_authorizer)
                        output = {"accepted": True, "run_id": str(changed.run.id)}
                if body.action in ("wait", "takeover", "answer"):
                    self.other.track_request(tenant_id=scope.tenant_id, request_id=body.request_id)
                if changed is not None:
                    assert self.other.runtime is not None
                    await self.other.runtime.post_commit(changed)
            return ToolResult(call.id, "success", canonical_json(output))
        except ValidationError:
            return ToolResult(call.id, "error", canonical_json({"code": "invalid_input", "message": "Cross-Agent request fields are invalid"}))
        except DomainError as error:
            return ToolResult(call.id, "error", canonical_json({"code": error.code, "message": str(error)[:1024]}))

    def binding(self) -> ExecutorBinding:
        return ExecutorBinding(A2A_DEFINITION.executor_key, self, builtin=A2A_DEFINITION)
