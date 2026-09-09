"""One Discord Gateway subscription with bounded frames and resumable sequence."""

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from contextlib import AbstractAsyncContextManager
from urllib.parse import urlsplit

from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import ConnectionClosed

from app.infrastructure.errors import AccessDenied, InvalidInput
from app.modules.channel.adapters import _json, _text
from app.modules.channel.contracts import AttachmentReference, ChannelView, InboundResult, IncomingMessage

_WIRE_LOGGER = logging.Logger("clawith.channel.discord.wire", level=logging.WARNING)  # noqa: LOG001 -- isolated wire logger never inherits DEBUG authentication frame logging.
Connector = Callable[[str], AbstractAsyncContextManager[ClientConnection]]


class _Reconnect(Exception):
    pass


def connector(url: str) -> AbstractAsyncContextManager[ClientConnection]:
    return connect(url, max_size=262144, max_queue=16, open_timeout=10, close_timeout=5,
        ping_interval=None, logger=_WIRE_LOGGER, proxy=None)


def _message(data: dict, bot_id: str) -> InboundResult | None:
    author = data.get("author")
    if not isinstance(author, dict) or author.get("bot"):
        return None
    mentions = data.get("mentions", [])
    if not isinstance(mentions, list) or len(mentions) > 100:
        raise InvalidInput("Discord mentions exceed their bound")
    group = data.get("guild_id") is not None
    mentioned = any(isinstance(item, dict) and item.get("id") == bot_id for item in mentions)
    if group and not mentioned:
        return None
    conversation, event = _text(data.get("channel_id")), _text(data.get("id"))
    text = _text(data.get("content", ""), maximum=262144, empty=True)
    if mentioned:
        text = text.replace(f"<@{bot_id}>", "").replace(f"<@!{bot_id}>", "").strip()
    attachments = data.get("attachments", [])
    if not isinstance(attachments, list) or len(attachments) > 64:
        raise InvalidInput("Discord attachments exceed their bound")
    references = []
    for item in attachments:
        if not isinstance(item, dict):
            raise InvalidInput("Discord attachment is invalid")
        references.append(AttachmentReference(conversation + "/" + event + "/" + _text(item.get("id")),
            _text(item.get("filename")), _text(item["content_type"], maximum=256) if item.get("content_type") else None))
    if not text and not references:
        return None
    quoted = data.get("message_reference")
    reply_to = None
    if quoted is not None:
        if not isinstance(quoted, dict):
            raise InvalidInput("Discord quoted message is invalid")
        if quoted.get("channel_id", conversation) != conversation:
            raise AccessDenied("Discord quoted message belongs to another conversation")
        reply_to = _text(quoted.get("message_id"))
    return InboundResult(message=IncomingMessage(event, _text(author.get("id")), conversation,
        conversation if group else None, text, reply_to, tuple(references)))


async def listen(channel: ChannelView, token: str, on_message: Callable[[InboundResult], Awaitable[None]],
        *, connect_socket: Connector = connector) -> None:
    gateway = "wss://gateway.discord.gg/?v=10&encoding=json"
    session_id: str | None = None
    sequence: int | None = None
    bot_id: str | None = None
    backoff = 1
    while True:
        try:
            async with connect_socket(gateway) as socket:
                async with asyncio.timeout(10):
                    hello = _json(await socket.recv())
                data = hello.get("d")
                interval = data.get("heartbeat_interval") if isinstance(data, dict) else None
                if hello.get("op") != 10 or isinstance(interval, bool) or not isinstance(interval, (int, float)) or not 100 <= interval <= 120000:
                    raise InvalidInput("Discord Gateway heartbeat configuration is invalid")
                heartbeat_acknowledged = True

                async def heartbeat(interval_seconds: float) -> None:
                    nonlocal heartbeat_acknowledged, sequence
                    while True:
                        await asyncio.sleep(interval_seconds)
                        if not heartbeat_acknowledged:
                            await socket.close(code=4000, reason="Heartbeat acknowledgement missing")
                            return
                        heartbeat_acknowledged = False
                        await socket.send(json.dumps({"op":1,"d":sequence}))  # noqa: B023 -- heartbeat reads the live accepted sequence; it is joined before reconnect.

                if session_id is None:
                    await socket.send(json.dumps({"op":2,"d":{"token":token,"intents":37377,
                        "properties":{"os":"linux","browser":"clawith","device":"clawith"}}}))
                else:
                    await socket.send(json.dumps({"op":6,"d":{"token":token,"session_id":session_id,"seq":sequence}}))
                pulse = asyncio.create_task(heartbeat(interval / 1000))
                try:
                    async for raw in socket:
                        event = _json(raw)
                        op, data = event.get("op"), event.get("d")
                        if op == 11:
                            heartbeat_acknowledged = True
                        elif op == 1:
                            await socket.send(json.dumps({"op":1,"d":sequence}))
                        elif op == 7:
                            raise _Reconnect()
                        elif op == 9:
                            if data is not True:
                                session_id, sequence, bot_id = None, None, None
                            raise _Reconnect()
                        elif op == 0:
                            observed_sequence = event.get("s")
                            if type(observed_sequence) is not int or observed_sequence < 0 or not isinstance(data, dict):
                                raise InvalidInput("Discord Gateway dispatch is invalid")
                            if event.get("t") == "READY":
                                application, user = data.get("application"), data.get("user")
                                if not isinstance(application, dict) or application.get("id") != channel.external_identity or not isinstance(user, dict):
                                    raise AccessDenied("Discord Gateway application does not match")
                                bot_id = _text(user.get("id"))
                                session_id = _text(data.get("session_id"))
                                resume = _text(data.get("resume_gateway_url"), maximum=2048)
                                parsed = urlsplit(resume)
                                if parsed.scheme != "wss" or not parsed.hostname or not (parsed.hostname == "gateway.discord.gg" or parsed.hostname.endswith(".discord.gg")) or parsed.username or parsed.password or parsed.query or parsed.fragment:
                                    raise AccessDenied("Discord resume endpoint is invalid")
                                gateway = resume.rstrip("/") + "/?v=10&encoding=json"
                                backoff = 1
                            elif event.get("t") == "RESUMED":
                                backoff = 1
                            elif event.get("t") == "MESSAGE_CREATE":
                                if bot_id is None:
                                    raise AccessDenied("Discord message arrived before authenticated readiness")
                                incoming = _message(data, bot_id)
                                if incoming is not None:
                                    await on_message(incoming)
                            sequence = observed_sequence
                finally:
                    pulse.cancel()
                    try:
                        await pulse
                    except asyncio.CancelledError:
                        pass
            await asyncio.sleep(backoff)
        except ConnectionClosed as exc:
            if exc.rcvd is not None and exc.rcvd.code in {4004,4010,4011,4012,4013,4014}:
                raise AccessDenied("Discord Gateway configuration was rejected") from None
            await asyncio.sleep(backoff)
        except _Reconnect:
            await asyncio.sleep(backoff)
        backoff = min(backoff * 2, 30)
