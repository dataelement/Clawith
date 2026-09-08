import httpx
import pytest
from modules.model.test_continuation import req, seed, service

from app.infrastructure.http import create_stateless_http_client
from app.modules.model.public import ModelFailure


@pytest.mark.parametrize("status,code,unrecoverable", [
    (429, "rate_limited", False), (500, "provider_unavailable", False),
    (503, "provider_unavailable", False), (400, "provider_rejected", True),
    (401, "provider_rejected", True), (403, "provider_rejected", True),
    (404, "provider_rejected", True),
])
async def test_http_failure_classification(test_database, status, code, unrecoverable):
    principal, model_id, run_id, keyring = await seed(test_database)
    async with create_stateless_http_client(transport=httpx.MockTransport(
            lambda request: httpx.Response(status, text="private-provider-detail"))) as client:
        model = service(test_database, client, keyring)
        resolved = await model.resolve_policy(tenant_id=principal.tenant_id, model_id=model_id, protocol="anthropic")
        failure = await model.execute_step(resolved.policy, req(run_id))
    assert isinstance(failure, ModelFailure)
    assert failure.code == code and failure.unrecoverable is unrecoverable
    assert "private-provider-detail" not in failure.message
