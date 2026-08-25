"""FastAPI middleware for request tracing, logging, and tenant context injection."""

import time
import uuid

from fastapi import Request, Response
from jose import JWTError, jwt
from loguru import logger
from starlette.middleware.base import BaseHTTPMiddleware

from app.core.error_contract import normalize_trace_id
from app.dao.base import _tenant_ctx


class TraceIdMiddleware(BaseHTTPMiddleware):
    """Middleware to inject trace ID into request context and log requests."""

    async def dispatch(self, request: Request, call_next) -> Response:
        # Reuse only bounded, header-safe client trace IDs.
        trace_id = normalize_trace_id(request.headers.get("X-Trace-Id"))

        # Add trace ID to request state for access in endpoints
        request.state.trace_id = trace_id

        start_time = time.time()

        # Log request
        client_host = request.client.host if request.client else "-"
        logger.info(
            f"--> {request.method} {request.url.path} "
            f"[client: {client_host}]"
        )

        try:
            response = await call_next(request)
            duration = time.time() - start_time

            # Add trace ID to response headers
            response.headers["X-Trace-Id"] = trace_id

            # Log response
            logger.info(
                f"<-- {request.method} {request.url.path} "
                f"{response.status_code} {duration:.3f}s"
            )

            return response

        except Exception as exc:
            duration = time.time() - start_time
            logger.error(
                f"<-- {request.method} {request.url.path} "
                f"ERROR {duration:.3f}s - {exc}"
            )
            raise


class TenantContextMiddleware(BaseHTTPMiddleware):
    """Inject tenant_id from JWT Bearer token into ContextVar for each request.

    This middleware performs a *lightweight, non-validating* JWT decode to extract
    the ``tenant_id`` claim and bind it to ``_tenant_ctx`` ContextVar.  Full JWT
    validation (expiry, signature, user existence) remains the responsibility of
    the ``get_current_user`` FastAPI dependency.

    After this middleware runs, all ``TenantScopedBaseDAO`` methods called within
    the same request coroutine automatically receive the correct ``tenant_id``
    without needing it passed explicitly.

    Background workers and daemons that do not go through HTTP must wrap their
    DB operations with ``tenant_context(tenant_id)`` from ``app.dao.base``.
    """

    def __init__(self, app, jwt_secret: str, jwt_algorithm: str = "HS256") -> None:
        super().__init__(app)
        self._jwt_secret = jwt_secret
        self._jwt_algorithm = jwt_algorithm

    async def dispatch(self, request: Request, call_next) -> Response:
        tenant_id = self._extract_tenant_id(request)
        if tenant_id is not None:
            token = _tenant_ctx.set(tenant_id)
            try:
                return await call_next(request)
            finally:
                _tenant_ctx.reset(token)
        return await call_next(request)

    def _extract_tenant_id(self, request: Request) -> uuid.UUID | None:
        """Attempt to parse tenant_id from Bearer JWT without raising on failure."""
        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            return None
        token = auth_header[len("Bearer "):]
        try:
            payload = jwt.decode(
                token,
                self._jwt_secret,
                algorithms=[self._jwt_algorithm],
                options={"verify_exp": False},  # expiry checked by security layer
            )
            raw = payload.get("tenant_id")
            if raw is None:
                return None
            return uuid.UUID(str(raw))
        except (JWTError, ValueError, AttributeError):
            return None

