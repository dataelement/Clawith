"""Run-scoped materialized workspace lifecycle for local sandboxes."""

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


class RunWorkspace(Protocol):
    """Minimum interface required for a run-scoped materialized workspace."""

    root: Path

    def cleanup(self) -> None: ...


@dataclass(frozen=True)
class RunWorkspaceIdentity:
    """Configuration that must remain stable throughout one Agent loop."""

    agent_id: str
    tenant_id: str | None
    session_id: str | None
    workspace_mode: str
    materialized_paths: tuple[str, ...]
    publish_paths: tuple[str, ...]


@dataclass
class _RunWorkspaceState:
    identity: RunWorkspaceIdentity
    workspace: RunWorkspace
    lock: asyncio.Lock


_run_workspace_tasks: dict[str, asyncio.Task[_RunWorkspaceState]] = {}


async def _create_state(
    identity: RunWorkspaceIdentity,
    factory: Callable[[], Awaitable[RunWorkspace]],
) -> _RunWorkspaceState:
    return _RunWorkspaceState(
        identity=identity,
        workspace=await factory(),
        lock=asyncio.Lock(),
    )


async def _get_or_create_state(
    run_id: str,
    identity: RunWorkspaceIdentity,
    factory: Callable[[], Awaitable[RunWorkspace]],
) -> _RunWorkspaceState:
    task = _run_workspace_tasks.get(run_id)
    if task is None:
        task = asyncio.create_task(_create_state(identity, factory))
        _run_workspace_tasks[run_id] = task
    try:
        state = await asyncio.shield(task)
    except BaseException:
        if task.done() and _run_workspace_tasks.get(run_id) is task:
            _run_workspace_tasks.pop(run_id, None)
        raise
    if state.identity != identity:
        raise RuntimeError("Agent-loop sandbox workspace identity changed")
    return state


@asynccontextmanager
async def use_run_workspace(
    *,
    run_id: str | None,
    identity: RunWorkspaceIdentity,
    factory: Callable[[], Awaitable[RunWorkspace]],
) -> AsyncIterator[RunWorkspace]:
    """Materialize once per Run, while preserving one-shot legacy behavior."""
    if not run_id:
        workspace = await factory()
        try:
            yield workspace
        finally:
            workspace.cleanup()
        return

    state = await _get_or_create_state(run_id, identity, factory)
    async with state.lock:
        yield state.workspace


async def close_run_workspace(run_id: str) -> None:
    """Discard the materialized workspace owned by one settled Agent loop."""
    task = _run_workspace_tasks.pop(run_id, None)
    if task is None:
        return
    try:
        state = await asyncio.shield(task)
    except (asyncio.CancelledError, Exception):
        return
    async with state.lock:
        state.workspace.cleanup()
