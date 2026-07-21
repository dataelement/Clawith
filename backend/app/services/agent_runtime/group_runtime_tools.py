"""Group-only tool definitions and execution over the group file boundary."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
import hashlib
import json
import uuid

from sqlalchemy import select

from app.models.agent import Agent
from app.models.group import GroupMember
from app.models.org import OrgMember
from app.models.participant import Participant
from app.models.user import User
from app.services import group_chat_service, group_file_service
from app.services.agent_runtime.command_worker import RuntimeSessionFactory
from app.services.agent_runtime.state import RuntimeContext, RuntimeGraphState
from app.services.agent_runtime.tool_execution import (
    ToolExecutionError,
    ToolExecutionOutcome,
    ToolExecutionReconciliationPending,
    assert_tool_execution_fence,
)
from app.services.builtin_tool_definitions import GROUP_RUNTIME_TOOL_DEFINITIONS


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
GROUP_WORKSPACE_MUTATION_TOOL_NAMES = frozenset(
    {GROUP_WRITE_WORKSPACE_FILE, GROUP_DELETE_WORKSPACE_FILE}
)
GROUP_TOOL_NAMES = GROUP_READ_TOOL_NAMES | GROUP_WRITE_TOOL_NAMES

_AGENT_WORKSPACE_TOOL_NAMES = frozenset(
    {
        "list_files",
        "read_file",
        "search_files",
        "find_files",
        "write_file",
        "edit_file",
        "move_file",
        "delete_file",
    }
)
_AGENT_WORKSPACE_GROUP_SCOPE_NOTE = (
    "Group scope note: this tool accesses the Agent's own Workspace, not the "
    "current Group Workspace. Paths listed in `group_context.workspace_index` "
    "must use the corresponding `group_*` workspace tool. A missing result here "
    "does not mean that the path is absent from Group Workspace."
)


class GroupRuntimeToolError(RuntimeError):
    """A group tool call has invalid checkpoint scope or arguments."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class GroupWorkspaceReconciliationPending(ToolExecutionReconciliationPending):
    """A prepared Group storage mutation must be reconciled, never repeated."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "group_workspace_reconciliation_pending",
        defer_without_attempt: bool = False,
    ) -> None:
        super().__init__(
            code,
            message,
            defer_without_attempt=defer_without_attempt,
        )


def _tool_name(tool: Mapping[str, object]) -> str | None:
    function = tool.get("function")
    name = function.get("name") if isinstance(function, Mapping) else None
    return name if isinstance(name, str) and name else None


def with_group_runtime_tools(
    tools: Sequence[Mapping[str, object]],
    state: RuntimeGraphState,
) -> list[dict]:
    """Append group tools only when a validated group snapshot exists."""
    resolved = [deepcopy(dict(tool)) for tool in tools]
    group_context = state["snapshots"].initial_input.get("group_context")
    if not isinstance(group_context, Mapping):
        return resolved
    for tool in resolved:
        function = tool.get("function")
        if not isinstance(function, dict) or _tool_name(tool) not in _AGENT_WORKSPACE_TOOL_NAMES:
            continue
        description = function.get("description")
        function["description"] = (
            f"{description.strip()}\n\n{_AGENT_WORKSPACE_GROUP_SCOPE_NOTE}"
            if isinstance(description, str) and description.strip()
            else _AGENT_WORKSPACE_GROUP_SCOPE_NOTE
        )
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


def _integer_argument(
    arguments: Mapping[str, object],
    field: str,
    *,
    default: int,
    minimum: int,
    maximum: int | None = None,
) -> int:
    value = arguments.get(field, default)
    if isinstance(value, bool) or not isinstance(value, int):
        raise GroupRuntimeToolError(
            "group_tool_arguments_invalid",
            f"{field} must be an integer",
        )
    if value < minimum or maximum is not None and value > maximum:
        raise GroupRuntimeToolError(
            "group_tool_arguments_invalid",
            f"{field} is outside its supported range",
        )
    return value


def _read_window(arguments: Mapping[str, object]) -> tuple[int, int]:
    return (
        _integer_argument(
            arguments,
            "offset",
            default=0,
            minimum=0,
        ),
        _integer_argument(
            arguments,
            "max_bytes",
            default=4096,
            minimum=4,
            maximum=6144,
        ),
    )


def _file_json(
    value: group_file_service.GroupTextFile,
    *,
    include_content: bool = True,
    offset: int = 0,
    max_bytes: int = 4096,
) -> dict:
    content = value.content.encode("utf-8")
    result = {
        "path": value.path,
        "exists": value.exists,
        "version_token": value.version_token,
        "modified_at": value.modified_at,
        "revision_id": str(value.revision_id) if value.revision_id else None,
        "content_hash": hashlib.sha256(content).hexdigest(),
    }
    if include_content:
        start = min(offset, len(content))
        while start < len(content) and content[start] & 0xC0 == 0x80:
            start += 1
        end = min(start + max_bytes, len(content))
        while end > start:
            try:
                chunk = content[start:end].decode("utf-8")
                break
            except UnicodeDecodeError:
                end -= 1
        else:
            chunk = ""
        result.update(
            {
                "content": chunk,
                "offset": start,
                "next_offset": end if end < len(content) else None,
                "has_more": end < len(content),
                "total_bytes": len(content),
            }
        )
    return result


def _workspace_operation_outcome(
    receipt: group_file_service.RuntimeWorkspaceOperationReceipt,
) -> ToolExecutionOutcome:
    value = {
        "operation_id": str(receipt.operation_id),
        "revision_id": str(receipt.revision_id),
        "operation": receipt.operation,
        "path": receipt.path,
        "content_hash": receipt.content_hash,
        "deleted": receipt.deleted,
    }
    return ToolExecutionOutcome(
        status="succeeded",
        result_summary=json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
        ),
        result_ref=None,
        metadata={
            "operation_id": str(receipt.operation_id),
            "operation": receipt.operation,
            "workspace_path": receipt.path,
        },
    )


def _scope(
    state: RuntimeGraphState,
    context: RuntimeContext,
    agent: Agent,
) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID, uuid.UUID]:
    initial_input = state["snapshots"].initial_input
    if not isinstance(initial_input.get("group_context"), Mapping):
        raise GroupRuntimeToolError(
            "group_tool_scope_unavailable",
            "Group tools require a validated group context snapshot",
        )
    try:
        tenant_id = uuid.UUID(context.tenant_id)
        group_id = uuid.UUID(str(initial_input["group_id"]))
        participant_id = uuid.UUID(str(initial_input["target_participant_id"]))
        session_id = uuid.UUID(context.session_id or "")
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
            "agent_id": str(agent.id) if agent is not None else None,
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

    @staticmethod
    async def _assert_workspace_fence(
        db,
        *,
        tenant_id: uuid.UUID,
        operation_id: uuid.UUID,
        lease_owner: str,
    ) -> None:
        try:
            await assert_tool_execution_fence(
                db,
                tenant_id=tenant_id,
                execution_id=operation_id,
                lease_owner=lease_owner,
            )
        except ToolExecutionError as exc:
            if exc.code != "tool_execution_lease_lost":
                raise
            raise GroupWorkspaceReconciliationPending(
                "Group workspace executor lost its durable fence",
                code="group_workspace_fence_lost",
                defer_without_attempt=True,
            ) from exc

    async def _execute_workspace_operation(
        self,
        *,
        tenant_id: uuid.UUID,
        group_id: uuid.UUID,
        participant_id: uuid.UUID,
        session_id: uuid.UUID,
        tool_name: str,
        arguments: dict,
        operation_id: uuid.UUID,
        lease_owner: str,
    ) -> ToolExecutionOutcome:
        try:
            async with self._session_factory() as db:
                async with db.begin():
                    await self._assert_workspace_fence(
                        db,
                        tenant_id=tenant_id,
                        operation_id=operation_id,
                        lease_owner=lease_owner,
                    )
                    path = _string_argument(arguments, "path", required=True)
                    expected_version_token = _optional_string(
                        arguments,
                        "expected_version_token",
                    )
                    if tool_name == GROUP_WRITE_WORKSPACE_FILE:
                        prepared = (
                            await group_file_service.prepare_runtime_workspace_write(
                                db,
                                tenant_id=tenant_id,
                                group_id=group_id,
                                actor_participant_id=participant_id,
                                operation_id=operation_id,
                                path=path,
                                content=_string_argument(
                                    arguments,
                                    "content",
                                    required=True,
                                ),
                                expected_version_token=expected_version_token,
                                session_id=session_id,
                            )
                        )
                    else:
                        prepared = (
                            await group_file_service.prepare_runtime_workspace_delete(
                                db,
                                tenant_id=tenant_id,
                                group_id=group_id,
                                actor_participant_id=participant_id,
                                operation_id=operation_id,
                                path=path,
                                expected_version_token=expected_version_token,
                                session_id=session_id,
                            )
                        )
        except (GroupRuntimeToolError, GroupWorkspaceReconciliationPending):
            raise
        except group_file_service.GroupFileServiceError as exc:
            raise GroupRuntimeToolError(exc.code, str(exc)) from exc
        except Exception as exc:
            raise GroupWorkspaceReconciliationPending(
                "Group workspace operation preparation did not settle"
            ) from exc

        try:
            async with self._session_factory() as db:
                async with db.begin():
                    await self._assert_workspace_fence(
                        db,
                        tenant_id=tenant_id,
                        operation_id=operation_id,
                        lease_owner=lease_owner,
                    )
                    await group_file_service.apply_runtime_workspace_operation(
                        prepared
                    )
        except GroupWorkspaceReconciliationPending:
            raise
        except group_file_service.GroupFileServiceError as exc:
            raise GroupRuntimeToolError(exc.code, str(exc)) from exc
        except Exception as exc:
            # The storage call may already have succeeded.  Preserve the
            # started ledger row so the exact operation ID can be reconciled.
            raise GroupWorkspaceReconciliationPending(
                "Group workspace storage outcome requires reconciliation"
            ) from exc

        try:
            async with self._session_factory() as db:
                async with db.begin():
                    await self._assert_workspace_fence(
                        db,
                        tenant_id=tenant_id,
                        operation_id=operation_id,
                        lease_owner=lease_owner,
                    )
                    receipt = (
                        await group_file_service.reconcile_runtime_workspace_operation(
                            db,
                            group_id=group_id,
                            operation_id=operation_id,
                        )
                    )
        except GroupWorkspaceReconciliationPending:
            raise
        except Exception as exc:
            # Storage is already proven written/deleted.  Never call apply a
            # second time; the next Runtime pass only finalizes revision/ledger.
            raise GroupWorkspaceReconciliationPending(
                "Group workspace revision settlement requires reconciliation"
            ) from exc
        return _workspace_operation_outcome(receipt)

    async def reconcile_workspace_operation(
        self,
        state: RuntimeGraphState,
        context: RuntimeContext,
        agent: Agent,
        tool_name: str,
        arguments: dict,
        *,
        operation_id: uuid.UUID,
        lease_owner: str,
    ) -> ToolExecutionOutcome:
        """Resolve a started mutation only from its durable revision/storage facts."""
        if tool_name not in GROUP_WORKSPACE_MUTATION_TOOL_NAMES:
            raise GroupRuntimeToolError(
                "group_tool_unknown",
                f"Tool {tool_name} is not a Group workspace mutation",
            )
        tenant_id, group_id, _, _ = _scope(state, context, agent)
        # Idempotency matching already checked the exact arguments in the Tool
        # Ledger.  Parse the path here so malformed replay state still fails
        # closed before reading a different operation.
        _string_argument(arguments, "path", required=True)
        return await self.reconcile_workspace_operation_by_scope(
            tenant_id=tenant_id,
            group_id=group_id,
            tool_name=tool_name,
            operation_id=operation_id,
            lease_owner=lease_owner,
        )

    async def reconcile_workspace_operation_by_scope(
        self,
        *,
        tenant_id: uuid.UUID,
        group_id: uuid.UUID,
        tool_name: str,
        operation_id: uuid.UUID,
        lease_owner: str,
    ) -> ToolExecutionOutcome:
        """Reconcile one fenced operation without reconstructing Graph state."""
        if tool_name not in GROUP_WORKSPACE_MUTATION_TOOL_NAMES:
            raise GroupRuntimeToolError(
                "group_tool_unknown",
                f"Tool {tool_name} is not a Group workspace mutation",
            )
        try:
            async with self._session_factory() as db:
                async with db.begin():
                    await self._assert_workspace_fence(
                        db,
                        tenant_id=tenant_id,
                        operation_id=operation_id,
                        lease_owner=lease_owner,
                    )
                    receipt = (
                        await group_file_service.reconcile_runtime_workspace_operation(
                            db,
                            group_id=group_id,
                            operation_id=operation_id,
                        )
                    )
        except GroupWorkspaceReconciliationPending:
            raise
        except group_file_service.GroupFileServiceError as exc:
            status = (
                "failed"
                if exc.code == "group_workspace_operation_not_prepared"
                else "unknown"
            )
            return ToolExecutionOutcome(
                status=status,
                result_summary=str(exc),
                result_ref=None,
                error_code=exc.code,
                retryable=False,
                metadata={
                    "operation_id": str(operation_id),
                    "operation": (
                        "write"
                        if tool_name == GROUP_WRITE_WORKSPACE_FILE
                        else "delete"
                    ),
                },
            )
        except Exception as exc:
            raise GroupWorkspaceReconciliationPending(
                "Group workspace reconciliation could not read durable facts"
            ) from exc
        return _workspace_operation_outcome(receipt)

    async def execute(
        self,
        state: RuntimeGraphState,
        context: RuntimeContext,
        agent: Agent,
        tool_name: str,
        arguments: dict,
        *,
        operation_id: uuid.UUID | None = None,
        lease_owner: str | None = None,
    ) -> ToolExecutionOutcome:
        if tool_name not in GROUP_TOOL_NAMES:
            raise GroupRuntimeToolError(
                "group_tool_unknown",
                f"Unknown group tool: {tool_name}",
            )
        tenant_id, group_id, participant_id, session_id = _scope(
            state,
            context,
            agent,
        )
        if tool_name in GROUP_WORKSPACE_MUTATION_TOOL_NAMES:
            if operation_id is None:
                raise GroupRuntimeToolError(
                    "group_workspace_operation_id_missing",
                    "Group workspace mutations require a Tool Ledger operation ID",
                )
            if lease_owner is None or not lease_owner.strip():
                raise GroupRuntimeToolError(
                    "group_workspace_fence_missing",
                    "Group workspace mutations require a durable fence owner",
                )
            return await self._execute_workspace_operation(
                tenant_id=tenant_id,
                group_id=group_id,
                participant_id=participant_id,
                session_id=session_id,
                tool_name=tool_name,
                arguments=arguments,
                operation_id=operation_id,
                lease_owner=lease_owner,
            )
        async with self._session_factory() as db:
            async with db.begin():
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
                    offset, max_bytes = _read_window(arguments)
                    value = _file_json(
                        await group_file_service.read_announcement(
                            db,
                            tenant_id=tenant_id,
                            group_id=group_id,
                            actor_participant_id=participant_id,
                        ),
                        offset=offset,
                        max_bytes=max_bytes,
                    )
                elif tool_name == GROUP_READ_MEMORY:
                    offset, max_bytes = _read_window(arguments)
                    value = _file_json(
                        await group_file_service.read_agent_memory(
                            db,
                            tenant_id=tenant_id,
                            group_id=group_id,
                            actor_participant_id=participant_id,
                            agent_id=_uuid_argument(arguments, "agent_id"),
                        ),
                        offset=offset,
                        max_bytes=max_bytes,
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
                        ),
                        include_content=False,
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
                    offset, max_bytes = _read_window(arguments)
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
                        ),
                        offset=offset,
                        max_bytes=max_bytes,
                    )
                else:
                    raise GroupRuntimeToolError(
                        "group_tool_unknown",
                        f"Unknown group tool: {tool_name}",
                    )
        return ToolExecutionOutcome(
            status="succeeded",
            result_summary=json.dumps(
                value,
                ensure_ascii=False,
                allow_nan=False,
                sort_keys=True,
            ),
            result_ref=None,
        )


__all__ = [
    "GROUP_READ_TOOL_NAMES",
    "GROUP_RUNTIME_TOOL_DEFINITIONS",
    "GROUP_TOOL_NAMES",
    "GROUP_WORKSPACE_MUTATION_TOOL_NAMES",
    "GROUP_WRITE_TOOL_NAMES",
    "GroupRuntimeToolError",
    "GroupRuntimeToolService",
    "GroupWorkspaceReconciliationPending",
    "with_group_runtime_tools",
]
