"""Redis-backed WebSocket presence and cross-instance message routing."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable, Collection
from typing import Literal
import uuid

from fastapi import WebSocket
from loguru import logger

from app.config import get_settings
from app.core.events import get_redis

settings = get_settings()

PRESENCE_TTL_SECONDS = 180
PUBSUB_PREFIX = "realtime:ws"
SUBSCRIBER_RETRY_INITIAL_SECONDS = 1.0
SUBSCRIBER_RETRY_MAX_SECONDS = 30.0

ScopeType = Literal["agent", "group"]
ScopedDelivery = Callable[..., Awaitable[None]]


def _scope_type(value: str) -> ScopeType:
    if value not in {"agent", "group"}:
        raise ValueError(f"Unsupported realtime scope type: {value}")
    return value


class RealtimeRouter:
    """Route best-effort socket events within one logical Agent or group scope.

    Agent-specific methods remain as compatibility wrappers. New code should use
    the ``*_scope_*`` methods so a group UUID can never collide semantically with
    an Agent UUID.
    """

    def __init__(self, *, instance_id: str | None = None) -> None:
        self.instance_id = instance_id or settings.INSTANCE_ID
        self.refresh_interval_seconds = max(1, PRESENCE_TTL_SECONDS // 3)
        self._subscriber_task: asyncio.Task | None = None
        self._deliver_local: ScopedDelivery | None = None
        self._deliver_local_supports_scopes = False
        self._subscriber_retry_initial_seconds = SUBSCRIBER_RETRY_INITIAL_SECONDS
        self._subscriber_retry_max_seconds = SUBSCRIBER_RETRY_MAX_SECONDS
        self._started = False

    def _connection_key(self, connection_id: str) -> str:
        return f"{PUBSUB_PREFIX}:conn:{connection_id}"

    def _scope_index_key(self, scope_type: ScopeType, scope_id: str) -> str:
        return f"{PUBSUB_PREFIX}:{scope_type}:{scope_id}"

    def _agent_index_key(self, agent_id: str) -> str:
        """Compatibility key retained for rolling deployments and old callers."""
        return self._scope_index_key("agent", agent_id)

    def _instance_channel(self, instance_id: str | None = None) -> str:
        return f"{PUBSUB_PREFIX}:instance:{instance_id or self.instance_id}"

    async def register_scope_connection(
        self,
        *,
        scope_type: ScopeType,
        scope_id: str,
        websocket: WebSocket,
        session_id: str | None = None,
        user_id: str | None = None,
        participant_id: str | None = None,
        tenant_id: str | None = None,
    ) -> str:
        resolved_scope_type = _scope_type(scope_type)
        connection_id = uuid.uuid4().hex
        payload = {
            "scope_type": resolved_scope_type,
            "scope_id": scope_id,
            "session_id": session_id or "",
            "user_id": user_id or "",
            "participant_id": participant_id or "",
            "tenant_id": tenant_id or "",
            "instance_id": self.instance_id,
        }
        if resolved_scope_type == "agent":
            payload["agent_id"] = scope_id
        else:
            payload["group_id"] = scope_id

        redis = await get_redis()
        index_key = self._scope_index_key(resolved_scope_type, scope_id)
        async with redis.pipeline(transaction=True) as pipe:
            pipe.sadd(index_key, connection_id)
            pipe.hset(self._connection_key(connection_id), mapping=payload)
            pipe.expire(self._connection_key(connection_id), PRESENCE_TTL_SECONDS)
            pipe.expire(index_key, PRESENCE_TTL_SECONDS)
            await pipe.execute()

        setattr(websocket.state, "realtime_connection_id", connection_id)
        setattr(websocket.state, "realtime_presence", payload)
        return connection_id

    async def refresh_scope_connection(
        self,
        *,
        scope_type: ScopeType,
        scope_id: str,
        websocket: WebSocket,
    ) -> bool:
        """Refresh one presence lease, recreating it after Redis eviction/restart."""
        resolved_scope_type = _scope_type(scope_type)
        connection_id = getattr(websocket.state, "realtime_connection_id", None)
        payload = getattr(websocket.state, "realtime_presence", None)
        if not connection_id or not isinstance(payload, dict):
            return False
        if payload.get("scope_type") != resolved_scope_type or payload.get("scope_id") != scope_id:
            return False

        redis = await get_redis()
        index_key = self._scope_index_key(resolved_scope_type, scope_id)
        async with redis.pipeline(transaction=True) as pipe:
            pipe.sadd(index_key, connection_id)
            pipe.hset(self._connection_key(connection_id), mapping=payload)
            pipe.expire(self._connection_key(connection_id), PRESENCE_TTL_SECONDS)
            pipe.expire(index_key, PRESENCE_TTL_SECONDS)
            await pipe.execute()
        return True

    async def unregister_scope_connection(
        self,
        *,
        scope_type: ScopeType,
        scope_id: str,
        websocket: WebSocket,
    ) -> None:
        resolved_scope_type = _scope_type(scope_type)
        connection_id = getattr(websocket.state, "realtime_connection_id", None)
        if not connection_id:
            return
        redis = await get_redis()
        async with redis.pipeline(transaction=True) as pipe:
            pipe.srem(self._scope_index_key(resolved_scope_type, scope_id), connection_id)
            pipe.delete(self._connection_key(connection_id))
            await pipe.execute()
        setattr(websocket.state, "realtime_connection_id", None)
        setattr(websocket.state, "realtime_presence", None)

    @staticmethod
    def _matches_filters(
        *,
        record_session_id: str | None,
        record_user_id: str | None,
        record_participant_id: str | None,
        record_tenant_id: str | None,
        session_id: str | None,
        user_id: str | None,
        participant_id: str | None,
        tenant_id: str | None,
    ) -> bool:
        return not (
            (session_id is not None and record_session_id != session_id)
            or (user_id is not None and record_user_id != user_id)
            or (participant_id is not None and record_participant_id != participant_id)
            or (tenant_id is not None and record_tenant_id != tenant_id)
        )

    async def route_scope_message(
        self,
        *,
        scope_type: ScopeType,
        scope_id: str,
        message: dict,
        local_connections: list[tuple] | None = None,
        session_id: str | None = None,
        user_id: str | None = None,
        participant_id: str | None = None,
        tenant_id: str | None = None,
        participant_allowlist: Collection[str] | None = None,
    ) -> None:
        """Send locally, then publish once per remote process with matching presence."""
        resolved_scope_type = _scope_type(scope_type)
        active_participants = (
            frozenset(participant_allowlist)
            if resolved_scope_type == "group" and participant_allowlist is not None
            else None
        )
        local_sent = 0
        if local_connections is None:
            local_sent = int(
                await self._deliver_registered_local(
                    scope_type=resolved_scope_type,
                    scope_id=scope_id,
                    payload=message,
                    session_id=session_id,
                    user_id=user_id,
                    participant_id=participant_id,
                    tenant_id=tenant_id,
                    participant_allowlist=active_participants,
                )
            )
        else:
            for connection in list(local_connections):
                if len(connection) < 3:
                    continue
                websocket = connection[0]
                local_session_id = connection[1]
                local_user_id = connection[2]
                local_participant_id = connection[3] if len(connection) > 3 else None
                local_tenant_id = connection[4] if len(connection) > 4 else None
                if (
                    active_participants is not None
                    and local_participant_id not in active_participants
                ):
                    continue
                if not self._matches_filters(
                    record_session_id=local_session_id,
                    record_user_id=local_user_id,
                    record_participant_id=local_participant_id,
                    record_tenant_id=local_tenant_id,
                    session_id=session_id,
                    user_id=user_id,
                    participant_id=participant_id,
                    tenant_id=tenant_id,
                ):
                    continue
                try:
                    await websocket.send_json(message)
                    local_sent += 1
                except Exception:
                    logger.debug(
                        "[Realtime] Local socket delivery failed scope={}:{}",
                        resolved_scope_type,
                        scope_id,
                    )

        remote_targets: set[str] = set()
        for record in await self._list_scope_presence(resolved_scope_type, scope_id):
            if record.get("instance_id") == self.instance_id:
                continue
            if (
                active_participants is not None
                and record.get("participant_id") not in active_participants
            ):
                continue
            if not self._matches_filters(
                record_session_id=record.get("session_id"),
                record_user_id=record.get("user_id"),
                record_participant_id=record.get("participant_id"),
                record_tenant_id=record.get("tenant_id"),
                session_id=session_id,
                user_id=user_id,
                participant_id=participant_id,
                tenant_id=tenant_id,
            ):
                continue
            target_instance = record.get("instance_id")
            if target_instance:
                remote_targets.add(target_instance)

        if not remote_targets:
            return

        redis = await get_redis()
        envelope = json.dumps(
            {
                "scope_type": resolved_scope_type,
                "scope_id": scope_id,
                "message": message,
                "session_id": session_id,
                "user_id": user_id,
                "participant_id": participant_id,
                "tenant_id": tenant_id,
                "origin_instance_id": self.instance_id,
                **(
                    {"participant_allowlist": sorted(active_participants)}
                    if active_participants is not None
                    else {}
                ),
                **({"agent_id": scope_id} if resolved_scope_type == "agent" else {}),
            }
        )
        target_instances = sorted(remote_targets)
        results = await asyncio.gather(
            *(redis.publish(self._instance_channel(instance_id), envelope) for instance_id in target_instances),
            return_exceptions=True,
        )
        for instance_id, result in zip(target_instances, results, strict=True):
            if isinstance(result, Exception):
                logger.warning(
                    "[Realtime] Pubsub delivery failed scope={}:{} target={}: {}",
                    resolved_scope_type,
                    scope_id,
                    instance_id,
                    result,
                )
        logger.debug(
            "[Realtime] Routed scope={}:{} local={} remote_instances={}",
            resolved_scope_type,
            scope_id,
            local_sent,
            target_instances,
        )

    async def start(self, deliver_local: ScopedDelivery, *, supports_scopes: bool = False) -> None:
        """Start the per-instance subscriber.

        ``supports_scopes=False`` preserves the original Agent-only callback
        contract. The application startup uses the scoped callback so the same
        subscriber can dispatch both Agent and group envelopes.
        """
        if self._subscriber_task is not None and not self._subscriber_task.done():
            return
        self._started = True
        self._deliver_local = deliver_local
        self._deliver_local_supports_scopes = supports_scopes
        task = asyncio.create_task(self._subscriber_loop(), name="realtime-subscriber")
        self._subscriber_task = task
        task.add_done_callback(self._subscriber_finished)

    def _subscriber_finished(self, task: asyncio.Task) -> None:
        """Clear liveness immediately if the supervised subscriber ever exits."""
        if self._subscriber_task is task:
            self._subscriber_task = None
            self._started = False
        if task.cancelled():
            return
        exception = task.exception()
        if exception is not None:
            logger.opt(exception=exception).error(
                "[Realtime] Subscriber stopped unexpectedly: {}",
                exception,
            )
        else:
            logger.error("[Realtime] Subscriber stopped unexpectedly without an error")

    async def stop(self) -> None:
        task = self._subscriber_task
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            if self._subscriber_task is task:
                self._subscriber_task = None
        self._deliver_local = None
        self._deliver_local_supports_scopes = False
        self._started = False

    async def _deliver_registered_local(
        self,
        *,
        scope_type: ScopeType,
        scope_id: str,
        payload: dict,
        session_id: str | None,
        user_id: str | None,
        participant_id: str | None,
        tenant_id: str | None,
        participant_allowlist: Collection[str] | None,
    ) -> bool:
        if self._deliver_local is None:
            return False
        if self._deliver_local_supports_scopes:
            await self._deliver_local(
                scope_type=scope_type,
                scope_id=scope_id,
                payload=payload,
                session_id=session_id,
                user_id=user_id,
                participant_id=participant_id,
                tenant_id=tenant_id,
                participant_allowlist=participant_allowlist,
            )
            return True
        if scope_type == "agent":
            await self._deliver_local(
                agent_id=scope_id,
                payload=payload,
                session_id=session_id,
                user_id=user_id,
            )
            return True
        return False

    async def _subscriber_loop(self) -> None:
        retry_delay = self._subscriber_retry_initial_seconds
        while True:
            pubsub = None
            try:
                redis = await get_redis()
                pubsub = redis.pubsub()
                await pubsub.subscribe(self._instance_channel())
                logger.info(
                    "[Realtime] Subscriber connected instance={}",
                    self.instance_id,
                )
                while True:
                    message = await pubsub.get_message(
                        ignore_subscribe_messages=True,
                        timeout=1.0,
                    )
                    # A successful read proves the connection is healthy again.
                    retry_delay = self._subscriber_retry_initial_seconds
                    if not message:
                        await asyncio.sleep(0.05)
                        continue
                    try:
                        data = json.loads(message["data"])
                        scope_type = _scope_type(data.get("scope_type") or "agent")
                        scope_id = data.get("scope_id") or data.get("agent_id")
                        if not isinstance(scope_id, str) or not scope_id:
                            raise ValueError("Realtime envelope has no scope_id")
                        payload = data.get("message")
                        if not isinstance(payload, dict):
                            raise ValueError("Realtime envelope has no object message")
                        participant_allowlist = data.get("participant_allowlist")
                        if participant_allowlist is not None and not (
                            isinstance(participant_allowlist, list)
                            and all(
                                isinstance(participant, str)
                                for participant in participant_allowlist
                            )
                        ):
                            raise ValueError(
                                "Realtime envelope has an invalid participant_allowlist"
                            )
                        await self._deliver_registered_local(
                            scope_type=scope_type,
                            scope_id=scope_id,
                            payload=payload,
                            session_id=data.get("session_id"),
                            user_id=data.get("user_id"),
                            participant_id=data.get("participant_id"),
                            tenant_id=data.get("tenant_id"),
                            participant_allowlist=participant_allowlist,
                        )
                    except Exception as exc:
                        logger.warning(f"[Realtime] Failed to deliver pubsub message: {exc}")
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning(
                    "[Realtime] Subscriber connection failed instance={} retry_in={:.1f}s: {}",
                    self.instance_id,
                    retry_delay,
                    exc,
                )
            finally:
                if pubsub is not None:
                    try:
                        # Closing the PubSub disconnects and drops all subscriptions
                        # without a network round trip, so stop remains promptly cancellable.
                        await pubsub.aclose()
                    except Exception as exc:
                        logger.debug(
                            "[Realtime] Subscriber cleanup failed instance={}: {}",
                            self.instance_id,
                            exc,
                        )

            await asyncio.sleep(retry_delay)
            retry_delay = min(
                retry_delay * 2,
                self._subscriber_retry_max_seconds,
            )

    async def _list_scope_presence(
        self,
        scope_type: ScopeType,
        scope_id: str,
    ) -> list[dict[str, str]]:
        resolved_scope_type = _scope_type(scope_type)
        redis = await get_redis()
        index_key = self._scope_index_key(resolved_scope_type, scope_id)
        connection_ids = await redis.smembers(index_key)
        if not connection_ids:
            return []
        records: list[dict[str, str]] = []
        stale_ids: list[str] = []
        for connection_id in connection_ids:
            data = await redis.hgetall(self._connection_key(connection_id))
            if not data:
                stale_ids.append(connection_id)
                continue
            record_scope_type = data.get("scope_type")
            record_scope_id = data.get("scope_id")
            if record_scope_type is None and resolved_scope_type == "agent":
                record_scope_type = "agent"
                record_scope_id = data.get("agent_id")
            if record_scope_type != resolved_scope_type or record_scope_id != scope_id:
                stale_ids.append(connection_id)
                continue
            records.append(data)
        if stale_ids:
            await redis.srem(index_key, *stale_ids)
        return records

    # Agent compatibility surface -------------------------------------------------

    async def register_connection(
        self,
        *,
        agent_id: str,
        websocket: WebSocket,
        session_id: str | None,
        user_id: str | None,
    ) -> str:
        return await self.register_scope_connection(
            scope_type="agent",
            scope_id=agent_id,
            websocket=websocket,
            session_id=session_id,
            user_id=user_id,
        )

    async def refresh_connection(self, *, agent_id: str, websocket: WebSocket) -> bool:
        return await self.refresh_scope_connection(
            scope_type="agent",
            scope_id=agent_id,
            websocket=websocket,
        )

    async def unregister_connection(self, *, agent_id: str, websocket: WebSocket) -> None:
        await self.unregister_scope_connection(
            scope_type="agent",
            scope_id=agent_id,
            websocket=websocket,
        )

    async def route_message(
        self,
        *,
        agent_id: str,
        message: dict,
        local_connections: list[tuple],
        session_id: str | None = None,
        user_id: str | None = None,
    ) -> None:
        await self.route_scope_message(
            scope_type="agent",
            scope_id=agent_id,
            message=message,
            local_connections=local_connections,
            session_id=session_id,
            user_id=user_id,
        )

    async def is_user_viewing_session(self, *, agent_id: str, session_id: str, user_id: str) -> bool:
        for record in await self._list_scope_presence("agent", agent_id):
            if record.get("session_id") == session_id and record.get("user_id") == user_id:
                return True
        return False

    async def get_active_session_ids(self, agent_id: str) -> list[str]:
        seen: set[str] = set()
        for record in await self._list_scope_presence("agent", agent_id):
            session_id = (record.get("session_id") or "").strip()
            if session_id:
                seen.add(session_id)
        return list(seen)

    async def _list_presence(self, agent_id: str) -> list[dict[str, str]]:
        return await self._list_scope_presence("agent", agent_id)


realtime_router = RealtimeRouter()
