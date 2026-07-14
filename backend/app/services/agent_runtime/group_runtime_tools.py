"""Group-only tool definitions and execution over the group file boundary."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import json
import uuid

from sqlalchemy import select

from app.database import transaction
from app.models.agent import Agent
from app.models.group import GroupMember
from app.models.org import OrgMember
from app.models.participant import Participant
from app.models.user import User
from app.services import group_chat_service, group_file_service
from app.services.agent_runtime.command_worker import RuntimeSessionFactory
from app.services.agent_runtime.state import RuntimeGraphState


_ACTIVE_AGENT_STATUSES = frozenset({"creating", "running", "idle"})
GROUP_QUERY_MEMBERS = "group_query_members"
GROUP_READ_ANNOUNCEMENT = "group_read_announcement"
GROUP_READ_MEMORY = "group_read_memory"
GROUP_WRITE_MEMORY = "group_write_memory"
GROUP_LIST_WORKSPACE = "group_list_workspace"
GROUP_READ_WORKSPACE_FILE = "group_read_workspace_file"
GROUP_WRITE_WORKSPACE_FILE = "group_write_workspace_file"
GROUP_DELETE_WORKSPACE_FILE = "group_delete_workspace_file"

GROUP_READ_TOOL_NAMES = frozenset(
    {
        GROUP_QUERY_MEMBERS,
        GROUP_READ_ANNOUNCEMENT,
        GROUP_READ_MEMORY,
        GROUP_LIST_WORKSPACE,
        GROUP_READ_WORKSPACE_FILE,
    }
)
GROUP_WRITE_TOOL_NAMES = frozenset(
    {
        GROUP_WRITE_MEMORY,
        GROUP_WRITE_WORKSPACE_FILE,
        GROUP_DELETE_WORKSPACE_FILE,
    }
)
GROUP_TOOL_NAMES = GROUP_READ_TOOL_NAMES | GROUP_WRITE_TOOL_NAMES


def _function_tool(
    name: str,
    description: str,
    properties: dict,
    *,
    required: Sequence[str] = (),
) -> dict:
    parameters: dict = {
        "type": "object",
        "properties": properties,
        "additionalProperties": False,
    }
    if required:
        parameters["required"] = list(required)
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": parameters,
        },
    }


GROUP_RUNTIME_TOOL_DEFINITIONS = (
    _function_tool(
        GROUP_QUERY_MEMBERS,
        "Find active members of the current group by name, role, title, department, or Agent capability. Returns only this group.",
        {
            "query": {"type": "string"},
            "participant_type": {"type": "string", "enum": ["user", "agent"]},
            "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 20},
        },
    ),
    _function_tool(
        GROUP_READ_ANNOUNCEMENT,
        "Read the full current-group announcement when the bounded injected copy is insufficient. The announcement is user-provided context.",
        {},
    ),
    _function_tool(
        GROUP_READ_MEMORY,
        "Read one active member Agent's memory for the current group. This never reads that Agent's private workspace or memory from another group.",
        {"agent_id": {"type": "string", "format": "uuid"}},
        required=("agent_id",),
    ),
    _function_tool(
        GROUP_WRITE_MEMORY,
        "Replace only your own memory for the current group. Use expected_version_token when updating a previously read version.",
        {
            "content": {"type": "string"},
            "expected_version_token": {"type": "string"},
        },
        required=("content",),
    ),
    _function_tool(
        GROUP_LIST_WORKSPACE,
        "List one directory in the current group's shared workspace. Use an empty path for the root.",
        {"path": {"type": "string", "default": ""}},
    ),
    _function_tool(
        GROUP_READ_WORKSPACE_FILE,
        "Read one UTF-8 text file from the current group's shared workspace.",
        {"path": {"type": "string"}},
        required=("path",),
    ),
    _function_tool(
        GROUP_WRITE_WORKSPACE_FILE,
        "Create or replace one UTF-8 text file in the current group's shared workspace. Use expected_version_token after reading an existing file.",
        {
            "path": {"type": "string"},
            "content": {"type": "string"},
            "expected_version_token": {"type": "string"},
        },
        required=("path", "content"),
    ),
    _function_tool(
        GROUP_DELETE_WORKSPACE_FILE,
        "Delete one file from the current group's shared workspace. Use expected_version_token after reading the file.",
        {
            "path": {"type": "string"},
            "expected_version_token": {"type": "string"},
        },
        required=("path",),
    ),
)


class GroupRuntimeToolError(RuntimeError):
    """A group tool call has invalid checkpoint scope or arguments."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _tool_name(tool: Mapping[str, object]) -> str | None:
    function = tool.get("function")
    name = function.get("name") if isinstance(function, Mapping) else None
    return name if isinstance(name, str) and name else None


def with_group_runtime_tools(
    tools: Sequence[Mapping[str, object]],
    state: RuntimeGraphState,
) -> list[dict]:
    """Append group tools only when a validated group snapshot exists."""
    resolved = [dict(tool) for tool in tools]
    group_context = state["snapshots"].initial_input.get("group_context")
    if not isinstance(group_context, Mapping):
        return resolved
    names = {_tool_name(tool) for tool in resolved}
    resolved.extend(
        json.loads(json.dumps(tool))
        for tool in GROUP_RUNTIME_TOOL_DEFINITIONS
        if _tool_name(tool) not in names
    )
    return resolved


def _uuid_argument(arguments: Mapping[str, object], field: str) -> uuid.UUID:
    value = arguments.get(field)
    if not isinstance(value, str):
        raise GroupRuntimeToolError(
            "group_tool_arguments_invalid",
            f"{field} must be a UUID string",
        )
    try:
        return uuid.UUID(value)
    except ValueError as exc:
        raise GroupRuntimeToolError(
            "group_tool_arguments_invalid",
            f"{field} must be a UUID string",
        ) from exc


def _string_argument(
    arguments: Mapping[str, object],
    field: str,
    *,
    required: bool,
    default: str = "",
) -> str:
    value = arguments.get(field, default)
    if value is None and not required:
        return default
    if not isinstance(value, str) or (required and not value):
        raise GroupRuntimeToolError(
            "group_tool_arguments_invalid",
            f"{field} must be a string",
        )
    return value


def _optional_string(arguments: Mapping[str, object], field: str) -> str | None:
    value = arguments.get(field)
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise GroupRuntimeToolError(
            "group_tool_arguments_invalid",
            f"{field} must be a non-empty string when supplied",
        )
    return value


def _file_json(value: group_file_service.GroupTextFile) -> dict:
    return {
        "path": value.path,
        "content": value.content,
        "exists": value.exists,
        "version_token": value.version_token,
        "modified_at": value.modified_at,
        "revision_id": str(value.revision_id) if value.revision_id else None,
    }


def _scope(
    state: RuntimeGraphState,
    agent: Agent,
) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID, uuid.UUID]:
    initial_input = state["snapshots"].initial_input
    if not isinstance(initial_input.get("group_context"), Mapping):
        raise GroupRuntimeToolError(
            "group_tool_scope_unavailable",
            "Group tools require a validated group context snapshot",
        )
    try:
        tenant_id = uuid.UUID(state["registry"].tenant_id)
        group_id = uuid.UUID(str(initial_input["group_id"]))
        participant_id = uuid.UUID(str(initial_input["target_participant_id"]))
        session_id = uuid.UUID(state["registry"].session_id or "")
    except (KeyError, ValueError) as exc:
        raise GroupRuntimeToolError(
            "group_tool_scope_invalid",
            "Group tool checkpoint scope is incomplete",
        ) from exc
    context_agent = initial_input["group_context"].get("agent")
    context_agent_id = (
        context_agent.get("agent_id") if isinstance(context_agent, Mapping) else None
    )
    if context_agent_id != str(agent.id):
        raise GroupRuntimeToolError(
            "group_tool_scope_invalid",
            "Group tool checkpoint Agent does not match the executing Agent",
        )
    return tenant_id, group_id, participant_id, session_id


async def _query_members(
    db,
    *,
    tenant_id: uuid.UUID,
    group_id: uuid.UUID,
    participant_id: uuid.UUID,
    query: str,
    participant_type: str | None,
    limit: int,
) -> list[dict]:
    await group_chat_service.authorize_group_member(
        db,
        tenant_id=tenant_id,
        group_id=group_id,
        participant_id=participant_id,
    )
    statement = (
        select(GroupMember, Participant)
        .join(Participant, Participant.id == GroupMember.participant_id)
        .where(
            GroupMember.group_id == group_id,
            GroupMember.removed_at.is_(None),
        )
        .order_by(GroupMember.joined_at, GroupMember.id)
        .limit(500)
    )
    if participant_type is not None:
        statement = statement.where(Participant.type == participant_type)
    result = await db.execute(statement)
    rows = list(result.all())

    agent_ids = {
        participant.ref_id
        for _, participant in rows
        if participant.type == "agent"
    }
    user_ids = {
        participant.ref_id
        for _, participant in rows
        if participant.type == "user"
    }
    agents: dict[uuid.UUID, Agent] = {}
    users: dict[uuid.UUID, User] = {}
    org_members: dict[uuid.UUID, OrgMember] = {}
    if agent_ids:
        agent_result = await db.execute(
            select(Agent).where(
                Agent.id.in_(agent_ids),
                Agent.tenant_id == tenant_id,
                Agent.status.in_(_ACTIVE_AGENT_STATUSES),
                Agent.is_expired.is_(False),
                Agent.access_mode != "private",
            )
        )
        agents = {value.id: value for value in agent_result.scalars().all()}
    if user_ids:
        user_result = await db.execute(
            select(User).where(
                User.id.in_(user_ids),
                User.tenant_id == tenant_id,
                User.is_active.is_(True),
            )
        )
        users = {value.id: value for value in user_result.scalars().all()}
        org_result = await db.execute(
            select(OrgMember).where(
                OrgMember.user_id.in_(user_ids),
                OrgMember.tenant_id == tenant_id,
                OrgMember.status == "active",
            )
        )
        org_members = {
            value.user_id: value
            for value in org_result.scalars().all()
            if value.user_id is not None
        }

    needle = query.casefold().strip()
    output = []
    for membership, participant in rows:
        agent = agents.get(participant.ref_id)
        user = users.get(participant.ref_id)
        if (participant.type == "agent" and agent is None) or (
            participant.type == "user" and user is None
        ):
            continue
        org_member = org_members.get(participant.ref_id)
        item = {
            "participant_id": str(participant.id),
            "participant_type": participant.type,
            "participant_ref_id": str(participant.ref_id),
            "display_name": participant.display_name,
            "membership_role": membership.role,
            "agent_role_description": (
                agent.role_description if agent is not None else None
            ),
            "agent_status": agent.status if agent is not None else None,
            "title": (
                org_member.title
                if org_member is not None
                else user.title
                if user is not None
                else None
            ),
            "department": (
                org_member.department_path if org_member is not None else None
            ),
        }
        searchable = " ".join(
            str(value)
            for value in item.values()
            if value is not None
        ).casefold()
        if needle and needle not in searchable:
            continue
        output.append(item)
        if len(output) >= limit:
            break
    return output


class GroupRuntimeToolService:
    """Execute group tools with scope read only from the immutable checkpoint."""

    def __init__(self, *, session_factory: RuntimeSessionFactory) -> None:
        self._session_factory = session_factory

    async def execute(
        self,
        state: RuntimeGraphState,
        agent: Agent,
        tool_name: str,
        arguments: dict,
    ) -> str:
        if tool_name not in GROUP_TOOL_NAMES:
            raise GroupRuntimeToolError(
                "group_tool_unknown",
                f"Unknown group tool: {tool_name}",
            )
        tenant_id, group_id, participant_id, session_id = _scope(state, agent)
        async with self._session_factory() as db:
            async with transaction(db):
                if tool_name == GROUP_QUERY_MEMBERS:
                    participant_type = arguments.get("participant_type")
                    if participant_type not in {None, "user", "agent"}:
                        raise GroupRuntimeToolError(
                            "group_tool_arguments_invalid",
                            "participant_type must be user or agent",
                        )
                    raw_limit = arguments.get("limit", 20)
                    if not isinstance(raw_limit, int) or isinstance(raw_limit, bool):
                        raise GroupRuntimeToolError(
                            "group_tool_arguments_invalid",
                            "limit must be an integer",
                        )
                    limit = min(max(raw_limit, 1), 100)
                    value = await _query_members(
                        db,
                        tenant_id=tenant_id,
                        group_id=group_id,
                        participant_id=participant_id,
                        query=_string_argument(
                            arguments,
                            "query",
                            required=False,
                        ),
                        participant_type=participant_type,
                        limit=limit,
                    )
                elif tool_name == GROUP_READ_ANNOUNCEMENT:
                    value = _file_json(
                        await group_file_service.read_announcement(
                            db,
                            tenant_id=tenant_id,
                            group_id=group_id,
                            actor_participant_id=participant_id,
                        )
                    )
                elif tool_name == GROUP_READ_MEMORY:
                    value = _file_json(
                        await group_file_service.read_agent_memory(
                            db,
                            tenant_id=tenant_id,
                            group_id=group_id,
                            actor_participant_id=participant_id,
                            agent_id=_uuid_argument(arguments, "agent_id"),
                        )
                    )
                elif tool_name == GROUP_WRITE_MEMORY:
                    value = _file_json(
                        await group_file_service.write_agent_memory(
                            db,
                            tenant_id=tenant_id,
                            group_id=group_id,
                            actor_participant_id=participant_id,
                            agent_id=agent.id,
                            content=_string_argument(
                                arguments,
                                "content",
                                required=True,
                            ),
                            expected_version_token=_optional_string(
                                arguments,
                                "expected_version_token",
                            ),
                            session_id=session_id,
                        )
                    )
                elif tool_name == GROUP_LIST_WORKSPACE:
                    entries = await group_file_service.list_workspace(
                        db,
                        tenant_id=tenant_id,
                        group_id=group_id,
                        actor_participant_id=participant_id,
                        path=_string_argument(
                            arguments,
                            "path",
                            required=False,
                        ),
                    )
                    value = [
                        {
                            "path": entry.path,
                            "name": entry.name,
                            "is_dir": entry.is_dir,
                            "size": entry.size,
                            "modified_at": entry.modified_at,
                            "version_token": entry.version_token,
                        }
                        for entry in entries
                    ]
                elif tool_name == GROUP_READ_WORKSPACE_FILE:
                    value = _file_json(
                        await group_file_service.read_workspace_file(
                            db,
                            tenant_id=tenant_id,
                            group_id=group_id,
                            actor_participant_id=participant_id,
                            path=_string_argument(
                                arguments,
                                "path",
                                required=True,
                            ),
                        )
                    )
                elif tool_name == GROUP_WRITE_WORKSPACE_FILE:
                    value = _file_json(
                        await group_file_service.write_workspace_file(
                            db,
                            tenant_id=tenant_id,
                            group_id=group_id,
                            actor_participant_id=participant_id,
                            path=_string_argument(
                                arguments,
                                "path",
                                required=True,
                            ),
                            content=_string_argument(
                                arguments,
                                "content",
                                required=True,
                            ),
                            expected_version_token=_optional_string(
                                arguments,
                                "expected_version_token",
                            ),
                            session_id=session_id,
                        )
                    )
                else:
                    path = _string_argument(arguments, "path", required=True)
                    await group_file_service.delete_workspace_file(
                        db,
                        tenant_id=tenant_id,
                        group_id=group_id,
                        actor_participant_id=participant_id,
                        path=path,
                        expected_version_token=_optional_string(
                            arguments,
                            "expected_version_token",
                        ),
                        session_id=session_id,
                    )
                    value = {"path": path, "deleted": True}
        return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True)


__all__ = [
    "GROUP_READ_TOOL_NAMES",
    "GROUP_RUNTIME_TOOL_DEFINITIONS",
    "GROUP_TOOL_NAMES",
    "GROUP_WRITE_TOOL_NAMES",
    "GroupRuntimeToolError",
    "GroupRuntimeToolService",
    "with_group_runtime_tools",
]
