# Agent Note: Stateless execution HTTP pools

Status: implemented — infrastructure provides reusable HTTP clients that reject response Cookie state; execution composition owns their lifetime.

## Problem

Provider and MCP calls reuse network connections across separately resolved accounts. HTTPX receives `Set-Cookie` even when a caller builds a raw request without default headers. A normal shared CookieJar can therefore retain one account's remote state and send it on a later request.

## Decision

[`create_stateless_http_client`](../../../../backend/app/infrastructure/http.py) supplies a CookieJar that never stores cookies. The client disables implicit redirects and environment-derived transport configuration. Consumers require this client contract at construction and before sending, because a caller can replace a client's CookieJar after construction. The application or test that creates the client closes it after its consumers finish; individual Model or MCP operations do not close a shared pool.

This factory addresses Cookie state, not every request credential. Account-aware adapters still construct explicit requests and bypass client default authentication and headers. Credentials come from the resolved operation, never a shared client default. Network connection reuse does not imply account-state reuse.

## Alternatives considered

Clearing a shared CookieJar after a request was rejected because concurrent requests can observe or mutate that state before clearing. Disabling connection reuse would avoid one shared-state path but unnecessarily give up pooled transport; rejecting Cookie storage preserves pooling without storing account cookies.

## Consequences

Cookie-dependent integrations require an explicitly separate account-owned client contract. The shared execution factory does not support implicit browser sessions, redirects or process proxy settings. It introduces no cache, persistence or additional lifecycle controller.

## Verification

`uv run --extra dev pytest tests/test_stateless_http.py -q` covers response-cookie rejection, repeated requests without cookies, ordinary or replaced CookieJar rejection, client closure and redirects. Model and MCP adapter integration tests additionally exercise explicit account credentials and default-header/auth isolation. Controlled transports do not prove hosted Provider behavior, live MCP compatibility or platform concurrency performance.
