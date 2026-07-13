"""Database-backed Agent delegation cycle guard.

Only delegated Run edges count.  The guard rebuilds the current parent chain
for every check so worker restarts and multi-process execution cannot reset or
split the counter.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent_run import AgentRun


MAX_AGENT_CYCLE_COUNT = 5
MAX_AGENT_ANCESTOR_DEPTH = 256

AgentEdge = tuple[uuid.UUID, uuid.UUID]


class AgentCycleGuardError(RuntimeError):
    """A candidate delegation is unsafe to create."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class AgentEdgeCount:
    """One directed Agent edge and its occurrences in the candidate chain."""

    source_agent_id: uuid.UUID
    target_agent_id: uuid.UUID
    count: int


@dataclass(frozen=True, slots=True)
class AgentCycleCheck:
    """Successful cycle calculation for an allowed candidate delegation."""

    cycle_count: int
    ancestor_depth: int
    edge_counts: tuple[AgentEdgeCount, ...]


@dataclass(frozen=True, slots=True)
class _ChainRun:
    """The only AgentRun fields the guard is allowed to read."""

    id: uuid.UUID
    tenant_id: uuid.UUID
    run_kind: str
    agent_id: uuid.UUID | None
    origin_agent_id: uuid.UUID | None
    parent_run_id: uuid.UUID | None
    root_run_id: uuid.UUID | None
    system_role: str | None


def count_agent_cycles(edges: Iterable[AgentEdge]) -> int:
    """Count repeats across directed edges; the first occurrence is free."""
    counts = Counter(edges)
    return sum(max(edge_count - 1, 0) for edge_count in counts.values())


def _chain_run_statement(tenant_id: uuid.UUID, run_id: uuid.UUID):
    return select(
        AgentRun.id,
        AgentRun.tenant_id,
        AgentRun.run_kind,
        AgentRun.agent_id,
        AgentRun.origin_agent_id,
        AgentRun.parent_run_id,
        AgentRun.root_run_id,
        AgentRun.system_role,
    ).where(
        AgentRun.tenant_id == tenant_id,
        AgentRun.id == run_id,
    )


def _invalid_chain(message: str) -> AgentCycleGuardError:
    return AgentCycleGuardError("agent_cycle_chain_invalid", message)


def _validate_chain_run(run: _ChainRun) -> AgentEdge | None:
    if run.run_kind == "delegated":
        if run.origin_agent_id is None or run.agent_id is None:
            raise _invalid_chain(f"delegated ancestor {run.id} is missing its Agent edge identity")
        return run.origin_agent_id, run.agent_id

    if run.run_kind == "orchestration":
        if run.agent_id is not None or run.system_role != "group_planning":
            raise _invalid_chain(f"orchestration ancestor {run.id} has an invalid Planning identity")
        return None

    if run.run_kind not in {"foreground", "background"}:
        raise _invalid_chain(f"ancestor {run.id} has unsupported run_kind {run.run_kind!r}")
    if run.agent_id is None:
        raise _invalid_chain(f"ancestor {run.id} is missing its Agent identity")
    return None


class AgentCycleGuard:
    """Reject delegated candidates that reach the configured repeat limit."""

    def __init__(
        self,
        *,
        max_cycle_count: int = MAX_AGENT_CYCLE_COUNT,
        max_ancestor_depth: int = MAX_AGENT_ANCESTOR_DEPTH,
    ) -> None:
        if max_cycle_count <= 0:
            raise ValueError("max_cycle_count must be greater than zero")
        if max_ancestor_depth <= 0:
            raise ValueError("max_ancestor_depth must be greater than zero")
        self.max_cycle_count = max_cycle_count
        self.max_ancestor_depth = max_ancestor_depth

    async def _load_chain_run(
        self,
        db: AsyncSession,
        *,
        tenant_id: uuid.UUID,
        run_id: uuid.UUID,
    ) -> _ChainRun:
        result = await db.execute(_chain_run_statement(tenant_id, run_id))
        row = result.one_or_none()
        if row is None:
            raise _invalid_chain(f"ancestor Run {run_id} is missing or outside tenant {tenant_id}")
        return _ChainRun(
            id=row.id,
            tenant_id=row.tenant_id,
            run_kind=row.run_kind,
            agent_id=row.agent_id,
            origin_agent_id=row.origin_agent_id,
            parent_run_id=row.parent_run_id,
            root_run_id=row.root_run_id,
            system_role=row.system_role,
        )

    async def _load_ancestor_edges(
        self,
        db: AsyncSession,
        *,
        tenant_id: uuid.UUID,
        source_run_id: uuid.UUID,
        source_agent_id: uuid.UUID,
    ) -> tuple[list[AgentEdge], int]:
        edges: list[AgentEdge] = []
        visited: set[uuid.UUID] = set()
        current_run_id: uuid.UUID | None = source_run_id
        depth = 0
        is_source = True
        expected_parent_agent_id: uuid.UUID | None = None

        while current_run_id is not None:
            if current_run_id in visited:
                raise _invalid_chain(f"parent cycle detected at ancestor Run {current_run_id}")
            if depth >= self.max_ancestor_depth:
                raise _invalid_chain("Agent delegation ancestor chain exceeds the configured depth limit")

            visited.add(current_run_id)
            run = await self._load_chain_run(
                db,
                tenant_id=tenant_id,
                run_id=current_run_id,
            )
            if run.tenant_id != tenant_id:
                raise _invalid_chain(f"ancestor Run {run.id} crossed the requested tenant boundary")
            if expected_parent_agent_id is not None and run.agent_id != expected_parent_agent_id:
                raise _invalid_chain(f"ancestor Run {run.id} does not match its delegated child origin")
            if is_source and run.agent_id != source_agent_id:
                raise _invalid_chain("candidate source_agent_id does not match the source Run")

            edge = _validate_chain_run(run)
            if edge is not None:
                edges.append(edge)
                expected_parent_agent_id = edge[0]
            else:
                expected_parent_agent_id = None

            current_run_id = run.parent_run_id
            depth += 1
            is_source = False

        return edges, depth

    async def ensure_delegation_allowed(
        self,
        db: AsyncSession,
        *,
        tenant_id: uuid.UUID,
        source_run_id: uuid.UUID,
        source_agent_id: uuid.UUID | None,
        target_agent_id: uuid.UUID | None,
    ) -> AgentCycleCheck:
        """Rebuild the chain, add the candidate edge, and fail before insert."""
        if source_agent_id is None or target_agent_id is None:
            raise _invalid_chain("candidate delegation is missing an Agent identity")

        ancestor_edges, ancestor_depth = await self._load_ancestor_edges(
            db,
            tenant_id=tenant_id,
            source_run_id=source_run_id,
            source_agent_id=source_agent_id,
        )
        candidate_edges = [*ancestor_edges, (source_agent_id, target_agent_id)]
        edge_counter = Counter(candidate_edges)
        cycle_count = count_agent_cycles(candidate_edges)
        if cycle_count >= self.max_cycle_count:
            raise AgentCycleGuardError(
                "agent_cycle_limit_reached",
                f"candidate delegation reaches the Agent cycle limit ({cycle_count} >= {self.max_cycle_count})",
            )

        edge_counts = tuple(
            AgentEdgeCount(
                source_agent_id=source,
                target_agent_id=target,
                count=count,
            )
            for (source, target), count in sorted(
                edge_counter.items(),
                key=lambda item: (item[0][0].int, item[0][1].int),
            )
        )
        return AgentCycleCheck(
            cycle_count=cycle_count,
            ancestor_depth=ancestor_depth,
            edge_counts=edge_counts,
        )


__all__ = [
    "AgentCycleCheck",
    "AgentCycleGuard",
    "AgentCycleGuardError",
    "AgentEdgeCount",
    "MAX_AGENT_ANCESTOR_DEPTH",
    "MAX_AGENT_CYCLE_COUNT",
    "count_agent_cycles",
]
