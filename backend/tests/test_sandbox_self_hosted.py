from __future__ import annotations

import pytest

from app.services.sandbox.config import SandboxConfig
from app.services.sandbox.remote import self_hosted_backend
from app.services.sandbox.remote.self_hosted_backend import SelfHostedBackend


@pytest.mark.asyncio
async def test_health_probe_log_excludes_url_secrets(monkeypatch) -> None:
    debug_calls: list[tuple[object, ...]] = []

    class FailingClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args) -> None:
            return None

        async def get(self, _url: str, *, timeout: float):
            raise RuntimeError(f"probe failed after {timeout}")

    monkeypatch.setattr(
        self_hosted_backend.httpx,
        "AsyncClient",
        FailingClient,
    )
    monkeypatch.setattr(
        self_hosted_backend.logger,
        "debug",
        lambda *args: debug_calls.append(args),
    )
    backend = SelfHostedBackend(
        SandboxConfig(
            api_url=(
                "https://sentinel-user:sentinel-password@example.test/"
                "v1/shell/exec?token=sentinel-query#sentinel-fragment"
            )
        )
    )

    assert await backend.health_check() is False
    assert [call[1] for call in debug_calls] == ["sandbox", "health"]
    logged = repr(debug_calls)
    for secret in (
        "sentinel-user",
        "sentinel-password",
        "sentinel-query",
        "sentinel-fragment",
    ):
        assert secret not in logged
