"""Real configuration acceptance with only the remote Provider replaced."""

import httpx

from app.infrastructure.http import create_stateless_http_client
from app.modules.model.public import ModelExecutionService, ModelHardLimits


async def validate_draft_model(sessions, principal, model, keyring):
    def respond(request):
        assert sessions.kw["bind"].pool.checkedout() == 0
        return httpx.Response(200, json={"choices": [{
            "finish_reason": "tool_calls",
            "message": {"content": "", "tool_calls": [{
                "id": "probe", "function": {
                    "name": "capability_probe", "arguments": '{"value":"ok"}',
                },
            }]},
        }]})

    async with create_stateless_http_client(transport=httpx.MockTransport(respond)) as client:
        execution = ModelExecutionService(
            sessions, http_client=client, credential_keyring=keyring,
            continuation_keys={"test": b"c" * 32}, active_continuation_key="test",
        )
        return await execution.validate_configuration(
            tenant_id=principal.tenant_id, credential_id=model.credential_id,
            provider=model.provider, protocol="openai_chat", model_name=model.model_name,
            endpoint=model.endpoint,
            administrator_limits=ModelHardLimits(model.context_limit, model.output_limit),
            settings=model.settings, capabilities=model.capabilities,
        )
