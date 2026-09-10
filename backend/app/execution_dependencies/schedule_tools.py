"""Native schedule configuration through typed owners; no fabricated human identity."""

import json
from dataclasses import fields
from hashlib import sha256
from uuid import UUID

from pydantic import TypeAdapter, ValidationError

from app.execution_dependencies.scheduled_inputs import ScheduledInputs
from app.infrastructure.errors import AccessDenied, DomainError, InvalidInput
from app.infrastructure.transactions import transaction
from app.modules.agent.public import AgentService
from app.modules.heartbeat.public import HeartbeatConfig, HeartbeatService
from app.modules.run.public import RunService
from app.modules.tool.public import (
    AgentToolResolutionScope,
    CallScope,
    DefinitionSpec,
    ExecutorBinding,
    ResolvedTool,
    ToolCall,
    ToolResult,
    canonical_json,
    json_object,
)
from app.modules.trigger.public import TriggerConfig, TriggerService


def trigger_config(value: object, *, timezone: str) -> TriggerConfig:
    if not isinstance(value, dict) or set(value) - {field.name for field in fields(TriggerConfig)}:
        raise InvalidInput("Trigger config contains unsupported fields")
    data = {"timezone": timezone, **value}
    try:
        return TypeAdapter(TriggerConfig).validate_json(json.dumps(data), strict=True)
    except (ValueError, TypeError, ValidationError):
        raise InvalidInput("Trigger config is invalid") from None


def heartbeat_config(value: object, *, timezone: str) -> HeartbeatConfig:
    if not isinstance(value, dict) or set(value) - {field.name for field in fields(HeartbeatConfig)}:
        raise InvalidInput("Heartbeat config contains unsupported fields")
    try:
        return TypeAdapter(HeartbeatConfig).validate_json(json.dumps({"timezone": timezone, **value}), strict=True)
    except (ValueError, TypeError, ValidationError):
        raise InvalidInput("Heartbeat config is invalid") from None


def _config_schema(config: type[TriggerConfig] | type[HeartbeatConfig]) -> dict:
    schema = TypeAdapter(config).json_schema()
    schema["properties"]["timezone"].pop("default", None)
    return schema


SCHEDULE_TOOL_DEFINITIONS = (
    DefinitionSpec("trigger", "Manage this Agent's triggers. Use result with trigger_id and occurrence_id to read complete authorized terminal results, including runs without a message destination; continue with result.next_offset. Timezone defaults to the Agent timezone. Poll Credential stores the complete Authorization header value; webhook Credential is an HMAC key. Pass only Credential IDs, never Secrets.", canonical_json({
        "type": "object", "properties": {"action": {"type": "string", "enum": ["create", "update", "get", "list", "remove", "fire", "result"]},
            "trigger_id": {"type": "string", "format": "uuid"}, "config": _config_schema(TriggerConfig),
            "occurrence_id": {"type": "string", "format": "uuid"},
            "content_offset": {"type": "integer", "minimum": 0, "description": "Continue get with next_offset or result with result.next_offset until null; result pages contain at most 8000 characters."},
            "enabled": {"type": "boolean"}, "after_id": {"type": "string", "format": "uuid"},
            "limit": {"type": "integer", "minimum": 1, "maximum": 100}}, "required": ["action"], "additionalProperties": False}),
        "schedule.trigger.v1", "builtin"),
    DefinitionSpec("heartbeat", "Configure or inspect this Agent's independent heartbeat. Use result with occurrence_id to read complete authorized terminal results and continue with result.next_offset. It never creates a trigger. Timezone defaults to the Agent timezone; set enabled=false to disable it.", canonical_json({
        "type": "object", "properties": {"action": {"type": "string", "enum": ["configure", "get", "result"]},
            "occurrence_id": {"type": "string", "format": "uuid"},
            "config": _config_schema(HeartbeatConfig), "enabled": {"type": "boolean"},
            "content_offset": {"type": "integer", "minimum": 0, "description": "Continue get with next_offset or result with result.next_offset until null; result pages contain at most 8000 characters."}},
        "required": ["action"], "additionalProperties": False}), "schedule.heartbeat.v1", "builtin"),
)


def _json_view(value: object) -> object:
    return json.loads(TypeAdapter(type(value)).dump_json(value))


def _view_fragment(value: object, offset: object = 0) -> object:
    text = TypeAdapter(type(value)).dump_json(value).decode()
    if type(offset) is not int or offset < 0 or offset >= len(text):
        raise InvalidInput("Schedule content offset is invalid")
    end = min(offset + 16000, len(text))
    return {"content_json": text[offset:end], "next_offset": end if end < len(text) else None}


def _list_item(value: object) -> object:
    data = _json_view(value)
    assert isinstance(data, dict)
    config = data["config"]
    return {"id": data["id"], "name": config["name"], "kind": config["kind"],
        "enabled": data["enabled"], "fire_count": data["fire_count"]}


def _id(value: object) -> UUID:
    if not isinstance(value, str):
        raise InvalidInput("Schedule identity must be a UUID")
    return UUID(value)


def _enabled(value: object) -> bool:
    if type(value) is not bool:
        raise InvalidInput("Schedule enabled must be boolean")
    return value


def _limit(value: object) -> int:
    if type(value) is not int or not 1 <= value <= 100:
        raise InvalidInput("Schedule page limit must be between one and 100")
    return value


class _ScheduleExecutor:
    def __init__(self, inputs: ScheduledInputs, scope: AgentToolResolutionScope, run_id: UUID, step_id: str,
            definition: DefinitionSpec) -> None:
        self.inputs, self.scope, self.run_id, self.step_id, self.definition = inputs, scope, run_id, step_id, definition

    async def execute(self, tool: ResolvedTool, call: ToolCall, scope: CallScope) -> ToolResult:
        try:
            if ((scope.tenant_id, scope.agent_id, scope.run_id) != (self.scope.tenant_id, self.scope.agent_id, self.run_id)
                    or self.scope.role != "main" or tool.definition.tenant_id != scope.tenant_id
                    or tool.definition.spec != self.definition or call.name != self.definition.name):
                raise AccessDenied("Schedule Tool is outside this Main scope")
            args = json_object(call.arguments_json)
            action = args.get("action")
            fire = None
            result: object
            async with transaction(self.inputs.database.control_sessions) as tx:
                origin_run = await RunService(tx).verify_main_tool_origin(tenant_id=scope.tenant_id, run_id=scope.run_id,
                    step_id=self.step_id, call_id=call.id, tool_name=call.name)
                agent = await AgentService(tx).get_for_agent_execution(tenant_id=scope.tenant_id, agent_id=scope.agent_id)
                if action == "result":
                    required = {"action", "occurrence_id"} | ({"trigger_id"} if call.name == "trigger" else set())
                    if not required <= args.keys() or args.keys() - (required | {"content_offset"}):
                        raise InvalidInput("Result action fields are invalid")
                    offset = args.get("content_offset", 0)
                    if not isinstance(offset, int) or isinstance(offset, bool):
                        raise InvalidInput("Result content offset is invalid")
                    occurrence_id = _id(args["occurrence_id"])
                    fragment = (await TriggerService(tx).read_result_for_run(origin_run,
                        trigger_id=_id(args["trigger_id"]), occurrence_id=occurrence_id, content_offset=offset)
                        if call.name == "trigger" else await HeartbeatService(tx).read_result_for_run(
                            origin_run, occurrence_id=occurrence_id, content_offset=offset))
                    payload = {"result": _json_view(fragment) if fragment is not None else None}
                elif call.name == "heartbeat":
                    owner = HeartbeatService(tx, enabled_sources=self.inputs.execution.market.enabled_source_ids)
                    if action == "get" and not args.keys() - {"action", "content_offset"}:
                        result = await owner.get_for_agent(self.scope)
                    elif action == "configure" and {"action", "config"} <= args.keys() and not args.keys() - {"action", "config", "enabled"}:
                        result = await owner.configure_for_agent(self.scope, config=heartbeat_config(args["config"], timezone=agent.timezone),
                            enabled=_enabled(args.get("enabled", True)), now=self.inputs._now(), origin_run=origin_run)
                    else:
                        raise InvalidInput("Heartbeat action or fields are invalid")
                    payload = _view_fragment(result, args.get("content_offset", 0))
                else:
                    triggers = TriggerService(tx, enabled_sources=self.inputs.execution.market.enabled_source_ids)
                    if action == "create" and {"action", "config"} <= args.keys() and not args.keys() - {"action", "config", "enabled"}:
                        result = await triggers.create_for_agent(self.scope, config=trigger_config(args["config"], timezone=agent.timezone),
                            enabled=_enabled(args.get("enabled", True)), now=self.inputs._now(), origin_run=origin_run)
                        payload = _view_fragment(result)
                    elif action == "list" and not args.keys() - {"action", "limit", "after_id"}:
                        values = await triggers.list_for_agent(self.scope, limit=_limit(args.get("limit", 20)),
                            after_id=_id(args["after_id"]) if "after_id" in args else None)
                        payload = {"triggers": [_list_item(value) for value in values],
                            "next_after_id": str(values[-1].id) if len(values) == _limit(args.get("limit", 20)) else None}
                    elif action in ("get", "remove", "update", "fire"):
                        allowed = {"action", "trigger_id"} | ({"config", "enabled"} if action == "update" else {"content_offset"} if action == "get" else set())
                        if not {"action", "trigger_id"} <= args.keys() or args.keys() - allowed:
                            raise InvalidInput("Trigger action fields are invalid")
                        id = _id(args["trigger_id"])
                        if action == "get":
                            result = await triggers.get_for_agent(self.scope, trigger_id=id)
                        elif action == "remove":
                            result = await triggers.remove_for_agent(self.scope, trigger_id=id)
                        elif action == "update":
                            result = await triggers.update_for_agent(self.scope, trigger_id=id,
                                config=trigger_config(args.get("config"), timezone=agent.timezone), enabled=_enabled(args.get("enabled", True)), origin_run=origin_run)
                        else:
                            target = await triggers.get_for_agent(self.scope, trigger_id=id)
                            if not set(target.delegated_connection_ids) <= self.scope.authorized_personal_connections:
                                raise AccessDenied("Current execution cannot start another owner's private schedule")
                            occurrence = await triggers.accept(tenant_id=scope.tenant_id, trigger_id=id,
                                source_key="agent:" + sha256(f"{scope.run_id}:{self.step_id}:{call.id}".encode()).hexdigest(),
                                now=self.inputs._now(), event_kind="manual")
                            fire = occurrence
                            result = occurrence
                            if not set(occurrence.delegated_connection_ids) <= self.scope.authorized_personal_connections:
                                raise AccessDenied("Current execution does not authorize the accepted schedule's account")
                        payload = ({"accepted": True, "occurrence_id": str(fire.id),
                            "run_id": str(fire.run_id) if fire.run_id else None} if fire is not None
                            else _view_fragment(result, args.get("content_offset", 0)))
                    else:
                        raise InvalidInput("Trigger action is invalid")
            if fire is not None:
                await self.inputs._execute(fire)
                async with transaction(self.inputs.database.control_sessions) as tx:
                    current = await TriggerService(tx).get_occurrence(tenant_id=scope.tenant_id, occurrence_id=fire.id)
                payload = {"accepted": True, "occurrence_id": str(current.id), "admission": current.admission,
                    "run_id": str(current.run_id) if current.run_id else None}
            return ToolResult(call.id, "success", canonical_json(payload, maximum=250000))
        except (ValueError, TypeError):
            return ToolResult(call.id, "error", '{"message":"Schedule arguments are invalid"}')
        except DomainError as error:
            return ToolResult(call.id, "error", canonical_json({"code": error.code, "message": str(error)[:512]}))


def schedule_tool_bindings(*, inputs: ScheduledInputs, scope: AgentToolResolutionScope, run_id: UUID,
        step_id: str) -> tuple[ExecutorBinding, ...]:
    if scope.role != "main":
        return ()
    return tuple(ExecutorBinding(definition.executor_key,
        _ScheduleExecutor(inputs, scope, run_id, step_id, definition), builtin=definition) for definition in SCHEDULE_TOOL_DEFINITIONS)
