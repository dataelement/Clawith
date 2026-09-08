"""Real Context/Model summary budget integration below a controlled HTTP peer."""

import json
from dataclasses import replace

import httpx
import pytest
from modules.model.test_continuation import seed, service, successful_probe
from modules.run.test_snapshot import snapshot

from app.execution_dependencies.runtime import ModelSummarizer
from app.infrastructure.errors import InvalidInput
from app.infrastructure.http import create_stateless_http_client
from app.infrastructure.transactions import TransactionContext
from app.modules.context.public import ContextAssembler, ContextBudgetExceeded, ContextSource, ContextState, ContextUnit
from app.modules.model.public import ModelContent, ModelHardLimits, ModelMessage, ModelService
from app.modules.run.public import RunService

SUMMARY = {
    "objective": "Complete the work", "constraints": "", "progress": "Earlier work reviewed",
    "decisions": "", "unresolved": "", "next_actions": "Continue", "references": "",
}


def peer(database, captured, summary_text=None):
    def respond(request):
        assert database.engine.pool.checkedout() == 0
        if request.method == "GET":
            return httpx.Response(404)
        data = json.loads(request.content)
        if data.get("tools"):
            return httpx.Response(200, json=successful_probe("anthropic"))
        captured.append(data)
        return httpx.Response(200, json={"stop_reason": "end_turn", "content": [
            {"type": "text", "text": json.dumps(SUMMARY) if summary_text is None else summary_text},
        ]})
    return respond


async def configured_snapshot(database, model, principal, model_id, run_id, settings):
    async with database.sessions.begin() as session:
        configured = await ModelService(TransactionContext(session)).get(principal, model_id=model_id)
    accepted = await model.validate_configuration(
        tenant_id=principal.tenant_id, credential_id=configured.credential_id, provider=configured.provider,
        protocol="anthropic", model_name=configured.model_name, endpoint=configured.endpoint,
        administrator_limits=ModelHardLimits(8192, 2048), settings=settings, capabilities=configured.capabilities,
    )
    async with database.sessions.begin() as session:
        tx = TransactionContext(session)
        await ModelService(tx).update(principal, model_id=model_id, output_limit=2048,
            settings=settings, acceptance=accepted)
        run = await RunService(tx).get(tenant_id=principal.tenant_id, run_id=run_id)
    resolved = await model.resolve_policy(tenant_id=principal.tenant_id, model_id=model_id, protocol="anthropic")
    return replace(snapshot(principal.tenant_id, run.agent_id, run_id), model=resolved)


async def test_previously_fitting_context_can_compact_after_a_small_addition(test_database):
    principal, model_id, run_id, keyring = await seed(test_database)
    captured = []
    async with create_stateless_http_client(transport=httpx.MockTransport(peer(test_database, captured))) as http:
        model = service(test_database, http, keyring)
        fixed = await configured_snapshot(test_database, model, principal, model_id, run_id,
            {"protocol": "anthropic"})
        assembler = ContextAssembler(sources=(ContextSource("Platform", "p" * 100, "system"),),
            profile=fixed.model.profile, summarizer=ModelSummarizer(model, fixed))
        first = await assembler.prepare(state=ContextState(), additions=(
            ContextUnit(1, (ModelMessage("user", (ModelContent("text", "x" * 5300),)),)),
        ), tools=())
        assert first.input_tokens <= 8192 - 2048
        result = await assembler.prepare(state=first.state, additions=(
            ContextUnit(2, (ModelMessage("user", (ModelContent("text", "y" * 600),)),)),
        ), tools=())
    assert result.telemetry.compactions == 1
    assert len(captured) == 1 and captured[0]["max_tokens"] == 2048
    assert "x" * 5300 in captured[0]["messages"][0]["content"][0]["text"]
    assert result.input_tokens + result.output_tokens <= 8192


async def test_small_summary_target_does_not_reduce_thinking_allowance(test_database):
    principal, model_id, run_id, keyring = await seed(test_database)
    captured = []
    async with create_stateless_http_client(transport=httpx.MockTransport(peer(test_database, captured))) as http:
        model = service(test_database, http, keyring)
        fixed = await configured_snapshot(test_database, model, principal, model_id, run_id,
            {"protocol": "anthropic", "thinking": {"type": "enabled", "budget_tokens": 1024}})
        result = await ModelSummarizer(model, fixed).summarize(previous=None,
            units=(ContextUnit(1, (ModelMessage("user", (ModelContent("text", "Earlier work"),)),)),),
            sources=(ContextSource("Platform", "Be accurate", "system"),), max_tokens=500)
    assert result.objective == SUMMARY["objective"]
    assert len(captured) == 1
    assert captured[0]["thinking"]["budget_tokens"] == 1024
    assert captured[0]["max_tokens"] == 2048
    assert "500" in captured[0]["system"][0]["text"]


@pytest.mark.parametrize("text", [
    "private-invalid-output", json.dumps({"objective": "private-invalid-output"}),
    json.dumps({**SUMMARY, "progress": ["private-invalid-output"]}),
])
async def test_malformed_structured_summary_fails_without_exposing_response(test_database, text):
    principal, model_id, run_id, keyring = await seed(test_database)
    captured = []
    async with create_stateless_http_client(transport=httpx.MockTransport(peer(test_database, captured, text))) as http:
        model = service(test_database, http, keyring)
        fixed = await configured_snapshot(test_database, model, principal, model_id, run_id,
            {"protocol": "anthropic"})
        with pytest.raises(InvalidInput) as error:
            await ModelSummarizer(model, fixed).summarize(previous=None,
                units=(ContextUnit(1, (ModelMessage("user", (ModelContent("text", "Earlier work"),)),)),),
                sources=(ContextSource("Platform", "Be accurate", "system"),), max_tokens=500)
    assert len(captured) == 1
    assert "private-invalid-output" not in str(error.value)


async def test_valid_but_oversized_summary_cannot_replace_a_fitting_prior_state(test_database):
    principal, model_id, run_id, keyring = await seed(test_database)
    captured = []
    oversized = json.dumps({**SUMMARY, "objective": "中" * 12000})
    async with create_stateless_http_client(transport=httpx.MockTransport(peer(test_database, captured, oversized))) as http:
        model = service(test_database, http, keyring)
        fixed = await configured_snapshot(test_database, model, principal, model_id, run_id,
            {"protocol": "anthropic"})
        assembler = ContextAssembler(sources=(ContextSource("Platform", "p" * 100, "system"),),
            profile=fixed.model.profile, summarizer=ModelSummarizer(model, fixed))
        first = await assembler.prepare(state=ContextState(), additions=(
            ContextUnit(1, (ModelMessage("user", (ModelContent("text", "x" * 5300),)),)),
        ), tools=())
        with pytest.raises(ContextBudgetExceeded):
            await assembler.prepare(state=first.state, additions=(
                ContextUnit(2, (ModelMessage("user", (ModelContent("text", "y" * 600),)),)),
            ), tools=())
    assert len(captured) == 1
    assert first.state.summary is None and first.state.through_sequence == 1
    assert first.state.units[0].messages[0].content[0].value == "x" * 5300
