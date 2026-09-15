import asyncio
import json
from dataclasses import replace
from uuid import uuid4

import httpx
import pytest
from database.test_schema_wave_S1 import _seed_to_agent
from modules.run.test_snapshot import snapshot

from app.infrastructure.errors import AccessDenied, Conflict, InvalidInput, NotFound
from app.infrastructure.http import create_stateless_http_client
from app.modules.channel.adapters import SlackAdapter
from app.modules.channel.public import ChannelService, DeliveryContent, DeliveryService
from app.modules.channel.reply_context import ChannelContextCodec
from app.modules.credential.public import CredentialKeyring, CredentialService, Secret
from app.modules.identity_tenant.public import TenantPrincipal
from app.modules.model.public import ModelStepResult, ModelUsage
from app.modules.run.public import InputContent, ModelStepPayload, RunService, SourceIdentity, WaitingPayload
from app.modules.session.public import SessionConsumers, SessionService


def context_codec():
    return ChannelContextCodec(active_key_version="v1", keys={"v1": b"k" * 32})


async def load_message(tx, *, tenant_id, agent_id, kind, message_id):
    assert kind == "session"
    message = await SessionService(tx).get_message_for_delivery(tenant_id=tenant_id, agent_id=agent_id, message_id=message_id)
    return DeliveryContent(message.content.text, tuple(ref.reference for ref in message.content.references))


async def setup(factory, *, question="Need your answer"):
    keyring = CredentialKeyring(active_key_version="v1", keys={"v1":b"k"*32})
    async with factory() as tx:
        seed = await _seed_to_agent(tx.session)
        principal = TenantPrincipal(seed["account"].id, seed["membership"].id, seed["tenant"].id, "tenant_admin")
        agent = seed["agent"].id
        credential = await CredentialService(tx, keyring).create(principal, kind="channel", provider="slack", label="Slack",
            secret=Secret(json.dumps({"version":1,"token":"bot-secret","signing_secret":"sign-secret"})), owner_kind="agent", owner_id=agent)
        channel = await ChannelService(tx).configure(principal, agent_id=agent, provider="slack", external_identity="T1:A1", credential_id=credential.id)
        sessions = SessionService(tx)
        session = await sessions.create(principal, agent_id=agent)
        accepted = await sessions.accept_input(principal, session_id=session.id, source_key="input", input=InputContent("question"))
        run_id = uuid4()
        value = snapshot(principal.tenant_id, agent, run_id)
        await RunService(tx).start(tenant_id=principal.tenant_id, agent_id=agent, run_id=run_id,
            source=SourceIdentity("session", session.id, str(accepted.link.id)), input=InputContent("question"),
            snapshot=value, start_consumer=SessionConsumers())
        await RunService(tx).record_model_step(tenant_id=principal.tenant_id, run_id=run_id,
            payload=ModelStepPayload("step", 1, ModelStepResult("", (), "stop", ModelUsage(), "interaction", False)))
        await RunService(tx).wait(tenant_id=principal.tenant_id, run_id=run_id,
            payload=WaitingPayload("step","wait",question,1), waiting_consumer=SessionConsumers())
        history = await sessions.read_history(principal, session_id=session.id)
        message = history.entries[-1]
        delivery = await ChannelService(tx).enqueue(tenant_id=principal.tenant_id, channel_id=channel.id, kind="session",
            message_id=message.id, destination="D1", delivery_key="delivery", messages=load_message)
    return principal, channel, delivery, keyring, message.id


async def test_delivery_is_source_backed_idempotent_and_tenant_scoped(transaction_factory, test_database):
    principal, channel, delivery, keyring, message_id = await setup(transaction_factory)
    async with transaction_factory() as tx:
        service = ChannelService(tx)
        same = await service.enqueue(tenant_id=principal.tenant_id, channel_id=channel.id, kind="session",
            message_id=message_id, destination="D1", delivery_key="delivery", messages=load_message)
        assert same.id == delivery.id
        with pytest.raises(Conflict):
            await service.enqueue(tenant_id=principal.tenant_id, channel_id=channel.id, kind="session",
                message_id=message_id, destination="D2", delivery_key="delivery", messages=load_message)
        with pytest.raises(AccessDenied):
            await service.get(replace(principal, role="member"), channel_id=channel.id)
        with pytest.raises(NotFound):
            await service.get(replace(principal, tenant_id=uuid4()), channel_id=channel.id)
    calls = []
    def peer(request):
        calls.append(request)
        assert json.loads(request.content)["text"] == "Need your answer"
        return httpx.Response(200, json={"ok":True,"channel":"D1","ts":"1.2"})
    async with create_stateless_http_client(transport=httpx.MockTransport(peer)) as http:
        sender = DeliveryService(test_database.sessions, credentials=lambda tx: CredentialService(tx, keyring), adapters=(SlackAdapter(http),), messages=load_message, context_codec=context_codec())
        assert (await sender.send(tenant_id=principal.tenant_id, delivery_id=delivery.id)).status == "delivered"
        assert (await sender.send(tenant_id=principal.tenant_id, delivery_id=delivery.id)).attempts == 1
    assert len(calls) == 1


async def test_concurrent_or_cancelled_send_leaves_uncertainty_not_duplicate_request(transaction_factory, test_database):
    principal, _, delivery, keyring, _ = await setup(transaction_factory)
    entered, release = asyncio.Event(), asyncio.Event()
    calls = []
    async def peer(request):
        calls.append(request)
        entered.set()
        await release.wait()
        return httpx.Response(200, json={"ok":True,"channel":"D1","ts":"1.2"})
    async with create_stateless_http_client(transport=httpx.MockTransport(peer)) as http:
        sender = DeliveryService(test_database.sessions, credentials=lambda tx: CredentialService(tx, keyring), adapters=(SlackAdapter(http),), messages=load_message, context_codec=context_codec())
        first = asyncio.create_task(sender.send(tenant_id=principal.tenant_id, delivery_id=delivery.id))
        await asyncio.wait_for(entered.wait(), 2)
        second = await sender.send(tenant_id=principal.tenant_id, delivery_id=delivery.id)
        assert second.status == "uncertain"
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        assert (await sender.send(tenant_id=principal.tenant_id, delivery_id=delivery.id)).status == "uncertain"
        release.set()
    assert len(calls) == 1


async def test_wrong_credential_owner_or_provider_cannot_configure(transaction_factory):
    principal, channel, _, keyring, _ = await setup(transaction_factory)
    async with transaction_factory() as tx:
        personal = await CredentialService(tx,keyring).create(principal,kind="channel",provider="slack",label="Personal",
            secret=Secret("private"),owner_kind="membership")
        with pytest.raises(InvalidInput):
            await ChannelService(tx).configure(principal,agent_id=channel.agent_id,provider="slack",external_identity="T2:A1",credential_id=personal.id)
        with pytest.raises(InvalidInput):
            await ChannelService(tx).configure(principal,agent_id=channel.agent_id,provider="feishu",external_identity="app",credential_id=channel.credential_id)
        company = await CredentialService(tx,keyring).create(principal,kind="channel",provider="slack",label="Company",
            secret=Secret('{"version":1,"token":"company","signing_secret":"company-sign"}'), owner_kind="tenant")
        shared = await ChannelService(tx).configure(principal,agent_id=channel.agent_id,provider="slack",external_identity="T2:A1",credential_id=company.id)
        assert shared.credential_owner_kind == "tenant" and shared.credential_owner_id == principal.tenant_id


async def test_authenticated_inbound_uses_explicit_actor_mapping(transaction_factory, test_database):
    from modules.channel.test_slack import NOW, signed

    from app.modules.channel.public import InboundService
    principal, channel, _, keyring, _ = await setup(transaction_factory)
    async with transaction_factory() as tx:
        await ChannelService(tx).bind_actor(principal,channel_id=channel.id,external_actor_id="U1",membership_id=principal.membership_id)
    body, headers = signed({"type":"event_callback", "team_id":"T1","api_app_id":"A1", "event_id":"event", "event":{
        "type":"message", "channel":"D1", "user":"U1", "text":"hello"}})
    async with create_stateless_http_client() as http:
        inbound = InboundService(test_database.sessions,credentials=lambda tx: CredentialService(tx, keyring),adapters=(SlackAdapter(http),), context_codec=context_codec())
        resolved = await inbound.receive(tenant_id=principal.tenant_id,channel_id=channel.id,body=body,headers=headers,now=NOW)
        assert resolved.membership_id == principal.membership_id and resolved.group_id is None
        assert resolved.message.text == "hello"
        unknown, signed_headers = signed({"type":"event_callback", "team_id":"T1","api_app_id":"A1", "event_id":"event2", "event":{
            "type":"message", "channel":"D1", "user":"U2", "text":"hello"}})
        with pytest.raises(NotFound, match="mapped"):
            await inbound.receive(tenant_id=principal.tenant_id,channel_id=channel.id,body=unknown,headers=signed_headers,now=NOW)
