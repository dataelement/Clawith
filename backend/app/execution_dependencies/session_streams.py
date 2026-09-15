"""Bounded transient Run display events; Session entries remain the message authority."""

import asyncio
import json
from collections import OrderedDict, deque
from dataclasses import asdict
from uuid import UUID

from app.infrastructure.database import DatabaseResources
from app.infrastructure.errors import AccessDenied, Conflict
from app.infrastructure.transactions import transaction
from app.modules.identity_tenant.public import TenantPrincipal
from app.modules.run.public import RunKey, RunService, RunStreamEvent
from app.modules.session.public import SessionService


class StreamSubscription:
    def __init__(self) -> None:
        self.ready = asyncio.Event()
        self.closed = False
        self._items: deque[tuple[str, int]] = deque()
        self._bytes = 0
        self._attempts: OrderedDict[str, tuple[str, int]] = OrderedDict()

    def push(self, payload: str, *, run_id: str, step_id: str, attempt: int, starts_attempt: bool) -> None:
        if self.closed or (not starts_attempt and self._attempts.get(run_id) != (step_id, attempt)):
            return
        if starts_attempt:
            if run_id not in self._attempts and len(self._attempts) >= 256:
                self.resync()
            self._attempts[run_id] = (step_id, attempt)
            self._attempts.move_to_end(run_id)
        else:
            self._attempts.move_to_end(run_id)
        size = len(payload.encode())
        if len(self._items) >= 64 or self._bytes + size > 256 * 1024:
            self.resync()
            return
        self._items.append((payload, size))
        self._bytes += size
        self.ready.set()

    def resync(self) -> None:
        if self.closed:
            return
        self._items.clear()
        self._attempts.clear()
        payload = '{"type":"execution_resync","reason":"overflow","discard_transient":true}'
        self._bytes = len(payload)
        self._items.append((payload, self._bytes))
        self.ready.set()

    def pop(self) -> str | None:
        if not self._items:
            return None
        payload, size = self._items.popleft()
        self._bytes -= size
        if not self._items:
            self.ready.clear()
        return payload

    def close(self) -> None:
        self.closed = True
        self._items.clear()
        self._bytes = 0
        self._attempts.clear()
        self.ready.set()


class SessionExecutionStreams:
    def __init__(self, database: DatabaseResources) -> None:
        self._database = database
        self._subscriptions: dict[tuple[UUID, UUID], set[StreamSubscription]] = {}
        self._routes: OrderedDict[RunKey, UUID | None] = OrderedDict()
        self.closed_event = asyncio.Event()

    @property
    def subscriptions(self) -> int:
        return sum(len(values) for values in self._subscriptions.values())

    async def subscribe(self, principal: TenantPrincipal, *, session_id: UUID) -> StreamSubscription:
        if self.closed_event.is_set():
            raise Conflict("Session execution stream is closed")
        async with transaction(self._database.control_sessions) as tx:
            await SessionService(tx).get(principal, session_id=session_id)
        if self.closed_event.is_set() or self.subscriptions >= 200:
            raise Conflict("Session execution stream capacity is unavailable")
        subscription = StreamSubscription()
        self._subscriptions.setdefault((principal.tenant_id, session_id), set()).add(subscription)
        return subscription

    def unsubscribe(self, principal: TenantPrincipal, *, session_id: UUID, subscription: StreamSubscription) -> None:
        subscription.close()
        key = (principal.tenant_id, session_id)
        current = self._subscriptions.get(key)
        if current is not None:
            current.discard(subscription)
            if not current:
                del self._subscriptions[key]

    async def observe(self, key: RunKey, event: RunStreamEvent) -> None:
        if self.closed_event.is_set() or not self._subscriptions:
            return
        if key not in self._routes:
            async with transaction(self._database.control_sessions) as tx:
                run = await RunService(tx).get(tenant_id=key.tenant_id, run_id=key.run_id)
                if run.agent_id != key.agent_id:
                    raise AccessDenied("Execution stream Run identity differs")
                session_id = None
                if run.parent_run_id is None and run.source.kind == "session":
                    session_id = (await SessionService(tx).get_execution_context(run)).session.id
            self._routes[key] = session_id
            if len(self._routes) > 256:
                self._routes.popitem(last=False)
        else:
            session_id = self._routes[key]
            self._routes.move_to_end(key)
        if session_id is None:
            return
        recipients = self._subscriptions.get((key.tenant_id, session_id), ())
        if not recipients:
            return
        if event.event is not None and sum(len(value or "") for value in (
                event.step_id, event.event.text, event.event.call_id, event.event.name)) > 65536:
            for subscription in recipients:
                subscription.resync()
            return
        payload = json.dumps({"type": "execution", "run_id": str(key.run_id), **asdict(event)}, ensure_ascii=False, separators=(",", ":"))
        for subscription in recipients:
            subscription.push(payload, run_id=str(key.run_id), step_id=event.step_id, attempt=event.attempt,
                starts_attempt=event.kind == "attempt_started")

    async def close(self) -> None:
        self.closed_event.set()
        for group in self._subscriptions.values():
            for subscription in group:
                subscription.close()
        self._subscriptions.clear()
        self._routes.clear()
