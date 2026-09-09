import logging

import httpx

from app.infrastructure.http import create_stateless_http_client
from app.modules.channel.transport import protect_request_url


async def test_private_wire_url_never_enters_httpx_logs_or_public_representations(caplog):
    wire_paths = []
    def peer(request):
        wire_paths.append(request.url.raw_path)
        assert "private-token" not in repr(request)
        assert "private-token" not in repr(httpx.ReadTimeout("deadline", request=request))
        return httpx.Response(200)
    caplog.set_level(logging.INFO, logger="httpx")
    async with create_stateless_http_client(transport=httpx.MockTransport(peer)) as http:
        request = protect_request_url(httpx.Request("GET", "https://example.com/private-token?secret=private-token"))
        response = await http.send(request, auth=None, follow_redirects=False)
        assert "private-token" not in str(response.request.url)
        assert "private-token" not in repr(response.request.url)
        assert "private-token" not in repr(response)
        await response.aclose()
    assert wire_paths == [b"/private-token?secret=private-token"]
    assert "private-token" not in caplog.text
    assert "redacted-channel-coordinate" in caplog.text
    assert str(httpx.URL("https://example.com/public")) == "https://example.com/public"
import asyncio

import pytest

from app.infrastructure.errors import AccessDenied
from app.modules.channel.contracts import ListenerDisconnected
from app.modules.channel.transport import listen_transport


@pytest.mark.parametrize("error", [OSError("closed"), ExceptionGroup("socket tasks", [TimeoutError(), OSError()])])
async def test_listener_transport_normalizes_only_retryable_disconnects(error):
    async def run():
        raise error
    with pytest.raises(ListenerDisconnected):
        await listen_transport(run())


@pytest.mark.parametrize("error", [AccessDenied("bad credential"), ValueError("defect"),
    ExceptionGroup("mixed tasks", [OSError(), ValueError("defect")]), asyncio.CancelledError()])
async def test_listener_transport_preserves_auth_defects_and_cancellation(error):
    async def run():
        raise error
    with pytest.raises(type(error)):
        await listen_transport(run())
