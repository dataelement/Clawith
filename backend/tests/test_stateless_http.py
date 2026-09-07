import httpx
import pytest

from app.infrastructure.http import create_stateless_http_client, require_stateless_http_client


@pytest.mark.asyncio
async def test_shared_pool_never_accepts_or_sends_response_cookies() -> None:
    requests: list[httpx.Request] = []

    async def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, headers={"set-cookie": "account=private; Path=/"})

    async with create_stateless_http_client(transport=httpx.MockTransport(handle)) as client:
        require_stateless_http_client(client)
        await client.get("https://provider.invalid/first")
        assert not client.cookies
        await client.get("https://provider.invalid/second")
        assert not client.cookies
        assert all("cookie" not in request.headers for request in requests)
    assert client.is_closed


@pytest.mark.asyncio
async def test_ordinary_or_replaced_cookie_jars_are_rejected() -> None:
    async with httpx.AsyncClient() as ordinary:
        with pytest.raises(TypeError, match="stateless HTTP client"):
            require_stateless_http_client(ordinary)
    async with create_stateless_http_client() as client:
        client.cookies = httpx.Cookies({"account": "other"})
        with pytest.raises(TypeError, match="stateless HTTP client"):
            require_stateless_http_client(client)


@pytest.mark.asyncio
async def test_redirects_are_not_followed_implicitly() -> None:
    seen: list[str] = []

    async def handle(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(302, headers={"location": "https://another.invalid/"})

    async with create_stateless_http_client(transport=httpx.MockTransport(handle)) as client:
        response = await client.get("https://provider.invalid/")
    assert response.status_code == 302
    assert seen == ["https://provider.invalid/"]
