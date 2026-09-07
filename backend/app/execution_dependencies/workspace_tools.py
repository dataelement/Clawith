"""Code-owned Workspace Tools assembled with one trusted Run scope."""

from collections.abc import Awaitable, Callable
from dataclasses import asdict
from typing import Literal

from app.infrastructure.errors import AccessDenied, DomainError, InvalidInput, NotFound
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
from app.modules.workspace.public import (
    DirectoryMutationResult,
    FileConflict,
    FileMutationUncertain,
    SkillDiscovery,
    WorkspaceScope,
    WorkspaceService,
    WorkspaceSubject,
)

MAX_TEXT_CHARACTERS = 16000
MAX_PAGE_ITEMS = 32
MAX_EDIT_RESULT_BYTES = 4 * 1024 * 1024


def _definition(
    name: str, description: str, properties: dict[str, object], required: tuple[str, ...]
) -> DefinitionSpec:
    return DefinitionSpec(
        name,
        description,
        canonical_json(
            {"type": "object", "properties": properties, "required": list(required), "additionalProperties": False}
        ),
        f"workspace.{name}.v1",
        "builtin",
    )


_TEXT = {"type": "string", "maxLength": MAX_TEXT_CHARACTERS}
_PATH = {"type": "string", "minLength": 1, "maxLength": 512}
_REVISION = {"type": "string", "minLength": 1, "maxLength": 256}
_EXPECTED = {"type": ["string", "null"], "maxLength": 256}
_LOCATION: dict[str, object] = {"workspace": {"type": "string", "enum": ["current", "agent"]}, "path": _PATH}
_PAGE: dict[str, object] = {
    "limit": {"type": "integer", "minimum": 1, "maximum": MAX_PAGE_ITEMS},
    "cursor": {"type": ["string", "null"], "maxLength": 2048},
}
_SLICE: dict[str, object] = {
    "offset": {"type": "integer", "minimum": 0, "maximum": 4194304},
    "limit": {"type": "integer", "minimum": 1, "maximum": MAX_TEXT_CHARACTERS},
}

WORKSPACE_DEFINITIONS = (
    _definition(
        "read_file",
        "Read a UTF-8 file and its revision. Use offset to continue a truncated result.",
        {**_LOCATION, **_SLICE},
        ("workspace", "path"),
    ),
    _definition(
        "list_files",
        "List one directory page. Continue with the returned cursor.",
        {**_LOCATION, **_PAGE},
        ("workspace", "path"),
    ),
    _definition(
        "find_files",
        "Find names in a directory page; continue with cursor even when a page has no matches.",
        {**_LOCATION, **_PAGE, "query": {"type": "string", "minLength": 1, "maxLength": 256}},
        ("workspace", "path", "query"),
    ),
    _definition(
        "search_files",
        "Search matching lines in one file. Continue with next_line when present.",
        {
            **_LOCATION,
            "query": {"type": "string", "minLength": 1, "maxLength": 256},
            "start_line": {"type": "integer", "minimum": 0, "maximum": 4194304},
            "limit": _PAGE["limit"],
        },
        ("workspace", "path", "query"),
    ),
    _definition(
        "write_file",
        "Write UTF-8 text with the last observed revision; use null only to create an absent file.",
        {**_LOCATION, "content": _TEXT, "expected_revision": _EXPECTED},
        ("workspace", "path", "content", "expected_revision"),
    ),
    _definition(
        "edit_file",
        "Replace an exact nonempty string in the observed file. A changed revision is a conflict.",
        {
            **_LOCATION,
            "old_string": _TEXT,
            "new_string": _TEXT,
            "expected_revision": _REVISION,
            "replace_all": {"type": "boolean"},
        },
        ("workspace", "path", "old_string", "new_string", "expected_revision"),
    ),
    _definition(
        "delete_file",
        "Delete a regular file only at its last observed revision.",
        {**_LOCATION, "expected_revision": _REVISION},
        ("workspace", "path", "expected_revision"),
    ),
    _definition(
        "make_directory", "Create a directory under files/ in the writable workspace.", _LOCATION, ("workspace", "path")
    ),
    _definition(
        "copy_file",
        "Copy an observed file to the current writable workspace; check both revisions.",
        {**_LOCATION, "destination_path": _PATH, "source_revision": _REVISION, "destination_revision": _EXPECTED},
        ("workspace", "path", "destination_path", "source_revision", "destination_revision"),
    ),
    _definition(
        "move_file",
        "Move a regular file within its workspace. Inspect source_deleted before assuming removal.",
        {**_LOCATION, "destination_path": _PATH, "source_revision": _REVISION, "destination_revision": _EXPECTED},
        ("workspace", "path", "destination_path", "source_revision", "destination_revision"),
    ),
    _definition(
        "load_skill",
        "Read a discovered Skill member. Use next_member_offset to continue member names; default member is SKILL.md.",
        {
            "name": {"type": "string", "minLength": 1, "maxLength": 64},
            "member": _PATH,
            **_SLICE,
            "member_offset": {"type": "integer", "minimum": 0, "maximum": 128},
        },
        ("name",),
    ),
    _definition(
        "inspect_directory",
        "Inspect a bounded directory manifest and revision; this is not an atomic tree snapshot.",
        {**_LOCATION, "offset": {"type": "integer", "minimum": 0, "maximum": 128}},
        ("workspace", "path"),
    ),
    _definition(
        "delete_directory",
        "Delete a directory at its observed revision. Partial deletion is reported; inspect remaining files before retrying.",
        {**_LOCATION, "expected_revision": _REVISION},
        ("workspace", "path", "expected_revision"),
    ),
    _definition(
        "move_directory",
        "Move to an absent destination at the observed source revision. This may partially complete; no rollback is promised.",
        {**_LOCATION, "destination_path": _PATH, "expected_revision": _REVISION},
        ("workspace", "path", "destination_path", "expected_revision"),
    ),
    _definition(
        "distill_memory",
        "Explicitly save generalized memory for this Agent. Do not copy private user or group details into shared memory.",
        {"content": _TEXT, "expected_revision": _EXPECTED},
        ("content", "expected_revision"),
    ),
)


def _text(arguments: dict[str, object], name: str, *, maximum: int = MAX_TEXT_CHARACTERS) -> str:
    value = arguments.get(name)
    if not isinstance(value, str) or len(value) > maximum:
        raise InvalidInput(f"{name} must be bounded text")
    return value


def _number(arguments: dict[str, object], name: str, default: int, *, minimum: int = 0, maximum: int) -> int:
    value = arguments.get(name, default)
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise InvalidInput(f"{name} is outside its supported range")
    return value


def _nullable_text(arguments: dict[str, object], name: str, maximum: int) -> str | None:
    value = arguments.get(name)
    if value is None:
        return None
    if not isinstance(value, str) or not value or len(value) > maximum:
        raise InvalidInput(f"{name} must be nonempty text or null")
    return value


def _slice(content: bytes, arguments: dict[str, object]) -> dict[str, object]:
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        raise InvalidInput("This Tool reads UTF-8 text; the selected member is binary") from None
    offset = _number(arguments, "offset", 0, maximum=4194304)
    limit = _number(arguments, "limit", MAX_TEXT_CHARACTERS, minimum=1, maximum=MAX_TEXT_CHARACTERS)
    end = min(len(text), offset + limit)
    return {"content": text[offset:end], "truncated": end < len(text), "next_offset": end if end < len(text) else None}


class _WorkspaceExecutor:
    def __init__(
        self,
        definition: DefinitionSpec,
        scope: WorkspaceScope,
        operation: Callable[[dict[str, object]], Awaitable[dict[str, object]]],
    ) -> None:
        self._definition = definition
        self._scope = scope
        self._operation = operation

    async def execute(self, tool: ResolvedTool, call: ToolCall, scope: CallScope) -> ToolResult:
        try:
            if (scope.tenant_id, scope.agent_id, scope.run_id) != (
                self._scope.tenant_id,
                self._scope.agent_id,
                self._scope.run_id,
            ):
                raise AccessDenied("Workspace Tool belongs to a different Run")
            if (
                tool.definition.tenant_id != scope.tenant_id
                or tool.definition.spec != self._definition
                or call.name != self._definition.name
            ):
                raise AccessDenied("Workspace Tool binding does not match its definition")
            arguments = json_object(call.arguments_json)
            schema = json_object(self._definition.input_schema_json)
            properties, required = schema["properties"], schema["required"]
            if not isinstance(properties, dict) or not isinstance(required, list):
                raise TypeError("Code-owned Workspace schema is invalid")
            if arguments.keys() - properties.keys() or not set(required) <= arguments.keys():
                raise InvalidInput("Tool arguments have missing or unsupported fields")
            payload = await self._operation(arguments)
            status: Literal["success", "error", "uncertain"] = "success"
            if payload.get("source_error") is not None or payload.get("uncertain_path") is not None:
                status = "uncertain"
            elif payload.get("source_deleted") is False or payload.get("completed") is False:
                status = "error"
            return ToolResult(call.id, status, canonical_json(payload, maximum=250000))
        except FileConflict as error:
            return ToolResult(
                call.id,
                "error",
                canonical_json(
                    {
                        "code": error.code,
                        "message": "File changed; read it again and merge before retrying.",
                        "current_revision": error.current_revision,
                    }
                ),
            )
        except FileMutationUncertain:
            return ToolResult(
                call.id, "uncertain", canonical_json({"message": "The file may have changed; read it before retrying."})
            )
        except DomainError as error:
            return ToolResult(call.id, "error", canonical_json({"code": error.code, "message": str(error)[:1024]}))


class _WorkspaceOperations:
    def __init__(self, workspace: WorkspaceService, scope: WorkspaceScope, skills: SkillDiscovery) -> None:
        self.workspace, self.scope, self.skills = workspace, scope, skills

    def subject(self, arguments: dict[str, object]) -> WorkspaceSubject:
        alias = arguments.get("workspace")
        if alias == "current":
            return self.scope.output
        if alias == "agent":
            return WorkspaceSubject("agent", self.scope.agent_id)
        raise InvalidInput("workspace must be current or agent")

    async def read(self, arguments: dict[str, object]) -> dict[str, object]:
        file = await self.workspace.read(self.scope, self.subject(arguments), _text(arguments, "path", maximum=512))
        return {"path": file.path, "revision": file.revision, **_slice(file.content, arguments)}

    async def listing(self, arguments: dict[str, object]) -> dict[str, object]:
        page = await self.workspace.list(
            self.scope,
            self.subject(arguments),
            _text(arguments, "path", maximum=512),
            limit=_number(arguments, "limit", MAX_PAGE_ITEMS, minimum=1, maximum=MAX_PAGE_ITEMS),
            cursor=_nullable_text(arguments, "cursor", 2048),
        )
        return {
            "entries": [
                {"path": entry.key, "is_directory": entry.is_dir, "size": entry.size} for entry in page.entries
            ],
            "cursor": page.cursor,
        }

    async def find(self, arguments: dict[str, object]) -> dict[str, object]:
        page = await self.workspace.search(
            self.scope,
            self.subject(arguments),
            _text(arguments, "path", maximum=512),
            query=_text(arguments, "query", maximum=256),
            limit=_number(arguments, "limit", MAX_PAGE_ITEMS, minimum=1, maximum=MAX_PAGE_ITEMS),
            cursor=_nullable_text(arguments, "cursor", 2048),
        )
        return {
            "entries": [
                {"path": entry.key, "is_directory": entry.is_dir, "size": entry.size} for entry in page.entries
            ],
            "cursor": page.cursor,
        }

    async def search(self, arguments: dict[str, object]) -> dict[str, object]:
        result = await self.workspace.search_content(
            self.scope,
            self.subject(arguments),
            _text(arguments, "path", maximum=512),
            query=_text(arguments, "query", maximum=256),
            start_line=_number(arguments, "start_line", 0, maximum=4194304),
            limit=_number(arguments, "limit", MAX_PAGE_ITEMS, minimum=1, maximum=MAX_PAGE_ITEMS),
        )
        return asdict(result)

    async def write(self, arguments: dict[str, object]) -> dict[str, object]:
        revision = await self.workspace.write(
            self.scope,
            self.subject(arguments),
            _text(arguments, "path", maximum=512),
            _text(arguments, "content").encode(),
            expected_revision=_nullable_text(arguments, "expected_revision", 256),
        )
        return {"revision": revision}

    async def edit(self, arguments: dict[str, object]) -> dict[str, object]:
        subject, path = self.subject(arguments), _text(arguments, "path", maximum=512)
        file = await self.workspace.read(self.scope, subject, path)
        if file.revision != _text(arguments, "expected_revision", maximum=256):
            raise FileConflict(file.revision)
        old, new = _text(arguments, "old_string"), _text(arguments, "new_string")
        replace_all = arguments.get("replace_all", False)
        if not isinstance(replace_all, bool) or not old:
            raise InvalidInput("old_string must be nonempty and replace_all must be a boolean")
        try:
            content = file.content.decode("utf-8")
        except UnicodeDecodeError:
            raise InvalidInput("edit_file requires UTF-8 text") from None
        count = content.count(old)
        if count == 0 or (count > 1 and not replace_all):
            raise InvalidInput("The selected text is absent or ambiguous; read the file and select an exact match")
        result_bytes = len(file.content) + (len(new.encode()) - len(old.encode())) * (count if replace_all else 1)
        if result_bytes > MAX_EDIT_RESULT_BYTES:
            raise InvalidInput("The edited file would exceed 4 MiB")
        revision = await self.workspace.write(
            self.scope,
            subject,
            path,
            content.replace(old, new, -1 if replace_all else 1).encode(),
            expected_revision=file.revision,
        )
        return {"revision": revision, "replacements": count if replace_all else 1}

    async def delete(self, arguments: dict[str, object]) -> dict[str, object]:
        await self.workspace.delete(
            self.scope,
            self.subject(arguments),
            _text(arguments, "path", maximum=512),
            expected_revision=_text(arguments, "expected_revision", maximum=256),
        )
        return {"deleted": True}

    async def mkdir(self, arguments: dict[str, object]) -> dict[str, object]:
        await self.workspace.mkdir(self.scope, self.subject(arguments), _text(arguments, "path", maximum=512))
        return {"created": True}

    async def copy(self, arguments: dict[str, object]) -> dict[str, object]:
        revision = await self.workspace.copy(
            self.scope,
            self.subject(arguments),
            _text(arguments, "path", maximum=512),
            self.scope.output,
            _text(arguments, "destination_path", maximum=512),
            source_revision=_text(arguments, "source_revision", maximum=256),
            destination_revision=_nullable_text(arguments, "destination_revision", 256),
        )
        return {"revision": revision}

    async def move(self, arguments: dict[str, object]) -> dict[str, object]:
        result = await self.workspace.move(
            self.scope,
            self.subject(arguments),
            _text(arguments, "path", maximum=512),
            _text(arguments, "destination_path", maximum=512),
            source_revision=_text(arguments, "source_revision", maximum=256),
            destination_revision=_nullable_text(arguments, "destination_revision", 256),
        )
        return asdict(result)

    async def load_skill(self, arguments: dict[str, object]) -> dict[str, object]:
        skill = await self.workspace.load_skill(self.skills, _text(arguments, "name", maximum=64))
        member = arguments.get("member", "SKILL.md")
        if not isinstance(member, str) or len(member.encode()) > 512:
            raise InvalidInput("Skill member path is invalid")
        content = skill.members.get(member)
        if content is None:
            raise NotFound("The member does not exist in this Skill")
        member_offset = _number(arguments, "member_offset", 0, maximum=128)
        names = sorted(skill.members)
        member_end = min(len(names), member_offset + MAX_PAGE_ITEMS)
        return {
            "name": skill.name,
            "member": member,
            "revision": skill.revision,
            "members": names[member_offset:member_end],
            "members_truncated": member_end < len(names),
            "next_member_offset": member_end if member_end < len(names) else None,
            **_slice(content, arguments),
        }

    async def inspect_directory(self, arguments: dict[str, object]) -> dict[str, object]:
        snapshot = await self.workspace.inspect_directory(
            self.scope, self.subject(arguments), _text(arguments, "path", maximum=512)
        )
        offset = _number(arguments, "offset", 0, maximum=128)
        end = min(len(snapshot.members), offset + MAX_PAGE_ITEMS)
        return {
            "path": snapshot.path,
            "revision": snapshot.revision,
            "members": [asdict(member) for member in snapshot.members[offset:end]],
            "member_count": len(snapshot.members),
            "next_offset": end if end < len(snapshot.members) else None,
        }

    @staticmethod
    def directory_outcome(result: DirectoryMutationResult) -> dict[str, object]:
        payload: dict[str, object] = {
            "completed": result.completed,
            "uncertain_path": result.uncertain_path,
            "reason": result.reason,
        }
        for name, paths in (
            ("copied_paths", result.copied_paths),
            ("deleted_paths", result.deleted_paths),
            ("remaining_paths", result.remaining_paths),
        ):
            payload[name] = list(paths[:16])
            payload[f"{name}_count"] = len(paths)
            payload[f"{name}_truncated"] = len(paths) > 16
        if any(len(paths) > 16 for paths in (result.copied_paths, result.deleted_paths, result.remaining_paths)):
            payload["next_action"] = "Inspect source and destination to see their complete current contents."
        return payload

    async def delete_directory(self, arguments: dict[str, object]) -> dict[str, object]:
        result = await self.workspace.delete_directory(
            self.scope,
            self.subject(arguments),
            _text(arguments, "path", maximum=512),
            expected_revision=_text(arguments, "expected_revision", maximum=256),
        )
        return self.directory_outcome(result)

    async def move_directory(self, arguments: dict[str, object]) -> dict[str, object]:
        result = await self.workspace.move_directory(
            self.scope,
            self.subject(arguments),
            _text(arguments, "path", maximum=512),
            _text(arguments, "destination_path", maximum=512),
            expected_revision=_text(arguments, "expected_revision", maximum=256),
        )
        return self.directory_outcome(result)

    async def distill_memory(self, arguments: dict[str, object]) -> dict[str, object]:
        revision = await self.workspace.distill_memory(
            self.scope,
            _text(arguments, "content").encode(),
            expected_revision=_nullable_text(arguments, "expected_revision", 256),
        )
        return {"revision": revision}


def workspace_bindings(
    workspace: WorkspaceService, *, scope: WorkspaceScope, skills: SkillDiscovery
) -> tuple[ExecutorBinding, ...]:
    """Compose capabilities; grant materialization and Run ownership remain outside."""
    if scope.run_id is None or (skills.tenant_id, skills.agent_id) != (scope.tenant_id, scope.agent_id):
        raise InvalidInput("Workspace bindings require matching trusted Run and Skill discovery")
    operations = _WorkspaceOperations(workspace, scope, skills)
    handlers = (
        operations.read,
        operations.listing,
        operations.find,
        operations.search,
        operations.write,
        operations.edit,
        operations.delete,
        operations.mkdir,
        operations.copy,
        operations.move,
        operations.load_skill,
        operations.inspect_directory,
        operations.delete_directory,
        operations.move_directory,
        operations.distill_memory,
    )
    return tuple(
        ExecutorBinding(definition.executor_key, _WorkspaceExecutor(definition, scope, handler), builtin=definition)
        for definition, handler in zip(WORKSPACE_DEFINITIONS, handlers, strict=True)
        if definition.name != "distill_memory" or (scope.main and not scope.preview_only)
    )
