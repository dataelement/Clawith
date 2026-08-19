import pytest

from app.services import feishu_service as feishu_service_module


class _FakeResponse:
    def __init__(self, status_code: int, payload: dict):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


class _FakeAsyncClient:
    def __init__(self, *, send_payload: dict | None = None, patch_payload: dict | None = None, get_payload: dict | None = None):
        self._send_payload = send_payload or {"code": 0, "msg": "ok", "data": {"message_id": "m_1"}}
        self._patch_payload = patch_payload or {"code": 0, "msg": "ok"}
        self._get_payload = get_payload or {"code": 0, "msg": "ok", "data": {"items": []}}

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def post(self, url, **_kwargs):
        if "app_access_token/internal" in url:
            return _FakeResponse(200, {"app_access_token": "token_x"})
        return _FakeResponse(200, self._send_payload)

    async def patch(self, _url, **_kwargs):
        return _FakeResponse(200, self._patch_payload)

    async def get(self, _url, **_kwargs):
        return _FakeResponse(200, self._get_payload)


@pytest.mark.asyncio
async def test_send_message_raises_when_business_code_nonzero(monkeypatch):
    monkeypatch.setattr(
        feishu_service_module.httpx,
        "AsyncClient",
        lambda: _FakeAsyncClient(send_payload={"code": 99991663, "msg": "rate limited"}),
    )

    with pytest.raises(RuntimeError, match="code=99991663"):
        await feishu_service_module.feishu_service.send_message(
            "app_id",
            "app_secret",
            "ou_xxx",
            "text",
            "{\"text\":\"hello\"}",
            stage="unit_test_send",
        )


@pytest.mark.asyncio
async def test_patch_message_raises_when_business_code_nonzero(monkeypatch):
    monkeypatch.setattr(
        feishu_service_module.httpx,
        "AsyncClient",
        lambda: _FakeAsyncClient(patch_payload={"code": 10019, "msg": "invalid card content"}),
    )

    with pytest.raises(RuntimeError, match="code=10019"):
        await feishu_service_module.feishu_service.patch_message(
            "app_id",
            "app_secret",
            "om_xxx",
            "{\"content\":\"test\"}",
            stage="unit_test_patch",
        )


@pytest.mark.asyncio
async def test_add_message_reaction_uses_glance_emoji(monkeypatch):
    client = _FakeAsyncClient()
    calls: dict[str, object] = {}

    async def post(url, **kwargs):
        if "app_access_token/internal" in url:
            return _FakeResponse(200, {"app_access_token": "token_x"})
        calls["url"] = url
        calls["kwargs"] = kwargs
        return _FakeResponse(200, {"code": 0, "msg": "ok", "data": {}})

    client.post = post
    monkeypatch.setattr(feishu_service_module.httpx, "AsyncClient", lambda: client)

    await feishu_service_module.feishu_service.add_message_reaction(
        "app_id",
        "app_secret",
        "om_source",
        "GLANCE",
        stage="unit_test_reaction",
    )

    assert calls["url"] == (
        "https://open.feishu.cn/open-apis/im/v1/messages/om_source/reactions"
    )
    assert calls["kwargs"]["json"] == {  # type: ignore[index]
        "reaction_type": {"emoji_type": "GLANCE"}
    }


@pytest.mark.asyncio
async def test_list_bot_chats_uses_app_identity_and_parses_groups(monkeypatch):
    client = _FakeAsyncClient(
        get_payload={
            "code": 0,
            "msg": "ok",
            "data": {"items": [{"chat_id": "oc_1", "name": "项目群"}], "has_more": False},
        }
    )
    monkeypatch.setattr(feishu_service_module.httpx, "AsyncClient", lambda **_kwargs: client)

    result = await feishu_service_module.feishu_service.list_bot_chats("app", "secret")

    assert result["data"]["items"][0]["chat_id"] == "oc_1"
