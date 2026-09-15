"""Redact provider-required URL credentials without changing wire coordinates."""

from collections.abc import Awaitable

import httpx
from websockets.exceptions import ConnectionClosed

from app.modules.channel.contracts import ListenerDisconnected


async def listen_transport(operation: Awaitable[None]) -> None:
    try:
        await operation
    except (ConnectionClosed, httpx.HTTPError, TimeoutError, OSError):
        raise ListenerDisconnected("Channel transport disconnected") from None
    except ExceptionGroup as error:
        _, remaining = error.split((ConnectionClosed, httpx.HTTPError, TimeoutError, OSError))
        if remaining is not None:
            raise
        raise ListenerDisconnected("Channel transport tasks disconnected") from None


class SecretURL(httpx.URL):
    def __str__(self) -> str:
        return "https://<redacted-channel-coordinate>"

    def __repr__(self) -> str:
        return "SecretURL(<redacted>)"


def protect_request_url(request: httpx.Request) -> httpx.Request:
    request.url = SecretURL(request.url)
    return request
