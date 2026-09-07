"""Workspace path and complete-operation bounds."""

from pathlib import PurePosixPath

from app.infrastructure.errors import AccessDenied, DomainError, InvalidInput

MAX_FILE_BYTES = 4 * 1024 * 1024
MAX_PAGE_SIZE = 100
MAX_INDEX_BYTES = 8192
MAX_PACKAGE_BYTES = 16 * 1024 * 1024
MAX_PACKAGE_MEMBERS = 128


class WorkspaceUnavailable(DomainError):
    code = "workspace_unavailable"


class FileMutationUncertain(DomainError):
    code = "file_mutation_uncertain"

    def __init__(self, path: str) -> None:
        super().__init__("file mutation outcome is uncertain; inspect the current file before retrying")
        self.path = path


def relative_path(value: str) -> str:
    if not value or len(value.encode()) > 512 or "\\" in value or "\x00" in value:
        raise InvalidInput("invalid relative path")
    parts = value.split("/")
    if any(part in {"", ".", ".."} for part in parts) or PurePosixPath(value).is_absolute():
        raise InvalidInput("invalid relative path")
    return value


def ordinary_path(value: str, *, directory: bool = False) -> str:
    path = relative_path(value)
    if path == "memory/MEMORY.md" or path.startswith("files/"):
        return path
    if directory and path in {"memory", "files"}:
        return path
    raise AccessDenied("only ordinary files and memory/MEMORY.md are available here")


def page_limit(limit: int) -> None:
    if isinstance(limit, bool) or not 1 <= limit <= MAX_PAGE_SIZE:
        raise InvalidInput("page limit must be between 1 and 100")
