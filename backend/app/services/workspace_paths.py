from dataclasses import dataclass
from pathlib import Path


class WorkspacePathError(ValueError):
    """Raised when a workspace-relative path escapes its allowed root."""


@dataclass(frozen=True)
class ResolvedWorkspacePath:
    path: Path
    relative_root: Path
    is_enterprise: bool = False


def enterprise_info_root(workspace_root: Path, tenant_id: str | None = None) -> Path:
    suffix = f"enterprise_info_{tenant_id}" if tenant_id else "enterprise_info"
    return (workspace_root / suffix).resolve()


def resolve_path_within_root(
    root: Path,
    rel_path: str = "",
    *,
    allow_root: bool = True,
    require_subpath: bool = False,
    label: str = "path",
) -> Path:
    root_resolved = root.resolve()
    normalized = (rel_path or "").strip()

    if require_subpath and not normalized:
        raise WorkspacePathError(f"{label} must point to a file or subdirectory under the allowed root")

    candidate = Path(normalized)
    if candidate.is_absolute():
        raise WorkspacePathError(f"Absolute {label} is not allowed")

    target = (root_resolved / candidate).resolve() if normalized else root_resolved
    try:
        target.relative_to(root_resolved)
    except ValueError as exc:
        raise WorkspacePathError(f"Access denied for this {label}") from exc

    if not allow_root and target == root_resolved:
        raise WorkspacePathError(f"{label} must not resolve to the root directory")

    return target


def resolve_agent_visible_path(
    agent_workspace: Path,
    rel_path: str,
    *,
    workspace_root: Path,
    tenant_id: str | None = None,
    allow_root: bool = True,
    require_subpath_for_enterprise: bool = False,
) -> ResolvedWorkspacePath:
    normalized = (rel_path or "").strip()

    if normalized.startswith("enterprise_info"):
        enterprise_root = enterprise_info_root(workspace_root, tenant_id)
        sub_path = normalized[len("enterprise_info"):].lstrip("/")
        target = resolve_path_within_root(
            enterprise_root,
            sub_path,
            allow_root=allow_root,
            require_subpath=require_subpath_for_enterprise,
            label="enterprise_info path",
        )
        return ResolvedWorkspacePath(
            path=target,
            relative_root=enterprise_root,
            is_enterprise=True,
        )

    target = resolve_path_within_root(
        agent_workspace,
        normalized,
        allow_root=allow_root,
        label="workspace path",
    )
    return ResolvedWorkspacePath(
        path=target,
        relative_root=agent_workspace.resolve(),
        is_enterprise=False,
    )
