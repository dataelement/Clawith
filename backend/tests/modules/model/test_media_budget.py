"""Model-owned media counting uses controlled HTTP and real Credential/replay persistence."""

import asyncio
import json
from copy import deepcopy
from dataclasses import replace
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import select

from app.infrastructure.http import create_stateless_http_client
from app.modules.model.adapters import build_request, count_input_tokens, execute
from app.modules.model.execution import ProviderFailure
from app.modules.model.models import ProviderContinuationRecord
from app.modules.model.public import (
    ModelContent,
    ModelExecutionService,
    ModelFailure,
    ModelLimits,
    ModelMessage,
    ModelStepRequest,
    ModelToolCall,
    ModelToolDefinition,
    PrivateModelPolicy,
)

from .test_continuation import KEY, seed

IMAGE = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aX1sAAAAASUVORK5CYII="


def policy(protocol):
    return PrivateModelPolicy(uuid4(), uuid4(), "test", protocol, "test", "https://model.invalid/v1?tenant_config=kept",
        uuid4(), 8192, 1024, json.dumps({"supports_tool_calling": True, "supports_images": True,
        "image_token_counting": "openai_responses"}), json.dumps({"protocol": protocol}))


def request(run_id=None):
    return ModelStepRequest(run_id or uuid4(), "media-step", (
        ModelMessage("system", (ModelContent("text", "Rules"),)),
        ModelMessage("user", (ModelContent("text", "Inspect the image"), ModelContent("image", IMAGE))),
    ), (ModelToolDefinition("inspect", "Inspect", '{"type":"object"}'),), 0, 1024, False)


def tool_exchange():
    return (
        ModelMessage("assistant", calls=(ModelToolCall("image-call", "inspect", "{}"), ModelToolCall("second-call", "inspect", "{}"))),
        ModelMessage("tool", (ModelContent("text", "Image result"), ModelContent("image", IMAGE)), call_id="image-call"),
        ModelMessage("tool", (ModelContent("text", "Second result"),), call_id="second-call"),
    )


@pytest.mark.parametrize("protocol", ["openai_chat", "openai_responses", "anthropic", "gemini"])
def test_provider_tool_images_preserve_complete_exchange_and_logical_source(protocol):
    logical = tool_exchange()
    _, payload = build_request(policy(protocol), replace(request(), messages=logical), {})
    assert logical[1].role == "tool" and logical[1].content[1].value == IMAGE
    if protocol == "openai_chat":
        messages = payload["messages"]
        assert [message["role"] for message in messages] == ["assistant", "tool", "tool", "user"]
        assert "base64" not in messages[1]["content"]
        assert messages[2]["tool_call_id"] == "second-call"
        assert "image-call" in messages[3]["content"][0]["text"]
        assert messages[3]["content"][1]["image_url"]["url"] == IMAGE
    elif protocol == "gemini":
        contents = payload["contents"]
        assert len(contents) == 4
        assert "inlineData" not in json.dumps(contents[1])
        assert "functionResponse" in contents[2]["parts"][0]
        assert "image-call" in contents[3]["parts"][0]["text"]
        assert contents[3]["parts"][1]["inlineData"]["data"] == IMAGE.split(",", 1)[1]
    elif protocol == "anthropic":
        block = payload["messages"][1]["content"][0]
        assert block["type"] == "tool_result" and block["tool_use_id"] == "image-call"
        assert block["content"][1]["type"] == "image"
    else:
        block = payload["input"][2]
        assert block["type"] == "function_call_output" and block["call_id"] == "image-call"
        assert block["output"][1] == {"type": "input_image", "image_url": IMAGE}


@pytest.mark.parametrize("protocol,route,field", [
    ("openai_chat", "/responses/input_tokens", "input_tokens"),
    ("openai_responses", "/responses/input_tokens", "input_tokens"),
    ("anthropic", "/messages/count_tokens", "input_tokens"),
    ("gemini", "/models/model:countTokens", "totalTokens"),
])
async def test_count_uses_real_credential_without_holding_transaction_or_generating(
    test_database, protocol, route, field,
):
    principal, model_id, run_id, keyring = await seed(test_database, protocol)
    requests = []

    def respond(outbound):
        assert test_database.engine.pool.checkedout() == 0
        requests.append(outbound)
        assert outbound.url.path.endswith(route)
        assert outbound.url.params["tenant_config"] == "kept"
        assert outbound.extensions["timeout"]["read"] == 10.0
        body = json.loads(outbound.content)
        assert "stream" not in body and "max_tokens" not in body and "max_output_tokens" not in body
        if protocol == "gemini":
            assert outbound.headers["x-goog-api-key"] == "actual-secret"
            assert body["generateContentRequest"]["model"] == "models/model"
            assert "systemInstruction" in body["generateContentRequest"]
        elif protocol == "anthropic":
            assert outbound.headers["x-api-key"] == "actual-secret"
            assert "system" in body and "tools" in body
        else:
            assert outbound.headers["authorization"] == "Bearer actual-secret"
            assert "input" in body and "tools" in body
        assert "image" in json.dumps(body) or "inlineData" in json.dumps(body)
        assert "cookie" not in outbound.headers
        return httpx.Response(200, json={field: 123}, headers={"set-cookie": "secret-cookie=yes"})

    async with create_stateless_http_client(transport=httpx.MockTransport(respond)) as client:
        client.headers["x-not-allowed"] = "shared"
        execution = ModelExecutionService(test_database.sessions, http_client=client, credential_keyring=keyring,
            continuation_keys={"v1": KEY}, active_continuation_key="v1")
        resolved = await execution.resolve_policy(tenant_id=principal.tenant_id, model_id=model_id, protocol=protocol)
        captured = replace(resolved.policy, capabilities_json=policy(protocol).capabilities_json,
            endpoint=resolved.policy.endpoint + "?tenant_config=kept")
        assert await execution.count_input_tokens(captured, request(run_id)) == 123
        assert len(requests) == 1 and "x-not-allowed" not in requests[0].headers
        assert not list(client.cookies.jar)
    async with test_database.sessions() as session:
        assert await session.scalar(select(ProviderContinuationRecord)) is None


@pytest.mark.parametrize("value", [None, True, -1, 1.5, "123", 2**63, float("nan")])
async def test_counter_rejects_invalid_token_values_without_disclosing_body(value):
    async with create_stateless_http_client(transport=httpx.MockTransport(
        lambda _: httpx.Response(200, content=json.dumps({"input_tokens": value, "secret": "never disclose"}).encode())
    )) as client:
        with pytest.raises(ProviderFailure) as error:
            await count_input_tokens(client, policy("anthropic"), request(), "key", {}, ModelLimits())
    assert error.value.code == "protocol_error" and "never disclose" not in str(error.value)


@pytest.mark.parametrize("status,code", [(404, "image_budget_unavailable"), (429, "rate_limited"),
    (503, "provider_unavailable"), (401, "provider_rejected")])
async def test_counter_errors_never_fallback_or_echo_provider_data(status, code):
    calls = []

    def respond(outbound):
        calls.append(outbound)
        return httpx.Response(status, text="private credential message")

    async with create_stateless_http_client(transport=httpx.MockTransport(respond)) as client:
        with pytest.raises(ProviderFailure) as error:
            await count_input_tokens(client, policy("openai_responses"), request(), "key", {}, ModelLimits())
    assert error.value.code == code and len(calls) == 1
    assert "private credential message" not in str(error.value)


@pytest.mark.parametrize("descriptor,code", [
    ({"type": "rate_limit_error"}, "rate_limited"),
    ({"code": "rate_limit_exceeded"}, "rate_limited"),
    ({"type": "overloaded_error"}, "provider_unavailable"),
    ({"code": "server_error"}, "provider_unavailable"),
    ({"type": "internal_server_error"}, "provider_unavailable"),
    ({"status": 429}, "rate_limited"),
    ({"status_code": 503}, "provider_unavailable"),
    ({"code": 429}, "rate_limited"),
    ({"type": "authentication_error", "message": "rate limit secret"}, "provider_error"),
])
@pytest.mark.parametrize("stream", [False, True])
async def test_structured_provider_errors_classify_without_guessing_messages(descriptor, code, stream):
    payload = {"type": "error", "error": {**descriptor, "private": "must-not-appear"}}
    body = ("data: " + json.dumps(payload) + "\n\n").encode() if stream else json.dumps(payload).encode()
    async with create_stateless_http_client(transport=httpx.MockTransport(lambda _: httpx.Response(200, content=body))) as client:
        with pytest.raises(ProviderFailure) as error:
            await execute(client, policy("anthropic"), replace(request(), stream=stream), "key", {}, ModelLimits(), None)
    assert error.value.code == code and "must-not-appear" not in str(error.value)
    assert "secret" not in str(error.value)


async def test_responses_failed_stream_uses_nested_structured_error():
    frame = {"type": "response.failed", "response": {"status": "failed", "error": {"code": "server_error", "message": "private"}}}
    async with create_stateless_http_client(transport=httpx.MockTransport(
        lambda _: httpx.Response(200, content=("data: " + json.dumps(frame) + "\n\n").encode())
    )) as client:
        with pytest.raises(ProviderFailure) as error:
            await execute(client, policy("openai_responses"), replace(request(), stream=True), "key", {}, ModelLimits(), None)
    assert error.value.code == "provider_unavailable" and "private" not in str(error.value)


@pytest.mark.parametrize("protocol", ["openai_chat", "openai_responses", "anthropic", "gemini"])
async def test_counter_encodes_images_from_tool_results_as_actual_media(protocol):
    def respond(outbound):
        payload = json.loads(outbound.content)
        if protocol in {"openai_chat", "openai_responses"}:
            inputs = payload["input"]
            if protocol == "openai_chat":
                assert inputs[2]["type"] == inputs[3]["type"] == "function_call_output"
                assert inputs[4]["role"] == "user"
                assert inputs[4]["content"][1] == {"type": "input_image", "image_url": IMAGE}
            else:
                assert inputs[2]["output"][1] == {"type": "input_image", "image_url": IMAGE}
        elif protocol == "anthropic":
            assert payload["messages"][1]["content"][0]["content"][1]["type"] == "image"
        else:
            parts = payload["generateContentRequest"]["contents"][3]["parts"]
            assert parts[1]["inlineData"]["data"] == IMAGE.split(",", 1)[1]
        return httpx.Response(200, json={"input_tokens": 0, "totalTokens": 0})

    async with create_stateless_http_client(transport=httpx.MockTransport(respond)) as client:
        assert await count_input_tokens(client, policy(protocol), replace(request(), messages=tool_exchange()), "key", {}, ModelLimits()) == 0


@pytest.mark.parametrize("limit", ["request_bytes", "event_bytes", "response_bytes"])
async def test_count_applies_complete_request_and_response_bounds(limit):
    calls = []

    def respond(outbound):
        calls.append(outbound)
        return httpx.Response(200, json={"input_tokens": 123})

    async with create_stateless_http_client(transport=httpx.MockTransport(respond)) as client:
        with pytest.raises(ProviderFailure, match="bound"):
            await count_input_tokens(client, policy("anthropic"), request(), "key", {}, replace(ModelLimits(), **{limit: 1}))
    assert len(calls) == (0 if limit == "request_bytes" else 1)


@pytest.mark.parametrize("protocol", ["anthropic", "openai_responses", "gemini", "openai_chat"])
async def test_count_preserves_required_replay_and_does_not_mutate_state(test_database, protocol):
    principal, model_id, run_id, keyring = await seed(test_database, protocol)
    items = {
        "anthropic": [{"type": "thinking", "thinking": "thought", "signature": "exact-signature"}],
        "openai_responses": [{"type": "reasoning", "id": "r1", "summary": [], "encrypted_content": "exact-signature"}],
        "gemini": [{"text": "thought", "thoughtSignature": "exact-signature"}],
        "openai_chat": [{"reasoning_content": "exact-signature"}],
    }[protocol]
    calls = []

    def respond(outbound):
        calls.append(outbound)
        assert b"exact-signature" in outbound.content
        return httpx.Response(200, json={"input_tokens": 12, "totalTokens": 12})

    async with create_stateless_http_client(transport=httpx.MockTransport(respond)) as client:
        execution = ModelExecutionService(test_database.sessions, http_client=client, credential_keyring=keyring,
            continuation_keys={"v1": KEY}, active_continuation_key="v1")
        resolved = await execution.resolve_policy(tenant_id=principal.tenant_id, model_id=model_id, protocol=protocol)
        captured = replace(resolved.policy, capabilities_json=policy(protocol).capabilities_json)
        await execution._continuation.save(principal.tenant_id, run_id, model_id, protocol, {"previous": items})
        async with test_database.sessions() as session:
            before = (await session.scalar(select(ProviderContinuationRecord))).encrypted_payload
        counted = await execution.count_input_tokens(captured, replace(request(run_id), messages=(
            ModelMessage("assistant", interaction_id="previous", requires_continuation=True), *request().messages[1:],
        )))
        if protocol == "openai_chat":
            assert isinstance(counted, ModelFailure) and counted.code == "image_budget_unavailable"
            assert not calls
        else:
            assert counted == 12 and len(calls) == 1
        async with test_database.sessions() as session:
            after = (await session.scalar(select(ProviderContinuationRecord))).encrypted_payload
        assert before == after


async def test_token_count_cache_directives_do_not_mutate_replay_values():
    state = {"previous": [{"type": "thinking", "thinking": "thought", "signature": "exact"}]}
    original = deepcopy(state)
    messages = (ModelMessage("assistant", interaction_id="previous", requires_continuation=True, cache_boundary=True),
        *request().messages[1:])
    async with create_stateless_http_client(transport=httpx.MockTransport(lambda _: httpx.Response(200, json={"input_tokens": 20}))) as client:
        assert await count_input_tokens(client, policy("anthropic"), replace(request(), messages=messages), "key", state, ModelLimits()) == 20
    assert state == original


async def test_unknown_chat_image_counter_does_not_call_http(test_database):
    principal, model_id, run_id, keyring = await seed(test_database, "openai_chat")
    async with create_stateless_http_client(transport=httpx.MockTransport(lambda _: pytest.fail("no implicit counter"))) as client:
        execution = ModelExecutionService(test_database.sessions, http_client=client, credential_keyring=keyring,
            continuation_keys={"v1": KEY}, active_continuation_key="v1")
        resolved = await execution.resolve_policy(tenant_id=principal.tenant_id, model_id=model_id, protocol="openai_chat")
        captured = replace(resolved.policy, capabilities_json='{"supports_images":true}')
        result = await execution.count_input_tokens(captured, request(run_id))
        assert isinstance(result, ModelFailure) and result.code == "image_budget_unavailable"


async def test_count_missing_required_replay_fails_before_http(test_database):
    principal, model_id, run_id, keyring = await seed(test_database)
    async with create_stateless_http_client(transport=httpx.MockTransport(lambda _: pytest.fail("no counter without replay"))) as client:
        execution = ModelExecutionService(test_database.sessions, http_client=client, credential_keyring=keyring,
            continuation_keys={"v1": KEY}, active_continuation_key="v1")
        resolved = await execution.resolve_policy(tenant_id=principal.tenant_id, model_id=model_id, protocol="anthropic")
        result = await execution.count_input_tokens(resolved.policy, replace(request(run_id), messages=(
            ModelMessage("assistant", interaction_id="missing", requires_continuation=True),
            ModelMessage("user", (ModelContent("text", "continue"),)),
        )))
        assert isinstance(result, ModelFailure) and result.code == "continuation_unavailable" and result.unrecoverable


async def test_count_uses_smaller_model_deadline_and_closes_timed_out_response(test_database):
    principal, model_id, run_id, keyring = await seed(test_database)

    class Stream(httpx.AsyncByteStream):
        closed = False

        async def __aiter__(self):
            await asyncio.Event().wait()
            yield b"unreachable"

        async def aclose(self):
            self.closed = True

    stream = Stream()
    def respond(outbound):
        assert outbound.extensions["timeout"]["read"] == .05
        return httpx.Response(200, stream=stream)

    async with create_stateless_http_client(transport=httpx.MockTransport(respond)) as client:
        execution = ModelExecutionService(test_database.sessions, http_client=client, credential_keyring=keyring,
            continuation_keys={"v1": KEY}, active_continuation_key="v1", limits=ModelLimits(timeout_seconds=.05))
        resolved = await execution.resolve_policy(tenant_id=principal.tenant_id, model_id=model_id, protocol="anthropic")
        captured = replace(resolved.policy, capabilities_json=policy("anthropic").capabilities_json)
        result = await execution.count_input_tokens(captured, request(run_id))
        assert isinstance(result, ModelFailure) and result.code == "transport_failed" and not result.unrecoverable
    assert stream.closed and test_database.engine.pool.checkedout() == 0


async def test_count_cancellation_closes_response_and_preserves_database_state(test_database):
    principal, model_id, run_id, keyring = await seed(test_database)
    entered = asyncio.Event()

    class Stream(httpx.AsyncByteStream):
        closed = False

        async def __aiter__(self):
            entered.set()
            await asyncio.Event().wait()
            yield b"unreachable"

        async def aclose(self):
            self.closed = True

    stream = Stream()
    async with create_stateless_http_client(transport=httpx.MockTransport(lambda _: httpx.Response(200, stream=stream))) as client:
        execution = ModelExecutionService(test_database.sessions, http_client=client, credential_keyring=keyring,
            continuation_keys={"v1": KEY}, active_continuation_key="v1")
        resolved = await execution.resolve_policy(tenant_id=principal.tenant_id, model_id=model_id, protocol="anthropic")
        captured = replace(resolved.policy, capabilities_json=policy("anthropic").capabilities_json)
        task = asyncio.create_task(execution.count_input_tokens(captured, request(run_id)))
        try:
            await asyncio.wait_for(entered.wait(), 2)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    assert stream.closed and test_database.engine.pool.checkedout() == 0
