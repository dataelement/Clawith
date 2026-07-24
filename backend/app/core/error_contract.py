"""Canonical, backward-compatible HTTP error responses."""

from http import HTTPStatus
import re
from typing import Any, NotRequired, TypedDict

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from loguru import logger
from starlette.exceptions import HTTPException

from app.core.logging_config import new_trace_id, set_trace_id

TRACE_ID_HEADER = "X-Trace-Id"
_TRACE_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{7,63}$")
_INTERNAL_ERROR_MESSAGE = "Internal server error"


class ErrorObject(TypedDict):
    """Safe error fields shared by HTTP and other transport contracts."""

    code: str
    message: str
    trace_id: str
    run_id: NotRequired[str]
    agent_id: NotRequired[str]
    stage: NotRequired[str]
    details: NotRequired[Any]
    retryable: NotRequired[bool]


def normalize_trace_id(candidate: str | None) -> str:
    """Accept a bounded, header-safe trace ID or generate a new one."""
    if candidate and _TRACE_ID_PATTERN.fullmatch(candidate):
        set_trace_id(candidate)
        return candidate
    return new_trace_id()


def get_request_trace_id(request: Request) -> str:
    """Return the request trace ID, repairing missing or invalid state."""
    trace_id = getattr(request.state, "trace_id", None)
    trace_id = normalize_trace_id(trace_id)
    request.state.trace_id = trace_id
    return trace_id


def _status_message(status_code: int) -> str:
    try:
        return HTTPStatus(status_code).phrase
    except ValueError:
        return "HTTP error"


def build_error_object(
    *,
    code: str,
    message: str,
    trace_id: str,
    run_id: str | None = None,
    agent_id: str | None = None,
    stage: str | None = None,
    details: Any | None = None,
    retryable: bool | None = None,
) -> ErrorObject:
    """Build the canonical safe error object, omitting absent optional fields."""
    error: ErrorObject = {
        "code": code,
        "message": message,
        "trace_id": trace_id,
    }
    if run_id is not None:
        error["run_id"] = run_id
    if agent_id is not None:
        error["agent_id"] = agent_id
    if stage is not None:
        error["stage"] = stage
    if details is not None:
        error["details"] = details
    if retryable is not None:
        error["retryable"] = retryable
    return error


def _error_body(
    *,
    detail: Any,
    code: str,
    message: str,
    trace_id: str,
    run_id: str | None = None,
    agent_id: str | None = None,
    stage: str | None = None,
    details: Any | None = None,
    retryable: bool | None = None,
) -> dict[str, Any]:
    return {
        "detail": detail,
        "error": build_error_object(
            code=code,
            message=message,
            trace_id=trace_id,
            run_id=run_id,
            agent_id=agent_id,
            stage=stage,
            details=details,
            retryable=retryable,
        ),
    }


def _optional_text(value: Any) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _http_error_fields(
    exc: HTTPException,
) -> tuple[
    str,
    str,
    str | None,
    str | None,
    str | None,
    Any | None,
    bool | None,
]:
    detail = exc.detail
    if isinstance(detail, dict):
        raw_code = detail.get("code")
        raw_message = detail.get("message")
        code = raw_code if isinstance(raw_code, str) and raw_code else f"http_{exc.status_code}"
        message = raw_message if isinstance(raw_message, str) and raw_message else _status_message(exc.status_code)
        retryable = detail.get("retryable")
        return (
            code,
            message,
            _optional_text(detail.get("run_id")),
            _optional_text(detail.get("agent_id")),
            _optional_text(detail.get("stage")),
            detail.get("details"),
            retryable if isinstance(retryable, bool) else None,
        )
    if isinstance(detail, str):
        return f"http_{exc.status_code}", detail, None, None, None, None, None
    return (
        f"http_{exc.status_code}",
        _status_message(exc.status_code),
        None,
        None,
        None,
        detail,
        None,
    )


async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
    """Preserve explicit endpoint detail while adding the canonical error object."""
    trace_id = get_request_trace_id(request)
    code, message, run_id, agent_id, stage, details, retryable = _http_error_fields(
        exc
    )
    body = _error_body(
        detail=exc.detail,
        code=code,
        message=message,
        trace_id=trace_id,
        run_id=run_id,
        agent_id=agent_id,
        stage=stage,
        details=details,
        retryable=retryable,
    )
    headers = dict(exc.headers or {})
    headers[TRACE_ID_HEADER] = trace_id
    return JSONResponse(
        status_code=exc.status_code,
        content=jsonable_encoder(body),
        headers=headers,
    )


async def request_validation_error_handler(
    request: Request,
    exc: RequestValidationError,
) -> JSONResponse:
    """Return validation issues as safe structured details."""
    trace_id = get_request_trace_id(request)
    details = exc.errors()
    body = _error_body(
        detail=details,
        code="validation_error",
        message="Request validation failed",
        trace_id=trace_id,
        details=details,
    )
    return JSONResponse(
        status_code=422,
        content=jsonable_encoder(body),
        headers={TRACE_ID_HEADER: trace_id},
    )


async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Log unknown failures with context without exposing their text to clients."""
    trace_id = get_request_trace_id(request)
    logger.opt(exception=(type(exc), exc, exc.__traceback__)).error(
        "Unhandled HTTP request exception"
    )
    body = _error_body(
        detail=_INTERNAL_ERROR_MESSAGE,
        code="internal_error",
        message=_INTERNAL_ERROR_MESSAGE,
        trace_id=trace_id,
    )
    return JSONResponse(
        status_code=500,
        content=body,
        headers={TRACE_ID_HEADER: trace_id},
    )


def register_error_handlers(app: FastAPI) -> None:
    """Install the canonical handlers on a FastAPI application."""
    app.add_exception_handler(HTTPException, http_exception_handler)
    app.add_exception_handler(RequestValidationError, request_validation_error_handler)
    app.add_exception_handler(Exception, unhandled_exception_handler)
