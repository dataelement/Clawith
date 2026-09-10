"""Authenticated Channel intake and source-backed delivery using public owners."""

import asyncio
import json
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from hashlib import sha256
from time import monotonic
from typing import Literal, cast
from uuid import UUID, uuid4

import httpx
from pydantic import SecretStr
from sqlalchemy.exc import SQLAlchemyError

from app.execution_dependencies.product_inputs import ProductInputs
from app.execution_dependencies.resources import ExecutionResources
from app.infrastructure.database import DatabaseResources
from app.infrastructure.errors import AccessDenied, DomainError, InvalidInput, NotFound
from app.infrastructure.transactions import TransactionContext, transaction
from app.modules.agent.public import AgentService
from app.modules.channel.public import (
    ChannelContextCodec,
    ChannelDeliverySource,
    ChannelService,
    ChannelSyncCursors,
    ChannelView,
    DeliveryContent,
    DeliveryService,
    DeliveryView,
    DingTalkAdapter,
    DiscordAdapter,
    FeishuAdapter,
    InboundResult,
    InboundService,
    ListenerDisconnected,
    QRChallenge,
    ResolvedInbound,
    SlackAdapter,
    WebhookReply,
    WeChatAdapter,
    WeComAdapter,
    create_channel_adapters,
)
from app.modules.credential.public import Secret
from app.modules.group.public import AcceptedGroupInput, GroupDeliveryScope, GroupService
from app.modules.identity_tenant.public import IdentityService, TenantPrincipal, require_admin
from app.modules.permission.public import PermissionService
from app.modules.run.public import InputContent, InputReference, RunService, SourceIdentity
from app.modules.session.public import AcceptedInput, SessionDeliveryScope, SessionService

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class _QRLogin:
    tenant_id: UUID
    membership_id: UUID
    agent_id: UUID
    challenge: QRChallenge
    expires: float
    route_tag: SecretStr | None
    base_url: str | None = None
    channel_id: UUID | None = None
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class ChannelInputs:
    """Application closes this worker before its HTTP, Credential and database resources."""

    def __init__(self, database: DatabaseResources, execution: ExecutionResources, products: ProductInputs,
            *, context_codec: ChannelContextCodec) -> None:
        self.database, self.execution, self.products = database, execution, products
        self._context_codec = context_codec
        self.adapters = create_channel_adapters(execution.http)
        self.inbound = InboundService(database.control_sessions, credentials=execution.credentials,
            adapters=self.adapters.adapters, context_codec=context_codec)
        self.delivery = DeliveryService(database.execution_sessions, credentials=execution.credentials,
            adapters=self.adapters.adapters, messages=self.load_message, context_codec=context_codec,
            files=products.attachments.read_for_delivery)
        self._task: asyncio.Task[None] | None = None
        self._closed = False
        self._changed = asyncio.Event()
        self.failures = 0
        self.listener_failures: dict[UUID, str] = {}
        self._listeners: dict[UUID, asyncio.Task[None]] = {}
        self._listener_versions: dict[UUID, tuple[UUID, str]] = {}
        self._supervisor: asyncio.Task[None] | None = None
        self._sync_task: asyncio.Task[None] | None = None
        self._qr: dict[UUID, _QRLogin] = {}
        self._qr_starting = 0

    async def startup(self) -> None:
        if self._task is not None or self._closed:
            raise RuntimeError("Channel intake has one application lifecycle")
        self._task = asyncio.create_task(self._delivery_loop(), name="channel-message-delivery")
        self._supervisor = asyncio.create_task(self._supervise_listeners(), name="channel-listener-supervisor")
        self._sync_task = asyncio.create_task(self._sync_loop(), name="channel-customer-service-sync")

    async def close(self) -> None:
        self._closed = True
        self._changed.set()
        tasks = list(self._listeners.values()) + ([self._supervisor] if self._supervisor is not None else []) + ([self._sync_task] if self._sync_task is not None else [])
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._listeners.clear()
        self._listener_versions.clear()
        self._qr.clear()
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def restart_listener(self, *, tenant_id: UUID, channel_id: UUID) -> None:
        async with transaction(self.database.control_sessions) as tx:
            await ChannelService(tx).get_for_intake(tenant_id=tenant_id, channel_id=channel_id)
        task = self._listeners.pop(channel_id, None)
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        self.listener_failures.pop(channel_id, None)
        self._listener_versions.pop(channel_id, None)

    async def _supervise_listeners(self) -> None:
        while not self._closed:
            try:
                await self._refresh_listeners()
            except (DomainError, SQLAlchemyError) as error:
                self.failures += 1
                logger.warning("Channel listener refresh requires retry: %s", type(error).__name__)
            self._qr = {id: request for id, request in self._qr.items() if request.expires > monotonic()}
            await asyncio.sleep(1)

    async def _refresh_listeners(self) -> None:
        active: dict[UUID, ChannelView] = {}
        after = None
        while True:
            async with transaction(self.database.control_sessions) as tx:
                channels = await ChannelService(tx).enabled_channels(after_id=after)
                tenants = await IdentityService(tx).filter_enabled_tenant_ids(tenant_ids=tuple({channel.tenant_id for channel in channels}))
            for channel in channels:
                mode = json.loads(channel.settings_json).get("connection_mode")
                if channel.tenant_id in tenants and channel.provider in self.adapters.listeners and mode in ("websocket", "gateway", "stream", "long_poll"):
                    if len(active) >= 256:
                        raise InvalidInput("Channel listener capacity is exhausted")
                    active[channel.id] = channel
            if len(channels) < 100:
                break
            after = channels[-1].id
        for id in tuple(self._listeners):
            channel = active.get(id)
            if channel is None or self._listener_versions.get(id) != (channel.credential_id, channel.settings_json):
                task = self._listeners.pop(id)
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                self._listener_versions.pop(id, None)
                self.listener_failures.pop(id, None)
        for id, channel in active.items():
            task = self._listeners.get(id)
            if task is not None and not task.done():
                continue
            if task is not None and not task.cancelled() and task.exception() is not None:
                self.listener_failures[id] = type(task.exception()).__name__
                logger.error("Channel listener stopped unexpectedly: %s", self.listener_failures[id])
            if id in self.listener_failures:
                continue
            self._listener_versions[id] = (channel.credential_id, channel.settings_json)
            self._listeners[id] = asyncio.create_task(self._listen(channel), name=f"channel-listener-{id}")

    async def _listen(self, channel: ChannelView) -> None:
        try:
            async with transaction(self.database.control_sessions) as tx:
                credential = await self.execution.credentials(tx).reveal_secret_for_owner(tenant_id=channel.tenant_id,
                    credential_id=channel.credential_id, owner_kind=channel.credential_owner_kind, owner_id=channel.credential_owner_id)
            async def received(result: InboundResult) -> None:
                try:
                    await self.accept_authenticated(channel, result)
                except DomainError as error:
                    self.failures += 1
                    logger.warning("Authenticated Channel event was rejected: %s", error.code)
            await self.adapters.listeners[channel.provider].listen(channel, credential, received)
        except DomainError as error:
            self.listener_failures[channel.id] = error.code
            logger.warning("Channel listener needs configuration or authentication: %s", error.code)
        except (ListenerDisconnected, httpx.HTTPError, TimeoutError, OSError) as error:
            self.failures += 1
            logger.warning("Channel listener will reconnect: %s", type(error).__name__)

    def _wechat(self) -> WeChatAdapter:
        return next(adapter for adapter in self.adapters.adapters if isinstance(adapter, WeChatAdapter))

    async def receive_customer_service(self, *, tenant_id: UUID, channel_id: UUID, body: bytes,
            headers: dict[str, str]) -> WebhookReply:
        async with transaction(self.database.control_sessions) as tx:
            channel = await ChannelService(tx).get_for_intake(tenant_id=tenant_id, channel_id=channel_id)
            if channel.provider != "wecom":
                raise InvalidInput("Customer-service callbacks require a WeCom Channel")
            secret = await self.execution.credentials(tx).reveal_secret_for_owner(tenant_id=tenant_id,
                credential_id=channel.credential_id, owner_kind=channel.credential_owner_kind, owner_id=channel.credential_owner_id)
        adapter = next(item for item in self.adapters.adapters if isinstance(item, WeComAdapter))
        notice = await adapter.receive_customer_service_notice(channel, secret, body=body, headers=headers, now=datetime.now(UTC))
        async with transaction(self.database.control_sessions) as tx:
            await ChannelSyncCursors(tx, self._context_codec).accept(channel, event_id=notice.event_id,
                open_kfid=notice.open_kfid, event_token=notice.token)
        return WebhookReply(200, "text/plain", "success")

    async def _sync_loop(self) -> None:
        while not self._closed:
            try:
                after = None
                while not self._closed:
                    async with transaction(self.database.control_sessions) as tx:
                        cursors = await ChannelSyncCursors(tx, self._context_codec).pending(after_id=after)
                    for cursor in cursors:
                        try:
                            async with transaction(self.database.control_sessions) as tx:
                                channel = await ChannelService(tx).get_for_intake(tenant_id=cursor.tenant_id, channel_id=cursor.channel_id)
                                secret = await self.execution.credentials(tx).reveal_secret_for_owner(tenant_id=cursor.tenant_id,
                                    credential_id=channel.credential_id, owner_kind=channel.credential_owner_kind, owner_id=channel.credential_owner_id)
                            adapter = next(item for item in self.adapters.adapters if isinstance(item, WeComAdapter))
                            page = await adapter.sync_customer_service(channel, secret, open_kfid=cursor.open_kfid,
                                event_token=cursor.coordinate if cursor.kind == "token" else None,
                                cursor=cursor.coordinate if cursor.kind == "cursor" else None)
                            for message in page.messages:
                                await self.accept_authenticated(channel, InboundResult(message=message))
                            async with transaction(self.database.control_sessions) as tx:
                                await ChannelSyncCursors(tx, self._context_codec).advance(cursor, next_cursor=page.next_cursor)
                        except (DomainError, SQLAlchemyError, httpx.HTTPError, TimeoutError) as error:
                            self.failures += 1
                            logger.warning("Customer-service synchronization requires retry: %s", type(error).__name__)
                    if len(cursors) < 100:
                        break
                    after = cursors[-1].id
            except (DomainError, SQLAlchemyError) as error:
                self.failures += 1
                logger.warning("Customer-service cursor scan requires retry: %s", type(error).__name__)
            await asyncio.sleep(1)

    async def create_wechat_qr(self, principal: TenantPrincipal, *, agent_id: UUID,
            route_tag: SecretStr | None = None) -> UUID:
        require_admin(principal)
        async with transaction(self.database.control_sessions) as tx:
            await AgentService(tx).get(principal, agent_id=agent_id)
        self._qr = {id: request for id, request in self._qr.items() if request.expires > monotonic()}
        if len(self._qr) + self._qr_starting >= 32:
            raise InvalidInput("WeChat QR login capacity is exhausted")
        self._qr_starting += 1
        try:
            challenge = await self._wechat().create_qr(route_tag=route_tag)
            id = uuid4()
            self._qr[id] = _QRLogin(principal.tenant_id, principal.membership_id, agent_id, challenge, monotonic() + 300, route_tag)
            return id
        finally:
            self._qr_starting -= 1

    def _qr_request(self, principal: TenantPrincipal, id: UUID) -> _QRLogin:
        require_admin(principal)
        request = self._qr.get(id)
        if request is None or request.expires <= monotonic():
            self._qr.pop(id, None)
            raise NotFound("WeChat QR login expired or is unavailable")
        if (request.tenant_id, request.membership_id) != (principal.tenant_id, principal.membership_id):
            raise AccessDenied("WeChat QR login belongs to another requester")
        return request

    async def wechat_qr_image(self, principal: TenantPrincipal, *, request_id: UUID) -> tuple[bytes, str]:
        request = self._qr_request(principal, request_id)
        return await self._wechat().qr_image(request.challenge.image_url)

    async def poll_wechat_qr(self, principal: TenantPrincipal, *, request_id: UUID,
            verify_code: SecretStr | None = None) -> dict[str, object]:
        request = self._qr_request(principal, request_id)
        async with request.lock:
            self._qr_request(principal, request_id)
            return await self._confirm_wechat_qr(principal, request, verify_code)

    async def _confirm_wechat_qr(self, principal: TenantPrincipal, request: _QRLogin, verify_code: SecretStr | None) -> dict[str, object]:
        if request.channel_id is not None:
            return {"status": "confirmed", "channel_id": str(request.channel_id)}
        options = {"base_url": request.base_url} if request.base_url is not None else {}
        status = await self._wechat().qr_status(request.challenge.qrcode, route_tag=request.route_tag, verify_code=verify_code, **options)
        if status.redirect_base_url is not None:
            request.base_url = status.redirect_base_url
        if status.status != "confirmed":
            return {"status": status.status}
        if status.bot_token is None or status.bot_id is None or status.base_url is None:
            raise InvalidInput("WeChat confirmation is incomplete")
        bundle = {"version": 1, "bot_token": status.bot_token.get_secret_value()}
        if request.route_tag is not None:
            bundle["route_tag"] = request.route_tag.get_secret_value()
        async with transaction(self.database.control_sessions) as tx:
            owner = ChannelService(tx)
            existing = next((item for item in await owner.list(principal, agent_id=request.agent_id)
                if item.provider == "wechat" and item.external_identity == status.bot_id), None)
            if existing is not None:
                await self.execution.credentials(tx).rotate_secret(principal, credential_id=existing.credential_id,
                    secret=Secret(json.dumps(bundle)))
                channel = await owner.set_enabled(principal, channel_id=existing.id, enabled=True)
                settings = json.loads(existing.settings_json)
                channel = await owner.update_settings(principal, channel_id=existing.id,
                    settings_json=json.dumps({**settings, "base_url": status.base_url}))
            else:
                credential = await self.execution.credentials(tx).create(principal, kind="channel", provider="wechat",
                    label="WeChat bot", owner_kind="agent", owner_id=request.agent_id, secret=Secret(json.dumps(bundle)))
                channel = await owner.configure(principal, agent_id=request.agent_id, provider="wechat", external_identity=status.bot_id,
                    credential_id=credential.id, settings_json=json.dumps({"connection_mode": "long_poll", "base_url": status.base_url,
                        "channel_version": "1.0.0"}))
        request.channel_id = channel.id
        await self.restart_listener(tenant_id=principal.tenant_id, channel_id=channel.id)
        return {"status": "confirmed", "channel_id": str(channel.id), "bot_id": status.bot_id}

    async def load_message(self, transaction_context: TransactionContext, *, tenant_id: UUID, agent_id: UUID,
            kind: str, message_id: UUID) -> DeliveryContent:
        tx = transaction_context
        if kind == "session":
            entry = await SessionService(tx).get_message_for_delivery(tenant_id=tenant_id, agent_id=agent_id, message_id=message_id)
            return DeliveryContent(entry.content.text, tuple(ref.reference for ref in entry.content.references))
        if kind == "group":
            event = await GroupService(tx).get_message_for_delivery(tenant_id=tenant_id, agent_id=agent_id, message_id=message_id)
            return DeliveryContent(event.input.text, tuple(ref.reference for ref in event.input.references))
        raise InvalidInput("Channel message source is unsupported")

    async def receive(self, *, tenant_id: UUID, channel_id: UUID, body: bytes, headers: dict[str, str]) -> WebhookReply:
        if self._closed:
            raise RuntimeError("Channel intake is closed")
        observed = await self.inbound.receive(tenant_id=tenant_id, channel_id=channel_id,
            body=body, headers=headers, now=datetime.now(UTC))
        async with transaction(self.database.control_sessions) as tx:
            channel = await ChannelService(tx).get_for_intake(tenant_id=tenant_id, channel_id=channel_id)
        return await self.accept_resolved(channel, observed)

    async def accept_authenticated(self, channel: ChannelView, result: InboundResult) -> None:
        observed = await self.inbound.accept_authenticated(channel=channel, result=result, now=datetime.now(UTC))
        await self.accept_resolved(channel, observed)

    async def accept_resolved(self, channel: ChannelView, observed: ResolvedInbound) -> WebhookReply:
        if observed.message is None:
            if observed.reply is not None:
                return observed.reply
            if observed.challenge is not None:
                import json
                return WebhookReply(200, "application/json", json.dumps({"challenge": observed.challenge}))
            return WebhookReply(200, "text/plain", "ok")
        message = observed.message
        async with transaction(self.database.control_sessions) as tx:
            previous = await ChannelService(tx).route_for_event(tenant_id=channel.tenant_id, channel_id=channel.id, event_id=message.event_id)
        if previous is not None:
            # Transport redelivery may advance its cursor but cannot restart an
            # already accepted input whose execution was interrupted or unstarted.
            return observed.reply or WebhookReply(200, "text/plain", "ok")
        if observed.membership_id is None:
            raise InvalidInput("Authenticated Channel event has no mapped Membership")
        async with transaction(self.database.control_sessions) as tx:
            identities = IdentityService(tx)
            membership = await identities.require_membership(tenant_id=channel.tenant_id, membership_id=observed.membership_id)
            identity = await identities.resolve_identity(account_id=membership.account_id, tenant_id=channel.tenant_id)
            principal = await PermissionService(tx).freeze_principal(identity.principal)
            await AgentService(tx).get_for_execution(principal, agent_id=channel.agent_id)
            if observed.group_id is not None:
                await GroupService(tx).get(principal, group_id=observed.group_id)
            session_id = None
            if observed.group_id is None:
                owner = ChannelService(tx)
                session_id = await owner.conversation_session(tenant_id=channel.tenant_id, channel_id=channel.id,
                    conversation_id=message.conversation_id, membership_id=principal.membership_id)
                if session_id is None:
                    await owner.get_for_intake(tenant_id=channel.tenant_id, channel_id=channel.id, lock=True)
                    session_id = await owner.conversation_session(tenant_id=channel.tenant_id, channel_id=channel.id,
                        conversation_id=message.conversation_id, membership_id=principal.membership_id)
                    if session_id is None:
                        session = await SessionService(tx).create(principal, agent_id=channel.agent_id)
                        session_id = await owner.bind_conversation(tenant_id=channel.tenant_id, channel_id=channel.id,
                            conversation_id=message.conversation_id, membership_id=principal.membership_id, session_id=session.id)
        references = []
        for index, item in enumerate(message.attachments):
            async with transaction(self.database.control_sessions) as tx:
                secret = await self.execution.credentials(tx).reveal_secret_for_owner(tenant_id=channel.tenant_id,
                    credential_id=channel.credential_id, owner_kind=channel.credential_owner_kind, owner_id=channel.credential_owner_id)
            adapter = next(adapter for adapter in self.adapters.adapters if adapter.provider == channel.provider)
            media_type = item.media_type
            if isinstance(adapter, SlackAdapter):
                data = await adapter.download_file(channel, secret, file_id=item.external_id, maximum=4 * 1024 * 1024)
            elif isinstance(adapter, DiscordAdapter):
                data = await adapter.download_resource(channel, secret, reference=item.external_id, maximum=4 * 1024 * 1024)
            elif isinstance(adapter, FeishuAdapter):
                message_id, separator, resource_key = item.external_id.partition("/")
                if not separator or not message_id or not resource_key:
                    raise InvalidInput("Feishu resource identity is invalid")
                data, media_type = await adapter.download_resource(channel, secret, message_id=message_id,
                    resource_key=resource_key, resource_type="image" if item.name == "image" else "file", maximum=4 * 1024 * 1024)
            elif isinstance(adapter, DingTalkAdapter):
                data, media_type = await adapter.download_media(channel, secret, item.external_id, max_bytes=4 * 1024 * 1024)
            elif isinstance(adapter, WeComAdapter) and observed.reply_context_id is not None:
                context = await self.inbound.private_context(channel, context_id=observed.reply_context_id)
                coordinate = next((value for value in context.media if value.reference_id == item.external_id), None)
                if coordinate is None:
                    raise InvalidInput("WeCom media is not in the authenticated event")
                data = await adapter.download_media(coordinate, maximum=4 * 1024 * 1024)
            else:
                raise InvalidInput("This Channel attachment transport is not implemented")
            async def chunks(content: bytes = data):
                yield content
            key = "channel:" + sha256(f"{channel.id}\0{message.event_id}\0{index}\0{item.external_id}".encode()).hexdigest()
            if observed.group_id is None:
                assert session_id is not None
                uploaded = await self.products.attachments.upload_session(principal, session_id=session_id, upload_source_key=key,
                    filename=item.name, media_type=media_type or "application/octet-stream", chunks=chunks())
            else:
                uploaded = await self.products.attachments.upload_group(principal, group_id=observed.group_id, upload_source_key=key,
                    filename=item.name, media_type=media_type or "application/octet-stream", chunks=chunks())
            references.append(InputReference(uploaded.reference, item.name, media_type))
        content = InputContent(message.text, tuple(references))
        source = "channel:" + sha256(f"{channel.id}\0{message.event_id}".encode()).hexdigest()
        if observed.group_id is None:
            assert session_id is not None
            reply_run, waiting = None, None
            if message.reply_to is not None:
                async with transaction(self.database.control_sessions) as tx:
                    delivered = await ChannelService(tx).delivered_reply(tenant_id=channel.tenant_id, channel_id=channel.id,
                        destination=message.conversation_id, acknowledgement=message.reply_to)
                    if delivered is not None and delivered.kind == "session":
                        question = await SessionService(tx).get_message_for_delivery(tenant_id=channel.tenant_id,
                            agent_id=channel.agent_id, message_id=delivered.message_id)
                        if question.session_id == session_id and question.waiting_reference is not None:
                            reply_run, waiting = question.source_run_id, question.waiting_reference
            async def accepted_session(tx: TransactionContext, accepted: AcceptedInput) -> None:
                await self.products.attachments.bind_session(tx, principal, accepted)
                await ChannelService(tx).record_input_route(tenant_id=channel.tenant_id, channel_id=channel.id,
                    event_id=message.event_id, kind="session", input_id=accepted.entry.id, destination=message.conversation_id,
                    reply_context_id=observed.reply_context_id)
            if reply_run is not None and waiting is not None:
                async with transaction(self.database.control_sessions) as tx:
                    accepted = await SessionService(tx).accept_input(principal, session_id=session_id, source_key=source,
                        input=content, reply_to_run_id=reply_run, waiting_reference=waiting)
                    changed = await RunService(tx).append_related(tenant_id=channel.tenant_id, run_id=reply_run,
                        input=accepted.entry.content, source=SourceIdentity("session_reply", session_id, str(accepted.entry.id)),
                        waiting_reference=waiting)
                    await accepted_session(tx, accepted)
                if self.products.runtime is None:
                    raise RuntimeError("Session reply Runtime is unavailable")
                await self.products.runtime.post_commit(changed)
                await self.products.after_session_input(principal, accepted)
            else:
                await self.products.submit_session(principal, session_id=session_id, source_key=source, input=content,
                    accepted_consumer=accepted_session)
        else:
            reply_run, waiting = None, None
            if message.reply_to is not None:
                async with transaction(self.database.control_sessions) as tx:
                    delivered = await ChannelService(tx).delivered_reply(tenant_id=channel.tenant_id, channel_id=channel.id,
                        destination=message.conversation_id, acknowledgement=message.reply_to)
                    if delivered is not None and delivered.kind == "group":
                        question = await GroupService(tx).get_message_for_delivery(tenant_id=channel.tenant_id,
                            agent_id=channel.agent_id, message_id=delivered.message_id)
                        if question.group_id == observed.group_id and question.waiting_reference is not None:
                            reply_run, waiting = question.source_run_id, question.waiting_reference
            async def accepted_group(tx: TransactionContext, accepted: AcceptedGroupInput) -> None:
                await self.products.attachments.bind_group(tx, principal, accepted)
                await ChannelService(tx).record_input_route(tenant_id=channel.tenant_id, channel_id=channel.id,
                    event_id=message.event_id, kind="group", input_id=accepted.event.id, destination=message.conversation_id,
                    reply_context_id=observed.reply_context_id)
            if reply_run is not None and waiting is not None:
                async with transaction(self.database.control_sessions) as tx:
                    accepted, changed = await GroupService(tx).answer_wait(principal, group_id=observed.group_id,
                        run_id=reply_run, waiting_reference=waiting, source_key=source, input=content)
                    await accepted_group(tx, accepted)
                if self.products.runtime is None:
                    raise RuntimeError("Group reply Runtime is unavailable")
                await self.products.runtime.post_commit(changed)
                await self.products.after_group_answer(principal, accepted, agent_id=changed.run.agent_id)
            else:
                await self.products.other.submit_group(principal, group_id=observed.group_id, source_key=source,
                    input=content, agent_ids=(channel.agent_id,), accepted_consumer=accepted_group)
        return observed.reply or WebhookReply(200, "text/plain", "ok")

    async def message_accepted(self, *, tenant_id: UUID, agent_id: UUID, kind: str, message_id: UUID) -> None:
        """Optional post-commit acceleration; durable cursors recover a missed notification."""
        if kind not in ("session", "group"):
            raise InvalidInput("Channel message source is unsupported")
        self._changed.set()

    async def _consume_messages(self, source: ChannelDeliverySource) -> None:
        async with transaction(self.database.execution_sessions) as tx:
            if source.kind == "session":
                if source.membership_id is None:
                    raise InvalidInput("Channel Session source has no Membership")
                page = await SessionService(tx).read_delivery_page(tenant_id=source.tenant_id, session_id=source.owner_id,
                    agent_id=source.agent_id, membership_id=source.membership_id, after_position=source.cursor)
                messages = [(entry.id, entry.origin_input_id) for entry in page.entries if entry.kind == "reply"]
                next_position = page.next_after_position
            else:
                group_page = await GroupService(tx).read_delivery_page(tenant_id=source.tenant_id, group_id=source.owner_id,
                    after_position=source.cursor)
                default_conversation = await GroupService(tx).default_conversation_id(
                    tenant_id=source.tenant_id, group_id=source.owner_id)
                messages = [(entry.id, entry.origin_event_id) for entry in group_page.entries
                    if entry.kind == "reply" and entry.agent_id == source.agent_id
                    and (entry.origin_event_id is not None or entry.conversation_id == default_conversation)]
                next_position = group_page.next_after_position
            owner = ChannelService(tx)
            for message_id, input_id in messages:
                if input_id is None:
                    await owner.enqueue(tenant_id=source.tenant_id, channel_id=source.channel_id,
                        kind=source.kind, message_id=message_id, destination=source.destination,
                        delivery_key=f"{source.kind}:{message_id}", messages=self.load_message)
                    continue
                routes = await owner.input_routes(tenant_id=source.tenant_id, agent_id=source.agent_id, kind=source.kind, input_id=input_id)
                for route in routes:
                    if route.channel_id == source.channel_id:
                        await owner.enqueue(tenant_id=source.tenant_id, channel_id=route.channel_id, kind=source.kind, message_id=message_id,
                            destination=route.destination, delivery_key=f"{source.kind}:{message_id}", messages=self.load_message,
                            reply_context_id=route.reply_context_id)
            if next_position != source.cursor:
                await owner.advance_message_cursor(source, through_position=next_position)

    async def _scan_messages(self) -> None:
        for kind in ("session", "group"):
            after = None
            while not self._closed:
                async with transaction(self.database.control_sessions) as tx:
                    sources = await ChannelService(tx).delivery_sources(kind=cast(Literal["session", "group"], kind), after_id=after)
                    if kind == "session":
                        if any(source.membership_id is None for source in sources):
                            raise InvalidInput("Channel Session source requires its Membership")
                        heads = await SessionService(tx).delivery_heads(tuple(SessionDeliveryScope(source.tenant_id,
                            source.owner_id, source.agent_id, cast(UUID, source.membership_id)) for source in sources))
                    else:
                        heads = await GroupService(tx).delivery_heads(tuple(GroupDeliveryScope(source.tenant_id, source.owner_id) for source in sources))
                for source in sources:
                    if heads[source.owner_id] <= source.cursor:
                        continue
                    try:
                        await self._consume_messages(source)
                    except (DomainError, SQLAlchemyError) as error:
                        self.failures += 1
                        logger.warning("Channel message consumption requires retry: %s", type(error).__name__)
                if len(sources) < 100:
                    break
                after = sources[-1].id

    async def _delivery_loop(self) -> None:
        while not self._closed:
            self._changed.clear()
            try:
                await self._delivery_cycle()
            except (DomainError, SQLAlchemyError) as error:
                self.failures += 1
                logger.warning("Channel delivery cycle requires retry: %s", type(error).__name__)
            try:
                await asyncio.wait_for(self._changed.wait(), timeout=.2)
            except TimeoutError:
                pass

    async def _delivery_cycle(self) -> None:
            await self._scan_messages()
            after = None
            while not self._closed:
                async with transaction(self.database.control_sessions) as tx:
                    pending = await ChannelService(tx).pending_deliveries(after_id=after)
                if not pending:
                    break
                async def send(tenant_id: UUID, delivery_id: UUID) -> None:
                    try:
                        await self.delivery.send(tenant_id=tenant_id, delivery_id=delivery_id)
                    except (DomainError, SQLAlchemyError) as error:
                        self.failures += 1
                        if isinstance(error, DomainError):
                            async with transaction(self.database.execution_sessions) as tx:
                                await ChannelService(tx).record_delivery_error(tenant_id=tenant_id, delivery_id=delivery_id, code=error.code)
                        logger.warning("Channel delivery failed: %s", type(error).__name__)
                destinations: dict[tuple[UUID, UUID, str], list[DeliveryView]] = {}
                for item in pending:
                    destinations.setdefault((item.tenant_id, item.channel_id, item.destination), []).append(item)
                async def send_ordered(items: list[DeliveryView]) -> None:
                    for item in items:
                        await send(item.tenant_id, item.id)
                await asyncio.gather(*(send_ordered(items) for items in destinations.values()))
                after = pending[-1].id
                if len(pending) < 100:
                    break
            now = datetime.now(UTC)
            async with transaction(self.database.control_sessions) as tx:
                tenants = await ChannelService(tx).expiry_tenants(now=now)
            for tenant in tenants:
                await self.inbound.clear_expired_contexts(tenant_id=tenant, now=now)
