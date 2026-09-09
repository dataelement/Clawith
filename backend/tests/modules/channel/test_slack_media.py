import json

import httpx
import pytest

from app.infrastructure.errors import AccessDenied, InvalidInput
from app.infrastructure.http import create_stateless_http_client
from app.modules.channel.adapters import SlackAdapter

from .test_slack import SECRET, channel


@pytest.mark.parametrize("final_status,expected", [(200,"delivered"),(403,"failed"),(500,"uncertain")])
async def test_external_upload_is_completed_once_and_preserves_ambiguous_publication(final_status, expected):
    calls = []
    def peer(request):
        calls.append(request)
        if request.url.path == "/api/files.getUploadURLExternal":
            assert request.url.params["filename"] == "report.txt" and request.url.params["length"] == "4"
            return httpx.Response(200, json={"ok":True,"file_id":"F1","upload_url":"https://files.slack.com/upload/private-key"})
        if request.url.host == "files.slack.com":
            assert request.content == b"data" and "authorization" not in request.headers
            assert "private-key" not in str(request.url)
            return httpx.Response(200, content=b"OK")
        assert request.url.path == "/api/files.completeUploadExternal"
        assert json.loads(request.content) == {"files":[{"id":"F1","title":"report.txt"}],"channel_id":"D1"}
        return httpx.Response(final_status, json={"ok":True,"files":[{"id":"F1"}]})
    async with create_stateless_http_client(transport=httpx.MockTransport(peer)) as http:
        result = await SlackAdapter(http).send_file(channel(), SECRET, destination="D1", filename="report.txt", content=b"data")
    assert result.status == expected and len(calls) == 3


@pytest.mark.parametrize("url,maximum,error", [
    ("https://files.slack.com/file/private",4,None),
    ("https://files.slack.com/file/private",3,InvalidInput),
    ("https://attacker.example/file",4,AccessDenied),
])
async def test_download_uses_owner_file_identity_and_never_forwards_token_to_foreign_host(url, maximum, error):
    calls = []
    def peer(request):
        calls.append(request)
        if request.url.path == "/api/files.info":
            assert request.url.params["file"] == "F1"
            return httpx.Response(200, json={"ok":True,"file":{"id":"F1","url_private_download":url}})
        assert request.url.host == "files.slack.com" and request.headers["authorization"] == "Bearer bot-secret"
        return httpx.Response(200, content=b"data")
    async with create_stateless_http_client(transport=httpx.MockTransport(peer)) as http:
        operation = SlackAdapter(http).download_file(channel(), SECRET, file_id="F1", maximum=maximum)
        if error:
            with pytest.raises(error):
                await operation
        else:
            assert await operation == b"data"
    assert len(calls) == (1 if error is AccessDenied else 2)
