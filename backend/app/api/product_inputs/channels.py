"""Channel administration and authenticated provider webhook transport."""

import json
from dataclasses import asdict
from typing import Literal, cast
from uuid import UUID

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel, ConfigDict, Field, SecretStr

from app.api.product_inputs.auth import authenticated
from app.execution_dependencies.channel_inputs import ChannelInputs
from app.infrastructure.errors import InvalidInput
from app.infrastructure.transactions import transaction
from app.modules.channel.public import ChannelService
from app.modules.group.public import GroupService

router = APIRouter(prefix="/api/channels", tags=["channels"])


class ConfigureChannel(BaseModel):
    model_config = ConfigDict(extra="forbid")
    agent_id: UUID
    provider: Literal["slack", "discord", "teams", "feishu", "wecom", "dingtalk", "wechat"]
    external_identity: str = Field(min_length=1, max_length=512)
    credential_id: UUID
    settings: dict[str, object] = Field(default_factory=dict)


class ChannelEnabled(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    enabled: bool


class BindActor(BaseModel):
    model_config = ConfigDict(extra="forbid")
    external_actor_id: str = Field(min_length=1, max_length=512)
    membership_id: UUID


class BindGroup(BaseModel):
    model_config = ConfigDict(extra="forbid")
    external_group_id: str = Field(min_length=1, max_length=512)
    group_id: UUID


class WeChatQRInput(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    agent_id: UUID
    route_tag: SecretStr | None = Field(default=None, max_length=512)


class WeChatQRVerification(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    verify_code: SecretStr | None = Field(default=None, max_length=64)


@router.post("/wechat/qr", status_code=201)
async def create_wechat_qr(body: WeChatQRInput, request: Request) -> dict[str, str]:
    principal = (await authenticated(request)).principal
    id = await channel_inputs(request).create_wechat_qr(principal, agent_id=body.agent_id, route_tag=body.route_tag)
    return {"request_id": str(id), "image": f"/api/channels/wechat/qr/{id}/image"}


@router.get("/wechat/qr/{request_id}/image")
async def wechat_qr_image(request_id: UUID, request: Request) -> Response:
    principal = (await authenticated(request)).principal
    content, media_type = await channel_inputs(request).wechat_qr_image(principal, request_id=request_id)
    return Response(content, media_type=media_type)


@router.post("/wechat/qr/{request_id}/status")
async def wechat_qr_status(request_id: UUID, body: WeChatQRVerification, request: Request) -> dict[str, object]:
    principal = (await authenticated(request)).principal
    return await channel_inputs(request).poll_wechat_qr(principal, request_id=request_id, verify_code=body.verify_code)


def channel_inputs(request: Request) -> ChannelInputs:
    return cast(ChannelInputs, request.app.state.channel_inputs)


@router.post("", status_code=201)
async def configure(body: ConfigureChannel, request: Request) -> dict[str, object]:
    principal = (await authenticated(request)).principal
    channels = channel_inputs(request)
    async with transaction(channels.database.control_sessions) as tx:
        configured = await ChannelService(tx).configure(principal, agent_id=body.agent_id, provider=body.provider,
            external_identity=body.external_identity, credential_id=body.credential_id, settings_json=json.dumps(body.settings))
    return asdict(configured)


@router.get("")
async def list_channels(agent_id: UUID, request: Request, limit: int = 100, offset: int = 0) -> dict[str, object]:
    principal = (await authenticated(request)).principal
    async with transaction(channel_inputs(request).database.control_sessions) as tx:
        channels = await ChannelService(tx).list(principal, agent_id=agent_id, limit=limit, offset=offset)
    return {"channels": [asdict(channel) for channel in channels]}


@router.patch("/{channel_id}")
async def set_enabled(channel_id: UUID, body: ChannelEnabled, request: Request) -> dict[str, object]:
    principal = (await authenticated(request)).principal
    async with transaction(channel_inputs(request).database.control_sessions) as tx:
        channel = await ChannelService(tx).set_enabled(principal, channel_id=channel_id, enabled=body.enabled)
    return asdict(channel)


@router.post("/{channel_id}/actors", status_code=204)
async def bind_actor(channel_id: UUID, body: BindActor, request: Request) -> None:
    principal = (await authenticated(request)).principal
    async with transaction(channel_inputs(request).database.control_sessions) as tx:
        await ChannelService(tx).bind_actor(principal, channel_id=channel_id,
            external_actor_id=body.external_actor_id, membership_id=body.membership_id)


@router.post("/{channel_id}/groups", status_code=204)
async def bind_group(channel_id: UUID, body: BindGroup, request: Request) -> None:
    principal = (await authenticated(request)).principal
    async with transaction(channel_inputs(request).database.control_sessions) as tx:
        channel = await ChannelService(tx).get(principal, channel_id=channel_id)
        await GroupService(tx).set_agent(principal, group_id=body.group_id, agent_id=channel.agent_id, enabled=True)
        await ChannelService(tx).bind_group(principal, channel_id=channel_id,
            external_group_id=body.external_group_id, group_id=body.group_id)


@router.get("/deliveries/{delivery_id}")
async def get_delivery(delivery_id: UUID, request: Request) -> dict[str, object]:
    principal = (await authenticated(request)).principal
    async with transaction(channel_inputs(request).database.control_sessions) as tx:
        delivery = await ChannelService(tx).get_delivery(principal, delivery_id=delivery_id)
    return asdict(delivery)


@router.post("/deliveries/{delivery_id}/retry")
async def retry_delivery(delivery_id: UUID, request: Request) -> dict[str, object]:
    principal = (await authenticated(request)).principal
    channels = channel_inputs(request)
    async with transaction(channels.database.control_sessions) as tx:
        delivery = await ChannelService(tx).get_delivery(principal, delivery_id=delivery_id)
    return asdict(await channels.delivery.send(tenant_id=delivery.tenant_id, delivery_id=delivery.id))


@router.get("/{tenant_id}/{channel_id}/events", operation_id="verify_channel_webhook")
@router.post("/{tenant_id}/{channel_id}/events", operation_id="receive_channel_webhook")
async def receive(tenant_id: UUID, channel_id: UUID, request: Request) -> Response:
    body, headers = await _webhook_request(request)
    reply = await channel_inputs(request).receive(tenant_id=tenant_id, channel_id=channel_id, body=body, headers=headers)
    return Response(content=reply.body, status_code=reply.status, media_type=reply.content_type)


@router.get("/{tenant_id}/{channel_id}/kf/events", operation_id="verify_wecom_customer_service")
async def verify_customer_service(tenant_id: UUID, channel_id: UUID, request: Request) -> Response:
    return await receive(tenant_id, channel_id, request)


@router.post("/{tenant_id}/{channel_id}/kf/events", operation_id="receive_wecom_customer_service")
async def receive_customer_service(tenant_id: UUID, channel_id: UUID, request: Request) -> Response:
    body, headers = await _webhook_request(request)
    reply = await channel_inputs(request).receive_customer_service(tenant_id=tenant_id, channel_id=channel_id, body=body, headers=headers)
    return Response(content=reply.body, status_code=reply.status, media_type=reply.content_type)


async def _webhook_request(request: Request) -> tuple[bytes, dict[str, str]]:
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > 262144:
            raise InvalidInput("Channel event exceeds its byte bound")
        body.extend(chunk)
    headers = dict(request.headers)
    for key in ("msg_signature", "timestamp", "nonce", "echostr", "signature"):
        if key in request.query_params:
            value = request.query_params[key]
            if len(value.encode()) > 32768:
                raise InvalidInput("Channel verification parameter is too large")
            headers[key] = value
    return bytes(body), headers
