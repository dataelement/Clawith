from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from pydantic import BaseModel

from app.core.error_contract import register_error_handlers
from app.core.middleware import TraceIdMiddleware


class _Payload(BaseModel):
    count: int


def _test_app() -> FastAPI:
    app = FastAPI()
    register_error_handlers(app)
    app.add_middleware(TraceIdMiddleware)

    @app.get("/string-error")
    async def string_error() -> None:
        raise HTTPException(status_code=404, detail="Widget not found")

    @app.get("/object-error")
    async def object_error() -> None:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "widget_conflict",
                "message": "Widget already exists",
                "run_id": "run-1",
                "agent_id": "agent-1",
                "stage": "request",
                "details": {"field": "name"},
                "retryable": False,
            },
            headers={"Retry-After": "3"},
        )

    @app.post("/validate")
    async def validate(payload: _Payload) -> _Payload:
        return payload

    @app.get("/crash")
    async def crash() -> None:
        raise RuntimeError("database password is hunter2")

    return app


def test_http_exception_keeps_legacy_detail_and_adds_canonical_error() -> None:
    with TestClient(_test_app()) as client:
        response = client.get("/string-error", headers={"X-Trace-Id": "client-trace-123"})

    assert response.status_code == 404
    assert response.headers["X-Trace-Id"] == "client-trace-123"
    assert response.json() == {
        "detail": "Widget not found",
        "error": {
            "code": "http_404",
            "message": "Widget not found",
            "trace_id": "client-trace-123",
        },
    }


def test_framework_http_exception_uses_canonical_error_contract() -> None:
    with TestClient(_test_app()) as client:
        response = client.get("/missing-route")

    body = response.json()
    assert response.status_code == 404
    assert body["detail"] == "Not Found"
    assert body["error"] == {
        "code": "http_404",
        "message": "Not Found",
        "trace_id": response.headers["X-Trace-Id"],
    }


def test_http_exception_preserves_structured_code_details_and_headers() -> None:
    with TestClient(_test_app()) as client:
        response = client.get("/object-error")

    body = response.json()
    assert response.status_code == 409
    assert response.headers["Retry-After"] == "3"
    assert response.headers["X-Trace-Id"] == body["error"]["trace_id"]
    assert body["detail"] == {
        "code": "widget_conflict",
        "message": "Widget already exists",
        "run_id": "run-1",
        "agent_id": "agent-1",
        "stage": "request",
        "details": {"field": "name"},
        "retryable": False,
    }
    assert body["error"] == {
        "code": "widget_conflict",
        "message": "Widget already exists",
        "trace_id": response.headers["X-Trace-Id"],
        "run_id": "run-1",
        "agent_id": "agent-1",
        "stage": "request",
        "details": {"field": "name"},
        "retryable": False,
    }


def test_request_validation_error_uses_safe_canonical_contract() -> None:
    with TestClient(_test_app()) as client:
        response = client.post("/validate", json={"count": "not-an-integer"})

    body = response.json()
    assert response.status_code == 422
    assert response.headers["X-Trace-Id"] == body["error"]["trace_id"]
    assert body["detail"] == body["error"]["details"]
    assert body["error"]["code"] == "validation_error"
    assert body["error"]["message"] == "Request validation failed"


def test_uncaught_exception_is_safe_json_with_matching_trace_id() -> None:
    with TestClient(_test_app(), raise_server_exceptions=False) as client:
        response = client.get("/crash")

    body = response.json()
    assert response.status_code == 500
    assert response.headers["X-Trace-Id"] == body["error"]["trace_id"]
    assert body == {
        "detail": "Internal server error",
        "error": {
            "code": "internal_error",
            "message": "Internal server error",
            "trace_id": response.headers["X-Trace-Id"],
        },
    }
    assert "hunter2" not in response.text
    assert "RuntimeError" not in response.text


def test_invalid_client_trace_id_is_regenerated() -> None:
    invalid_trace_id = "bad trace id with spaces"

    with TestClient(_test_app()) as client:
        response = client.get("/string-error", headers={"X-Trace-Id": invalid_trace_id})

    trace_id = response.headers["X-Trace-Id"]
    assert trace_id == response.json()["error"]["trace_id"]
    assert trace_id != invalid_trace_id
    assert len(trace_id) == 12
    assert all(character in "0123456789abcdef" for character in trace_id)
