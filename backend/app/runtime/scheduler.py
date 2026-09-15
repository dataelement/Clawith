"""Run-owned, in-memory Tenant → Agent → Run ready rotation."""

from collections import OrderedDict
from dataclasses import dataclass
from uuid import UUID


@dataclass(frozen=True, slots=True)
class RunKey:
    tenant_id: UUID
    agent_id: UUID
    run_id: UUID


class FairReadyQueue:
    """Synchronous single-event-loop primitive; Runner owns admission, slots and lifecycle.

    Each Run has at most one ready position. Taking a Run removes that position;
    only Runner may re-enqueue it after an eligible quantum settles. Capacity
    failure is explicit and leaves every existing position intact.
    """

    def __init__(self, *, capacity: int) -> None:
        if type(capacity) is not int or capacity <= 0:
            raise ValueError("Ready queue capacity must be a positive integer")
        self._capacity = capacity
        self._tenants: OrderedDict[UUID, OrderedDict[UUID, OrderedDict[UUID, RunKey]]] = OrderedDict()
        self._runs: dict[UUID, RunKey] = {}

    def __len__(self) -> int:
        return len(self._runs)

    def enqueue(self, run: RunKey) -> bool:
        existing = self._runs.get(run.run_id)
        if existing is not None:
            if existing != run:
                raise ValueError("A ready Run cannot change Tenant or Agent ownership")
            return False
        if len(self._runs) >= self._capacity:
            raise OverflowError("Ready queue capacity is exhausted")
        agents = self._tenants.setdefault(run.tenant_id, OrderedDict())
        runs = agents.setdefault(run.agent_id, OrderedDict())
        runs[run.run_id] = run
        self._runs[run.run_id] = run
        return True

    def discard(self, run_id: UUID) -> bool:
        run = self._runs.pop(run_id, None)
        if run is None:
            return False
        agents = self._tenants[run.tenant_id]
        runs = agents[run.agent_id]
        del runs[run_id]
        if not runs:
            del agents[run.agent_id]
        if not agents:
            del self._tenants[run.tenant_id]
        return True

    def take(self) -> RunKey | None:
        if not self._tenants:
            return None
        tenant_id = next(iter(self._tenants))
        agents = self._tenants[tenant_id]
        agent_id = next(iter(agents))
        runs = agents[agent_id]
        run_id, run = runs.popitem(last=False)
        del self._runs[run_id]
        if runs:
            agents.move_to_end(agent_id)
        else:
            del agents[agent_id]
        if agents:
            self._tenants.move_to_end(tenant_id)
        else:
            del self._tenants[tenant_id]
        return run

    def clear(self) -> None:
        self._tenants.clear()
        self._runs.clear()
