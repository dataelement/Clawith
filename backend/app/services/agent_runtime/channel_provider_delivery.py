"""Provider adapters for durable Runtime channel delivery envelopes."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os

import httpx
from sqlalchemy import select

from app.models.channel_config import ChannelConfig
from app.services.agent_runtime.channel_delivery import (
    ChannelDeliveryEnvelope,
    ChannelSendResult,
)
from app.services.agent_runtime.command_worker import RuntimeSessionFactory


@dataclass(frozen=True, slots=True)
class _ProviderConfig:
    app_id: str
    app_secret: str
    extra_config: dict


class ChannelProviderDeliveryError(RuntimeError):
    """A provider route is invalid or the provider did not confirm delivery."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _required(target: dict, field: str) -> str:
    value = target.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ChannelProviderDeliveryError(
            "channel_target_invalid",
            f"Channel delivery target is missing {field}",
        )
    return value.strip()


def _chunks(text: str, limit: int) -> list[str]:
    return [text[index : index + limit] for index in range(0, len(text), limit)] or [""]


def _provider_error(channel: str, response: httpx.Response, payload: object | None = None) -> None:
    detail = payload if payload is not None else response.text[:300]
    raise ChannelProviderDeliveryError(
        f"{channel}_send_failed",
        f"{channel} rejected delivery with HTTP {response.status_code}: {str(detail)[:300]}",
    )


class DatabaseChannelDeliverySender:
    """Load current channel credentials and send one already-generated message."""

    def __init__(self, *, session_factory: RuntimeSessionFactory) -> None:
        self._session_factory = session_factory

    async def _config(self, envelope: ChannelDeliveryEnvelope) -> _ProviderConfig:
        async with self._session_factory() as db:
            result = await db.execute(
                select(ChannelConfig).where(
                    ChannelConfig.agent_id == envelope.agent_id,
                    ChannelConfig.channel_type == envelope.channel,
                    ChannelConfig.is_configured.is_(True),
                )
            )
            config = result.scalar_one_or_none()
        if config is None:
            raise ChannelProviderDeliveryError(
                "channel_config_unavailable",
                f"{envelope.channel} channel is not configured",
            )
        return _ProviderConfig(
            app_id=(config.app_id or "").strip(),
            app_secret=(config.app_secret or "").strip(),
            extra_config=dict(config.extra_config or {}),
        )

    async def send(self, envelope: ChannelDeliveryEnvelope) -> ChannelSendResult:
        config = await self._config(envelope)
        handlers = {
            "feishu": self._feishu,
            "dingtalk": self._dingtalk,
            "wecom": self._wecom,
            "wechat": self._wechat,
            "whatsapp": self._whatsapp,
            "slack": self._slack,
            "discord": self._discord,
            "microsoft_teams": self._teams,
        }
        handler = handlers.get(envelope.channel)
        if handler is None:
            raise ChannelProviderDeliveryError(
                "channel_provider_unsupported",
                f"Unsupported channel provider: {envelope.channel}",
            )
        return await handler(envelope, config)

    async def _feishu(
        self,
        envelope: ChannelDeliveryEnvelope,
        config: _ProviderConfig,
    ) -> ChannelSendResult:
        from app.services.feishu_service import feishu_service

        receive_id = _required(envelope.target, "receive_id")
        receive_id_type = _required(envelope.target, "receive_id_type")
        if receive_id_type not in {"open_id", "user_id", "chat_id"}:
            raise ChannelProviderDeliveryError(
                "channel_target_invalid",
                "Unsupported Feishu receive_id_type",
            )
        response = await feishu_service.send_message(
            config.app_id,
            config.app_secret,
            receive_id,
            "text",
            json.dumps({"text": envelope.content}, ensure_ascii=False),
            receive_id_type=receive_id_type,
            stage="runtime_channel_delivery",
        )
        message_id = (response.get("data") or {}).get("message_id")
        return ChannelSendResult(
            provider_message_id=str(message_id) if message_id else None,
        )

    async def _dingtalk(
        self,
        envelope: ChannelDeliveryEnvelope,
        config: _ProviderConfig,
    ) -> ChannelSendResult:
        session_webhook = envelope.target.get("session_webhook")
        if isinstance(session_webhook, str) and session_webhook.strip():
            async with httpx.AsyncClient(timeout=20) as client:
                response = await client.post(
                    session_webhook.strip(),
                    json={
                        "msgtype": "markdown",
                        "markdown": {
                            "title": envelope.target.get("title") or "AI Reply",
                            "text": envelope.content,
                        },
                    },
                )
            payload = response.json() if response.content else {}
            if response.status_code >= 400 or (
                isinstance(payload, dict)
                and payload.get("errcode") not in {None, 0}
            ):
                _provider_error("dingtalk", response, payload)
            await self._recall_dingtalk_reaction(envelope, config)
            return ChannelSendResult()

        from app.services.dingtalk_service import send_dingtalk_message

        user_id = _required(envelope.target, "user_id")
        response = await send_dingtalk_message(
            app_id=config.app_id,
            app_secret=config.app_secret,
            user_id=user_id,
            message=envelope.content,
            agent_id=str(config.extra_config.get("agent_id") or "") or None,
        )
        if response.get("errcode") != 0:
            raise ChannelProviderDeliveryError(
                "dingtalk_send_failed",
                f"DingTalk rejected delivery: {response.get('errmsg') or 'unknown error'}",
            )
        await self._recall_dingtalk_reaction(envelope, config)
        message_id = response.get("processQueryKey") or response.get("task_id")
        return ChannelSendResult(
            provider_message_id=str(message_id) if message_id else None,
        )

    @staticmethod
    async def _recall_dingtalk_reaction(
        envelope: ChannelDeliveryEnvelope,
        config: _ProviderConfig,
    ) -> None:
        source_message_id = envelope.target.get("source_message_id")
        conversation_id = envelope.target.get("conversation_id")
        if not source_message_id or not conversation_id or not config.app_id:
            return
        try:
            from app.services.dingtalk_reaction import recall_thinking_reaction

            await recall_thinking_reaction(
                config.app_id,
                config.app_secret,
                str(source_message_id),
                str(conversation_id),
            )
        except Exception:
            # Reaction cleanup is cosmetic and must not turn a confirmed reply
            # into a retry that could duplicate the provider message.
            return

    async def _wecom(
        self,
        envelope: ChannelDeliveryEnvelope,
        config: _ProviderConfig,
    ) -> ChannelSendResult:
        user_id = _required(envelope.target, "user_id")
        if envelope.target.get("transport") == "websocket":
            from app.services.wecom_stream import wecom_stream_manager

            await wecom_stream_manager.send_message(
                envelope.agent_id,
                _required(envelope.target, "chat_id"),
                envelope.content,
            )
            return ChannelSendResult()
        async with httpx.AsyncClient(timeout=20) as client:
            token_response = await client.get(
                "https://qyapi.weixin.qq.com/cgi-bin/gettoken",
                params={"corpid": config.app_id, "corpsecret": config.app_secret},
            )
            token_payload = token_response.json()
            access_token = token_payload.get("access_token")
            if token_response.status_code >= 400 or not access_token:
                _provider_error("wecom", token_response, token_payload)

            open_kfid = envelope.target.get("open_kfid")
            if envelope.target.get("is_kf") and isinstance(open_kfid, str) and open_kfid:
                state_response = await client.post(
                    "https://qyapi.weixin.qq.com/cgi-bin/kf/service_state/trans",
                    params={"access_token": access_token},
                    json={
                        "open_kfid": open_kfid,
                        "external_userid": user_id,
                        "service_state": 1,
                    },
                )
                state_payload = state_response.json()
                if (
                    state_response.status_code >= 400
                    or state_payload.get("errcode") != 0
                ):
                    _provider_error("wecom", state_response, state_payload)
                response = await client.post(
                    "https://qyapi.weixin.qq.com/cgi-bin/kf/send_msg",
                    params={"access_token": access_token},
                    json={
                        "touser": user_id,
                        "open_kfid": open_kfid,
                        "msgtype": "text",
                        "text": {"content": envelope.content},
                    },
                )
            else:
                agent_id = config.extra_config.get("wecom_agent_id")
                if agent_id in {None, ""}:
                    raise ChannelProviderDeliveryError(
                        "wecom_agent_id_missing",
                        "WeCom channel has no application agent ID",
                    )
                response = await client.post(
                    "https://qyapi.weixin.qq.com/cgi-bin/message/send",
                    params={"access_token": access_token},
                    json={
                        "touser": user_id,
                        "msgtype": "text",
                        "agentid": int(agent_id),
                        "text": {"content": envelope.content},
                    },
                )
            payload = response.json()
            if response.status_code >= 400 or payload.get("errcode") != 0:
                _provider_error("wecom", response, payload)
        message_id = payload.get("msgid")
        return ChannelSendResult(
            provider_message_id=str(message_id) if message_id else None,
        )

    async def _wechat(
        self,
        envelope: ChannelDeliveryEnvelope,
        config: _ProviderConfig,
    ) -> ChannelSendResult:
        from app.services.wechat_channel import (
            WECHAT_ILINK_BASE_URL,
            get_wechat_context_entry,
            send_wechat_text_message,
        )

        user_id = _required(envelope.target, "user_id")
        entry = get_wechat_context_entry(
            config.extra_config,
            from_user_id=user_id,
        )
        context_token = str((entry or {}).get("context_token") or "").strip()
        if not context_token:
            raise ChannelProviderDeliveryError(
                "wechat_context_unavailable",
                "WeChat reply context is no longer available",
            )
        token = str(config.extra_config.get("bot_token") or "").strip()
        if not token:
            raise ChannelProviderDeliveryError(
                "wechat_token_missing",
                "WeChat bot token is missing",
            )
        await send_wechat_text_message(
            token=token,
            base_url=str(
                config.extra_config.get("baseurl") or WECHAT_ILINK_BASE_URL
            ).strip(),
            to_user_id=user_id,
            context_token=context_token,
            text=envelope.content,
            route_tag=(str(config.extra_config.get("route_tag") or "").strip() or None),
        )
        return ChannelSendResult()

    async def _whatsapp(
        self,
        envelope: ChannelDeliveryEnvelope,
        config: _ProviderConfig,
    ) -> ChannelSendResult:
        phone = _required(envelope.target, "phone")
        api_version = str(config.extra_config.get("api_version") or "v23.0").strip()
        if not config.app_id or not config.app_secret:
            raise ChannelProviderDeliveryError(
                "whatsapp_config_incomplete",
                "WhatsApp channel credentials are incomplete",
            )
        provider_ids: list[str] = []
        async with httpx.AsyncClient(timeout=20) as client:
            for chunk in _chunks(envelope.content, 4096):
                response = await client.post(
                    f"https://graph.facebook.com/{api_version}/{config.app_id}/messages",
                    headers={
                        "Authorization": f"Bearer {config.app_secret}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "messaging_product": "whatsapp",
                        "recipient_type": "individual",
                        "to": phone,
                        "type": "text",
                        "text": {"preview_url": False, "body": chunk},
                    },
                )
                payload = response.json()
                if response.status_code >= 400:
                    _provider_error("whatsapp", response, payload)
                provider_ids.extend(
                    str(item.get("id"))
                    for item in payload.get("messages", [])
                    if item.get("id")
                )
        return ChannelSendResult(
            provider_message_id=",".join(provider_ids) or None,
        )

    async def _slack(
        self,
        envelope: ChannelDeliveryEnvelope,
        config: _ProviderConfig,
    ) -> ChannelSendResult:
        channel_id = _required(envelope.target, "channel_id")
        provider_ids: list[str] = []
        async with httpx.AsyncClient(timeout=20) as client:
            for chunk in _chunks(envelope.content, 4000):
                response = await client.post(
                    "https://slack.com/api/chat.postMessage",
                    headers={
                        "Authorization": f"Bearer {config.app_secret}",
                        "Content-Type": "application/json",
                    },
                    json={"channel": channel_id, "text": chunk},
                )
                payload = response.json()
                if response.status_code >= 400 or not payload.get("ok"):
                    _provider_error("slack", response, payload)
                if payload.get("ts"):
                    provider_ids.append(str(payload["ts"]))
        return ChannelSendResult(
            provider_message_id=",".join(provider_ids) or None,
        )

    async def _discord(
        self,
        envelope: ChannelDeliveryEnvelope,
        config: _ProviderConfig,
    ) -> ChannelSendResult:
        interaction_token = envelope.target.get("interaction_token")
        channel_id = envelope.target.get("channel_id")
        reply_to_message_id = envelope.target.get("reply_to_message_id")
        proxy = os.environ.get("DISCORD_PROXY") or os.environ.get("HTTPS_PROXY") or None
        provider_ids: list[str] = []
        async with httpx.AsyncClient(timeout=20, proxy=proxy) as client:
            for index, chunk in enumerate(_chunks(envelope.content, 2000)):
                if isinstance(interaction_token, str) and interaction_token.strip():
                    if not config.app_id:
                        raise ChannelProviderDeliveryError(
                            "discord_config_incomplete",
                            "Discord application ID is missing",
                        )
                    if index == 0:
                        url = (
                            "https://discord.com/api/v10/webhooks/"
                            f"{config.app_id}/{interaction_token}/messages/@original"
                        )
                        response = await client.patch(url, json={"content": chunk})
                    else:
                        url = (
                            "https://discord.com/api/v10/webhooks/"
                            f"{config.app_id}/{interaction_token}"
                        )
                        response = await client.post(url, json={"content": chunk})
                    if (
                        response.status_code in {401, 404}
                        and isinstance(channel_id, str)
                        and channel_id.strip()
                    ):
                        interaction_token = None
                        response = await client.post(
                            "https://discord.com/api/v10/channels/"
                            f"{channel_id.strip()}/messages",
                            headers={
                                "Authorization": f"Bot {config.app_secret}",
                                "Content-Type": "application/json",
                            },
                            json={"content": chunk},
                        )
                else:
                    if not isinstance(channel_id, str) or not channel_id.strip():
                        raise ChannelProviderDeliveryError(
                            "channel_target_invalid",
                            "Discord target has neither interaction token nor channel ID",
                        )
                    payload: dict = {"content": chunk}
                    if (
                        index == 0
                        and isinstance(reply_to_message_id, str)
                        and reply_to_message_id.strip()
                    ):
                        payload["message_reference"] = {
                            "message_id": reply_to_message_id.strip(),
                            "fail_if_not_exists": False,
                        }
                    response = await client.post(
                        f"https://discord.com/api/v10/channels/{channel_id.strip()}/messages",
                        headers={
                            "Authorization": f"Bot {config.app_secret}",
                            "Content-Type": "application/json",
                        },
                        json=payload,
                    )
                if response.status_code >= 400:
                    _provider_error("discord", response)
                if response.content:
                    payload = response.json()
                    if isinstance(payload, dict) and payload.get("id"):
                        provider_ids.append(str(payload["id"]))
        return ChannelSendResult(
            provider_message_id=",".join(provider_ids) or None,
        )

    async def _teams(
        self,
        envelope: ChannelDeliveryEnvelope,
        config: _ProviderConfig,
    ) -> ChannelSendResult:
        from app.api.teams import _send_teams_message

        conversation_id = _required(envelope.target, "conversation_id")
        config_model = ChannelConfig(
            agent_id=envelope.agent_id,
            channel_type="microsoft_teams",
            app_id=config.app_id,
            app_secret=config.app_secret,
            extra_config=config.extra_config,
            is_configured=True,
        )
        activity = {
            "id": str(envelope.delivery_id),
            "type": "message",
            "conversation": {"id": conversation_id},
            "text": envelope.content,
        }
        reply_to_id = envelope.target.get("reply_to_id")
        if isinstance(reply_to_id, str) and reply_to_id:
            activity["replyToId"] = reply_to_id
        sender = envelope.target.get("bot_account")
        recipient = envelope.target.get("recipient")
        if isinstance(sender, dict) and sender.get("id"):
            activity["from"] = sender
        if isinstance(recipient, dict) and recipient.get("id"):
            activity["recipient"] = recipient
        await _send_teams_message(config_model, conversation_id, activity)
        return ChannelSendResult()


__all__ = [
    "ChannelProviderDeliveryError",
    "DatabaseChannelDeliverySender",
]
