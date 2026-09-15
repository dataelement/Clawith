"""Model execution integrates actual Credential and encrypted PostgreSQL state."""

import asyncio
import json
import os
from dataclasses import replace
from datetime import UTC, datetime
from uuid import uuid4

import httpx
import pytest
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.infrastructure.errors import InvalidInput
from app.infrastructure.http import create_stateless_http_client
from app.infrastructure.transactions import TransactionContext
from app.modules.agent.public import AgentService
from app.modules.credential.public import CredentialKeyring, CredentialService, Secret
from app.modules.identity_tenant.public import IdentityService, TenantPrincipal
from app.modules.model.continuation import ContinuationStore
from app.modules.model.execution import ProviderFailure
from app.modules.model.models import ModelRecord, ProviderContinuationRecord
from app.modules.model.public import (
    ModelCatalogEntry,
    ModelContent,
    ModelExecutionService,
    ModelFailure,
    ModelHardLimits,
    ModelMessage,
    ModelService,
    ModelStepRequest,
    ModelStepResult,
    _validate_configuration,
)
from app.modules.run.models import RunRecord

KEY = b"k" * 32


def successful_probe(protocol):
    if protocol == "openai_chat":
        return {"choices": [{"finish_reason": "tool_calls", "message": {"tool_calls": [
            {"id": "probe", "function": {"name": "capability_probe", "arguments": '{"value":"ok"}'}},
        ]}}]}
    if protocol == "openai_responses":
        return {"status": "completed", "output": [{"type": "function_call", "call_id": "probe",
            "name": "capability_probe", "arguments": '{"value":"ok"}'}]}
    if protocol == "gemini":
        return {"candidates": [{"finishReason": "STOP", "content": {"parts": [
            {"functionCall": {"name": "capability_probe", "args": {"value": "ok"}}},
        ]}}]}
    return {"stop_reason": "tool_use", "content": [
        {"type": "tool_use", "id": "probe", "name": "capability_probe", "input": {"value": "ok"}}]}


@pytest.mark.parametrize("protocol,options,output_limit", [
    ("anthropic", {"thinking": {"type": "enabled", "budget_tokens": 1024}}, 2048),
    ("openai_chat", {"reasoning_effort": "high"}, 2048),
    ("openai_responses", {"reasoning": {"effort": "high"}}, 2048),
    ("gemini", {}, 2048),
    ("anthropic", {}, 128), ("openai_chat", {}, 128),
    ("openai_responses", {}, 128), ("gemini", {}, 128),
])
async def test_configuration_probe_preserves_output_and_reasoning_configuration(
    test_database, protocol, options, output_limit,
):
    principal, model_id, _, keyring = await seed(test_database, protocol)
    async with test_database.sessions.begin() as session:
        model = await ModelService(TransactionContext(session)).get(principal, model_id=model_id)
    settings = {"protocol": protocol, **options}
    captured = []

    def respond(request):
        assert test_database.engine.pool.checkedout() == 0
        if request.method == "GET":
            return httpx.Response(404)
        body = json.loads(request.content)
        budget = (body["generationConfig"]["maxOutputTokens"] if protocol == "gemini"
                  else body["max_output_tokens"] if protocol == "openai_responses" else body["max_tokens"])
        assert budget == output_limit
        for name, value in options.items():
            assert body[name] == value
        if "thinking" in options:
            assert budget > body["thinking"]["budget_tokens"]
        captured.append(body)
        return httpx.Response(200, json=successful_probe(protocol))

    async with create_stateless_http_client(transport=httpx.MockTransport(respond)) as client:
        accepted = await service(test_database, client, keyring).validate_configuration(
            tenant_id=principal.tenant_id, credential_id=model.credential_id, provider=model.provider,
            protocol=protocol, model_name=model.model_name, endpoint=model.endpoint,
            administrator_limits=ModelHardLimits(8192, output_limit), settings=settings, capabilities=model.capabilities,
        )
    assert len(captured) == 1 and json.loads(accepted.settings_json) == settings
    async with test_database.sessions.begin() as session:
        updated = await ModelService(TransactionContext(session)).update(principal, model_id=model_id,
            settings=settings, output_limit=output_limit, acceptance=accepted)
        assert updated.settings == settings and updated.output_limit == output_limit


@pytest.mark.parametrize("case", [
    "metadata_error", "metadata_invalid", "metadata_identity", "metadata_partial",
    "probe_text", "probe_tool", "probe_arguments", "catalog_mismatch", "no_limits",
])
async def test_invalid_capability_evidence_never_enables_a_draft(test_database, case):
    principal, model_id, _, keyring = await seed(test_database)
    async with test_database.sessions.begin() as session:
        model = await ModelService(TransactionContext(session)).set_enabled(
            principal, model_id=model_id, enabled=False,
        )
    requests = []

    def respond(request):
        assert test_database.engine.pool.checkedout() == 0
        requests.append(request.method)
        if request.method == "GET":
            if case == "metadata_error":
                return httpx.Response(500)
            if case == "metadata_invalid":
                return httpx.Response(200, content=b"not-json")
            if case == "metadata_identity":
                return httpx.Response(200, json={"id": "different", "max_input_tokens": 8192, "max_tokens": 1024})
            if case == "metadata_partial":
                return httpx.Response(200, json={"id": "model", "max_input_tokens": 8192})
            return httpx.Response(404)
        if case == "probe_text":
            return httpx.Response(200, json={"stop_reason": "end_turn", "content": [{"type": "text", "text": "ok"}]})
        return httpx.Response(200, json={"stop_reason": "tool_use", "content": [{
            "type": "tool_use", "id": "probe", "name": "wrong" if case == "probe_tool" else "capability_probe",
            "input": {"value": "wrong"},
        }]})

    catalog = (ModelCatalogEntry("anthropic", "https://different.invalid/v1", "model", ModelHardLimits(8192, 1024)),)
    async with create_stateless_http_client(transport=httpx.MockTransport(respond)) as client:
        execution = ModelExecutionService(
            test_database.sessions, http_client=client, credential_keyring=keyring,
            continuation_keys={"v1": KEY}, active_continuation_key="v1", builtin_catalog=catalog,
        )
        with pytest.raises((InvalidInput, ProviderFailure)):
            await execution.validate_configuration(
                tenant_id=principal.tenant_id, credential_id=model.credential_id, provider="anthropic",
                protocol="anthropic", model_name="model", endpoint=model.endpoint,
                administrator_limits=None if case in {"catalog_mismatch", "no_limits"} else ModelHardLimits(8192, 1024),
                settings=model.settings, capabilities=model.capabilities,
            )
    assert requests == (["GET", "POST"] if case.startswith("probe_") else ["GET"])
    async with test_database.sessions.begin() as session:
        assert not (await ModelService(TransactionContext(session)).get(principal, model_id=model_id)).enabled


async def test_protocol_is_owned_by_configuration_not_execution_caller(test_database):
    principal, model_id, _, keyring = await seed(test_database)
    async with test_database.sessions.begin() as session:
        model = await ModelService(TransactionContext(session)).get(principal, model_id=model_id)
    async with create_stateless_http_client(transport=httpx.MockTransport(lambda _: pytest.fail("no HTTP"))) as client:
        execution = service(test_database, client, keyring)
        with pytest.raises(InvalidInput, match="protocol"):
            await execution.resolve_policy(tenant_id=principal.tenant_id, model_id=model_id, protocol="openai_chat")
        with pytest.raises(InvalidInput, match="protocol"):
            await execution.validate_configuration(
                tenant_id=principal.tenant_id, credential_id=model.credential_id, provider=model.provider,
                protocol="openai_chat", model_name=model.model_name, endpoint=model.endpoint,
                administrator_limits=ModelHardLimits(8192, 1024), settings=model.settings, capabilities=model.capabilities,
            )


async def test_archiving_an_enabled_default_model_prevents_new_selection(test_database):
    principal, model_id, _, _ = await seed(test_database)
    async with test_database.sessions.begin() as session:
        models = ModelService(TransactionContext(session))
        await models.set_default(principal, model_id=model_id)
        archived = await models.archive(principal, model_id=model_id)
        assert not archived.enabled and archived.archived_at is not None
        with pytest.raises(InvalidInput):
            await AgentService(TransactionContext(session)).create(
                principal, name="New", soul="Useful", timezone="UTC",
            )


@pytest.mark.parametrize("source", ["provider_metadata", "builtin_catalog", "administrator"])
async def test_configuration_acceptance_resolves_limits_before_enablement(test_database, source):
    principal, model_id, _, keyring = await seed(test_database)
    async with test_database.sessions.begin() as session:
        model = await ModelService(TransactionContext(session)).get(principal, model_id=model_id)
    requests = []

    def respond(request):
        assert test_database.engine.pool.checkedout() == 0
        requests.append(request)
        if request.method == "GET":
            if source == "provider_metadata":
                return httpx.Response(200, json={"id": "model", "max_input_tokens": 16384, "max_tokens": 2048})
            return httpx.Response(404)
        return httpx.Response(200, json={"stop_reason": "tool_use", "content": [
            {"type": "tool_use", "id": "probe", "name": "capability_probe", "input": {"value": "ok"}},
        ]})

    catalog = (() if source == "administrator" else (
        ModelCatalogEntry("anthropic", model.endpoint, "model", ModelHardLimits(12288, 1536)),
    ))
    async with create_stateless_http_client(transport=httpx.MockTransport(respond)) as client:
        execution = ModelExecutionService(
            test_database.sessions, http_client=client, credential_keyring=keyring,
            continuation_keys={"v1": KEY}, active_continuation_key="v1", builtin_catalog=catalog,
        )
        accepted = await execution.validate_configuration(
            tenant_id=principal.tenant_id, credential_id=model.credential_id, provider="anthropic",
            protocol="anthropic", model_name="model", endpoint=model.endpoint,
            administrator_limits=ModelHardLimits(8192, 1024), settings=model.settings, capabilities=model.capabilities,
        )
    assert accepted.capability_source == source
    assert accepted.limits.context_limit == {
        "provider_metadata": 16384, "builtin_catalog": 12288, "administrator": 8192,
    }[source]
    assert [request.method for request in requests] == ["GET", "POST"]
    async with test_database.sessions.begin() as session:
        models = ModelService(TransactionContext(session))
        with pytest.raises(InvalidInput, match="acceptance"):
            await models.set_enabled(principal, model_id=model_id, enabled=True)
        with pytest.raises(InvalidInput, match="acceptance"):
            await models.create(
                principal, credential_id=model.credential_id, provider=model.provider,
                model_name=model.model_name, endpoint=model.endpoint,
                context_limit=accepted.limits.context_limit, output_limit=accepted.limits.output_limit,
                capability_source=source, capabilities=model.capabilities,
                settings_version=1, settings={"protocol": "openai_chat"}, acceptance=accepted,
            )
        with pytest.raises(InvalidInput, match="acceptance"):
            await models.update(principal, model_id=model_id, settings={"protocol": "openai_chat"}, acceptance=accepted)
        updated = await models.update(
            principal, model_id=model_id, context_limit=accepted.limits.context_limit,
            output_limit=accepted.limits.output_limit, capability_source=source, acceptance=accepted,
        )
        assert updated.context_limit == accepted.limits.context_limit
        await models.set_enabled(principal, model_id=model_id, enabled=False)
        await models.update(principal, model_id=model_id, settings={"protocol": "openai_chat"})
        with pytest.raises(InvalidInput, match="acceptance"):
            await models.set_enabled(principal, model_id=model_id, enabled=True, acceptance=accepted)


async def seed(database, protocol="anthropic"):
    keyring = CredentialKeyring(active_key_version="v1", keys={"v1": KEY})
    async with database.sessions.begin() as session:
        tx = TransactionContext(session)
        identities = IdentityService(tx)
        account = await identities.create_account()
        tenant = await identities.create_tenant(name="Models")
        membership = await identities.create_membership(tenant_id=tenant.id, account_id=account.id,
            display_name="Admin", role="tenant_admin")
        principal = TenantPrincipal(account.id, membership.id, tenant.id, "tenant_admin")
        credential = await CredentialService(tx, keyring).create(principal, kind="api_key", provider="anthropic",
            label="test", secret=Secret("actual-secret"), owner_kind="tenant")
        model = await ModelService(tx).create(principal, credential_id=credential.id, provider="anthropic",
            model_name="model", endpoint="https://provider.invalid/v1", context_limit=8192, output_limit=1024,
            capability_source="administrator", capabilities={"supports_tool_calling": True, "supports_streaming": True},
            settings_version=1, settings={"protocol": protocol}, enabled=False)
    def probe_handler(request):
        if request.method == "GET":
            return httpx.Response(404)
        return httpx.Response(200, json=successful_probe(protocol))
    async with create_stateless_http_client(transport=httpx.MockTransport(probe_handler)) as client:
        accepted = await service(database, client, keyring).validate_configuration(
            tenant_id=tenant.id, credential_id=credential.id, provider="anthropic", protocol=protocol,
            model_name="model", endpoint="https://provider.invalid/v1", administrator_limits=ModelHardLimits(8192, 1024),
            settings={"protocol": protocol}, capabilities={"supports_tool_calling": True, "supports_streaming": True})
    async with database.sessions.begin() as session:
        tx = TransactionContext(session)
        await ModelService(tx).set_enabled(principal, model_id=model.id, enabled=True, acceptance=accepted)
        agent = await AgentService(tx).create(principal, name="Agent", soul="Soul", timezone="UTC", model_id=model.id)
        now = datetime.now(UTC)
        run = RunRecord(id=uuid4(), tenant_id=tenant.id, agent_id=agent.id, status="Running", initiator_kind="session",
            initiator_owner_id=uuid4(), source_key="input", created_at=now, started_at=now, updated_at=now)
        session.add(run)
    return principal, model.id, run.id, keyring


def service(database, client, keyring, keys=None):
    return ModelExecutionService(database.sessions, http_client=client, credential_keyring=keyring,
        continuation_keys=keys or {"v1": KEY}, active_continuation_key=next(iter(keys or {"v1": KEY})))


def req(run_id, *, step="s1", messages=None):
    return ModelStepRequest(run_id, step, messages or (ModelMessage("user", (ModelContent("text", "hello"),)),),
                            (), 10, 100, False)


def signed():
    return {"stop_reason": "end_turn", "content": [
        {"type": "thinking", "thinking": "confidential", "signature": "exact-signature"},
        {"type": "text", "text": "answer"}], "usage": {"input_tokens": 3, "output_tokens": 2}}


async def test_durable_encrypted_replay_waiting_and_cleanup(test_database):
    principal, model_id, run_id, keyring = await seed(test_database)
    calls = []
    def handler(request):
        # No pooled DB connection remains checked out while provider transport executes.
        assert test_database.engine.pool.checkedout() == 0
        calls.append(json.loads(request.content))
        assert request.headers["x-api-key"] == "actual-secret"
        return httpx.Response(200, json=signed())
    async with create_stateless_http_client(transport=httpx.MockTransport(handler)) as client:
        execution = service(test_database, client, keyring)
        resolved = await execution.resolve_policy(tenant_id=principal.tenant_id, model_id=model_id, protocol="anthropic")
        assert "endpoint" not in repr(resolved.profile) and "credential" not in repr(resolved.profile)
        first = await execution.execute_step(resolved.policy, req(run_id))
        assert isinstance(first, ModelStepResult) and first.requires_continuation
        assert "confidential" not in repr(first) and "exact-signature" not in repr(first)
        async with test_database.sessions.begin() as session:
            row = await session.scalar(select(ProviderContinuationRecord))
            assert row and b"confidential" not in row.encrypted_payload and b"exact-signature" not in row.encrypted_payload
            run = await session.get(RunRecord, run_id)
            run.status, run.active_waiting_reference = "Waiting", "question"
        resumed = service(test_database, client, keyring)
        second = await resumed.execute_step(resolved.policy, req(run_id, step="s2", messages=(
            ModelMessage("assistant", (ModelContent("text", "answer"),), interaction_id="s1", requires_continuation=True),
            ModelMessage("user", (ModelContent("text", "continue"),)),
        )))
        assert isinstance(second, ModelStepResult)
        assert calls[1]["messages"][0]["content"] == signed()["content"]
        await resumed.release_continuation(tenant_id=principal.tenant_id, run_id=run_id,
                                           model_id=model_id, terminal_status="Completed")
        await resumed.release_continuation(tenant_id=principal.tenant_id, run_id=run_id,
                                           model_id=model_id, terminal_status="Completed")
    async with test_database.sessions() as session:
        assert await session.scalar(select(ProviderContinuationRecord)) is None


@pytest.mark.parametrize("corruption", ["ciphertext", "version", "key", "missing"])
async def test_required_replay_failure_never_calls_provider(test_database, corruption):
    principal, model_id, run_id, keyring = await seed(test_database)
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(200, json=signed())
    async with create_stateless_http_client(transport=httpx.MockTransport(handler)) as client:
        execution = service(test_database, client, keyring)
        resolved = await execution.resolve_policy(tenant_id=principal.tenant_id, model_id=model_id, protocol="anthropic")
        assert isinstance(await execution.execute_step(resolved.policy, req(run_id)), ModelStepResult)
        async with test_database.sessions.begin() as session:
            row = await session.scalar(select(ProviderContinuationRecord))
            if corruption == "ciphertext":
                row.encrypted_payload = b"invalid"
            elif corruption == "version":
                row.payload_schema_version = 99
            elif corruption == "key":
                row.key_version = "missing-key"
            else:
                await session.delete(row)
        result = await execution.execute_step(resolved.policy, req(run_id, step="s2", messages=(
            ModelMessage("assistant", interaction_id="s1", requires_continuation=True),
            ModelMessage("user", (ModelContent("text", "continue"),)),)))
    assert isinstance(result, ModelFailure) and result.unrecoverable and len(calls) == 1


async def test_continuation_commit_failure_does_not_release_result(test_database):
    principal, model_id, _, keyring = await seed(test_database)
    async with create_stateless_http_client(transport=httpx.MockTransport(lambda _: httpx.Response(200, json=signed()))) as client:
        execution = service(test_database, client, keyring)
        resolved = await execution.resolve_policy(tenant_id=principal.tenant_id, model_id=model_id, protocol="anthropic")
        # Actual FK failure when Model tries to commit state for an absent Run.
        result = await execution.execute_step(resolved.policy, req(uuid4()))
    assert isinstance(result, ModelFailure) and result.code == "persistence_failed" and result.unrecoverable


async def test_request_validation_and_explicit_protocol(test_database):
    principal, model_id, run_id, keyring = await seed(test_database)
    async with create_stateless_http_client(transport=httpx.MockTransport(lambda _: pytest.fail("provider must not run"))) as client:
        execution = service(test_database, client, keyring)
        resolved = await execution.resolve_policy(tenant_id=principal.tenant_id, model_id=model_id, protocol="anthropic")
        for invalid in (replace(req(run_id), input_tokens=9000), replace(req(run_id), output_tokens=2000),
            replace(req(run_id), messages=(ModelMessage("tool", call_id="orphan"),))):
            assert isinstance(await execution.execute_step(resolved.policy, invalid), ModelFailure)


async def test_cancelled_provider_call_has_no_continuation(test_database):
    principal, model_id, run_id, keyring = await seed(test_database)
    entered = asyncio.Event()
    async def handler(_):
        entered.set()
        await asyncio.Event().wait()
    async with create_stateless_http_client(transport=httpx.MockTransport(handler)) as client:
        execution = service(test_database, client, keyring)
        resolved = await execution.resolve_policy(tenant_id=principal.tenant_id, model_id=model_id, protocol="anthropic")
        task = asyncio.create_task(execution.execute_step(resolved.policy, req(run_id)))
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    async with test_database.sessions() as session:
        assert await session.scalar(select(ProviderContinuationRecord)) is None


@pytest.mark.parametrize("field,value", [
    ("settings_version", 2), ("settings", ["not-an-object"]),
    ("capabilities", ["not-an-object"]), ("settings", {"api_key": "secret"}),
])
async def test_resolution_rejects_invalid_persisted_configuration(test_database, field, value):
    principal, model_id, _, keyring = await seed(test_database)
    async with test_database.sessions.begin() as session:
        model = await session.get(ModelRecord, model_id)
        setattr(model, field, value)
    async with create_stateless_http_client(transport=httpx.MockTransport(lambda _: pytest.fail("no HTTP"))) as client:
        execution = service(test_database, client, keyring)
        with pytest.raises(InvalidInput):
            await execution.resolve_policy(tenant_id=principal.tenant_id, model_id=model_id, protocol="anthropic")


@pytest.mark.parametrize("source", ["provider_metadata", "builtin_catalog", "administrator", "unknown"])
def test_resolution_shared_validator_capability_source(source):
    arguments = {"context_limit": 8192, "output_limit": 1024, "capability_source": source,
        "capabilities": {"supports_tool_calling": True}, "settings_version": 1,
        "settings": {"protocol": "anthropic"}, "enabled": True}
    if source == "unknown":
        with pytest.raises(InvalidInput, match="capability_source"):
            _validate_configuration(**arguments)
    else:
        _validate_configuration(**arguments)


@pytest.mark.parametrize("protocol,replay", [
    ("openai_chat", [{"reasoning_content": "opaque"}]),
    ("anthropic", [{"type": "thinking", "thinking": "private", "signature": "sig"}]),
    ("openai_responses", [{"type": "reasoning", "id": "r", "encrypted_content": "opaque", "summary": []}]),
    ("gemini", [{"text": "private", "thought": True, "thoughtSignature": "sig"}]),
])
async def test_valid_protocol_replay_survives_postgres_exactly(test_database, protocol, replay):
    principal, model_id, run_id, _ = await seed(test_database)
    store = ContinuationStore(test_database.sessions, keys={"v1": KEY}, active_key="v1", max_bytes=100_000)
    await store.save(principal.tenant_id, run_id, model_id, protocol, {"prior": replay})
    assert await store.load(principal.tenant_id, run_id, model_id, protocol) == {"prior": replay}


async def test_execution_constructor_rejects_stateful_client():
    async with httpx.AsyncClient() as client:
        with pytest.raises(TypeError, match="stateless"):
            ModelExecutionService(async_sessionmaker(), http_client=client,
                credential_keyring=CredentialKeyring(active_key_version="v1", keys={"v1": KEY}),
                continuation_keys={"v1": KEY}, active_continuation_key="v1")


@pytest.mark.parametrize("protocol,replay", [
    ("openai_chat", []), ("openai_chat", [{}]), ("openai_chat", [{"reasoning_content": 1}]),
    ("anthropic", []), ("anthropic", [{"type": "thinking", "thinking": "private"}]),
    ("anthropic", ["not-an-object"]),
    ("openai_responses", []), ("openai_responses", [{"type": "reasoning", "summary": []}]),
    ("gemini", []), ("gemini", [{"thoughtSignature": "sig", "functionCall": {"name": "search"}}]),
])
async def test_invalid_authenticated_replay_is_unrecoverable_before_http(test_database, protocol, replay):
    principal, model_id, run_id, keyring = await seed(test_database, protocol)
    nonce = os.urandom(12)
    encrypted = nonce + AESGCM(KEY).encrypt(nonce, json.dumps({"prior": replay}).encode(),
        ContinuationStore._aad(principal.tenant_id, run_id, model_id, protocol))
    now = datetime.now(UTC)
    async with test_database.sessions.begin() as session:
        session.add(ProviderContinuationRecord(tenant_id=principal.tenant_id, run_id=run_id, model_id=model_id,
            payload_kind=protocol, payload_schema_version=1, encryption_version=1, key_version="v1",
            encrypted_payload=encrypted, created_at=now, updated_at=now))
    async with create_stateless_http_client(transport=httpx.MockTransport(lambda _: pytest.fail("no HTTP"))) as client:
        execution = service(test_database, client, keyring)
        resolved = await execution.resolve_policy(tenant_id=principal.tenant_id, model_id=model_id, protocol=protocol)
        result = await execution.execute_step(resolved.policy, req(run_id, messages=(
            ModelMessage("assistant", interaction_id="prior", requires_continuation=True),
            ModelMessage("user", (ModelContent("text", "continue"),)),)))
    assert isinstance(result, ModelFailure) and result.code == "continuation_unavailable" and result.unrecoverable
