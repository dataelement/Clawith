"""Trusted workspace policy for local code execution."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Literal

from app.services.workspace_collaboration import normalize_workspace_path

WorkspaceMode = Literal["merge", "isolated_output"]
PublicationOwner = Literal["gateway", "workspace_cas"]
PublicationConflictMode = Literal["fail", "overwrite"]


@dataclass(frozen=True, slots=True)
class SandboxExecutionScope:
    tenant_id: uuid.UUID
    agent_id: uuid.UUID
    session_id: uuid.UUID


@dataclass(frozen=True, slots=True)
class SandboxWorkspacePolicy:
    mode: WorkspaceMode
    session_id: uuid.UUID | None
    materialized_paths: tuple[str, ...]
    publish_paths: tuple[str, ...]

    @property
    def session_output_path(self) -> str | None:
        if self.session_id is None:
            return None
        return normalize_workspace_path(f"workspace/output/{self.session_id}")

    @property
    def guest_output_path(self) -> str | None:
        relative = self.session_output_path
        if relative is None:
            return None
        workspace_relative = relative.removeprefix("workspace/")
        return f"/workspace/{workspace_relative}"

    @property
    def publication_conflict_mode(self) -> PublicationConflictMode:
        """Return the durable write policy for this workspace mode."""
        return "overwrite" if self.mode == "isolated_output" else "fail"


def parse_canonical_uuid(value: str | uuid.UUID, *, label: str) -> uuid.UUID:
    try:
        parsed = value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))
    except (TypeError, ValueError, AttributeError) as exc:
        raise ValueError(f"{label} must be a canonical UUID") from exc
    if str(parsed) != str(value).lower():
        raise ValueError(f"{label} must be a canonical UUID")
    return parsed


def build_workspace_policy(
    *,
    mode: WorkspaceMode,
    session_id: uuid.UUID | None,
    default_paths: list[str] | tuple[str, ...],
) -> SandboxWorkspacePolicy:
    materialized = tuple(normalize_workspace_path(path) for path in default_paths)
    if mode == "merge":
        return SandboxWorkspacePolicy(mode, session_id, materialized, materialized)
    if mode != "isolated_output":
        raise ValueError("Unsupported sandbox workspace mode")
    if session_id is None:
        raise ValueError("isolated_output requires a Session")
    output_path = normalize_workspace_path(f"workspace/output/{session_id}")
    return SandboxWorkspacePolicy(mode, session_id, materialized, (output_path,))
