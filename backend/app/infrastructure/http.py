"""Application-owned HTTP pools without cross-request cookie account state."""

from http.cookiejar import Cookie, CookieJar

import httpx


class _RejectingCookieJar(CookieJar):
    def set_cookie(self, cookie: Cookie) -> None:
        # Account credentials belong to the explicitly resolved request, never a shared jar.
        return None


def create_stateless_http_client(
    *,
    transport: httpx.AsyncBaseTransport | None = None,
    timeout: httpx.Timeout | float = 30.0,
    limits: httpx.Limits | None = None,
) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=transport,
        timeout=timeout,
        limits=limits or httpx.Limits(),
        cookies=_RejectingCookieJar(),
        follow_redirects=False,
        trust_env=False,
    )


def require_stateless_http_client(client: httpx.AsyncClient) -> None:
    """Reject ordinary clients before a shared Provider/MCP pool receives cookie state."""
    if not isinstance(client.cookies.jar, _RejectingCookieJar):
        raise TypeError("A stateless HTTP client is required for account-isolated execution")
