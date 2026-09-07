"""One-shot summaries do not consume or replace the Run's real replay state."""

import asyncio
import json
from dataclasses import replace

import httpx
import pytest
from sqlalchemy import select

from app.infrastructure.http import create_stateless_http_client
from app.modules.model.continuation import ContinuationStore
from app.modules.model.models import ProviderContinuationRecord
from app.modules.model.public import (
    ModelContent,
    ModelFailure,
    ModelMessage,
    ModelStepRequest,
    ModelStepResult,
    ModelToolCall,
    ModelToolDefinition,
)

from .test_continuation import KEY, seed, service, signed


async def stored_replay(database, tenant, run, model):
    async with database.sessions() as session:
        records = (await session.scalars(select(ProviderContinuationRecord).where(
            ProviderContinuationRecord.tenant_id == tenant,
            ProviderContinuationRecord.run_id == run,
            ProviderContinuationRecord.model_id == model,
        ))).all()
        return tuple(tuple(getattr(record, column.key) for column in record.__table__.columns) for record in records)


def summary_request(run):
    return ModelStepRequest(run, "summary-only", (
        ModelMessage("system", (ModelContent("text", "Summarize supplied work."),)),
        ModelMessage("user", (ModelContent("text", "An earlier operation finished."),)),
    ), (), 20, 100, False)


@pytest.mark.parametrize("outcome", ["success", "failure", "cancel"])
async def test_summary_leaves_actual_run_continuation_unchanged_and_replayable(test_database, outcome):
    principal, model_id, run_id, keyring = await seed(test_database)
    replay = [
        {"type": "thinking", "thinking": "retained reasoning", "signature": "retained-signature"},
        {"type": "text", "text": "prior answer"},
    ]
    store = ContinuationStore(test_database.sessions, keys={"v1": KEY}, active_key="v1", max_bytes=65536)
    await store.save(principal.tenant_id, run_id, model_id, "anthropic", {"prior": replay})
    before = await stored_replay(test_database, principal.tenant_id, run_id, model_id)
    entered, cancelled = asyncio.Event(), asyncio.Event()
    requests = []

    async def peer(request):
        assert test_database.engine.pool.checkedout() == 0
        assert request.headers["x-api-key"] == "actual-secret"
        payload = json.loads(request.content)
        requests.append(payload)
        if len(requests) == 1:
            assert not payload.get("tools")
            assert "retained-signature" not in request.content.decode()
            if outcome == "failure":
                return httpx.Response(500, text="private-provider-body")
            if outcome == "cancel":
                entered.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    cancelled.set()
            return httpx.Response(200, json=signed())
        assert payload["messages"][1]["content"] == replay
        return httpx.Response(200, json=signed())

    async with create_stateless_http_client(transport=httpx.MockTransport(peer)) as http:
        model = service(test_database, http, keyring)
        resolved = await model.resolve_policy(tenant_id=principal.tenant_id, model_id=model_id, protocol="anthropic")
        if outcome == "cancel":
            task = asyncio.create_task(model.execute_summary(resolved.policy, summary_request(run_id)))
            try:
                await asyncio.wait_for(entered.wait(), 2)
            finally:
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
            assert cancelled.is_set()
        else:
            result = await model.execute_summary(resolved.policy, summary_request(run_id))
            if outcome == "success":
                assert isinstance(result, ModelStepResult)
                assert result.content == "answer" and not result.calls and not result.requires_continuation
            else:
                assert isinstance(result, ModelFailure)
                assert "private-provider-body" not in repr(result)
        assert await stored_replay(test_database, principal.tenant_id, run_id, model_id) == before
        continued = await model.execute_step(resolved.policy, ModelStepRequest(run_id, "after-summary", (
            ModelMessage("user", (ModelContent("text", "original task"),)),
            ModelMessage("assistant", (ModelContent("text", "prior answer"),),
                interaction_id="prior", requires_continuation=True),
            ModelMessage("user", (ModelContent("text", "continue"),)),
        ), (), 20, 100, False))
        assert isinstance(continued, ModelStepResult) and continued.requires_continuation
    assert len(requests) == 2 and http.is_closed
    assert (await store.load(principal.tenant_id, run_id, model_id, "anthropic"))["prior"] == replay


@pytest.mark.parametrize("change", ["stream", "tools", "assistant", "tool", "call", "identity", "replay", "image"])
async def test_summary_rejects_execution_inputs_before_http(test_database, change):
    principal, model_id, run_id, keyring = await seed(test_database)
    request = summary_request(run_id)
    if change == "stream":
        request = replace(request, stream=True)
    elif change == "tools":
        request = replace(request, tools=(ModelToolDefinition("tool", "tool", '{}'),))
    else:
        message = request.messages[-1]
        options = {
            "assistant": {"role": "assistant"}, "tool": {"role": "tool", "call_id": "call"},
            "call": {"calls": (ModelToolCall("call", "tool", '{}'),)},
            "identity": {"interaction_id": "prior"}, "replay": {"requires_continuation": True},
            "image": {"content": (ModelContent("image", "data:image/png;base64,aA=="),)},
        }
        request = replace(request, messages=(request.messages[0], replace(message, **options[change])))
    async with create_stateless_http_client(transport=httpx.MockTransport(lambda _: pytest.fail("Unexpected HTTP"))) as http:
        model = service(test_database, http, keyring)
        policy = (await model.resolve_policy(tenant_id=principal.tenant_id, model_id=model_id, protocol="anthropic")).policy
        result = await model.execute_summary(policy, request)
    assert isinstance(result, ModelFailure) and result.code == "invalid_summary_request"
    assert await stored_replay(test_database, principal.tenant_id, run_id, model_id) == ()


@pytest.mark.parametrize("output", [
    {"stop_reason": "max_tokens", "content": [{"type": "text", "text": "partial"}]},
    {"stop_reason": "tool_use", "content": [{"type": "tool_use", "id": "call", "name": "ungranted", "input": {}}]},
])
async def test_summary_never_accepts_truncation_or_tool_calls(test_database, output):
    principal, model_id, run_id, keyring = await seed(test_database)
    async with create_stateless_http_client(transport=httpx.MockTransport(lambda _: httpx.Response(200, json=output))) as http:
        model = service(test_database, http, keyring)
        policy = (await model.resolve_policy(tenant_id=principal.tenant_id, model_id=model_id, protocol="anthropic")).policy
        result = await model.execute_summary(policy, summary_request(run_id))
    assert isinstance(result, ModelFailure)
    assert await stored_replay(test_database, principal.tenant_id, run_id, model_id) == ()
