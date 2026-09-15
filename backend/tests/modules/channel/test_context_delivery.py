import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from modules.run.test_snapshot import snapshot
from sqlalchemy import select

from app.infrastructure.errors import InvalidInput, NotFound
from app.infrastructure.http import create_stateless_http_client
from app.modules.channel.context_repository import ReplyContextRepository
from app.modules.channel.models import ChannelReplyContextRecord
from app.modules.channel.providers.discord import DiscordAdapter
from app.modules.channel.public import ChannelService, DeliveryService, InboundService
from app.modules.credential.public import CredentialService, Secret
from app.modules.model.public import ModelStepResult, ModelUsage
from app.modules.run.public import InputContent, ModelStepPayload, RunService, SourceIdentity, WaitingPayload
from app.modules.session.public import SessionConsumers, SessionService

from .test_delivery import context_codec, load_message, setup


@pytest.mark.parametrize("followup_status,expected", [(200,"delivered"),(500,"uncertain")])
async def test_signed_slash_context_is_encrypted_scoped_and_delivered_from_committed_source(transaction_factory, test_database, followup_status, expected):
    principal, base, _, keyring, message_id = await setup(transaction_factory)
    signer = Ed25519PrivateKey.generate()
    now = datetime.now(UTC)
    async with transaction_factory() as tx:
        secret = await CredentialService(tx, keyring).create(principal, kind="channel", provider="discord", label="Discord",
            secret=Secret('{"version":1,"bot_token":"bot-secret"}'), owner_kind="agent", owner_id=base.agent_id)
        channel = await ChannelService(tx).configure(principal, agent_id=base.agent_id, provider="discord",
            external_identity="123", credential_id=secret.id, settings_json=json.dumps({"connection_mode":"webhook",
                "public_key":signer.public_key().public_bytes_raw().hex()}))
        await ChannelService(tx).bind_actor(principal, channel_id=channel.id, external_actor_id="321", membership_id=principal.membership_id)
    payload = {"type":2,"application_id":"123","id":"456","channel_id":"789","token":"private-interaction",
        "data":{"name":"ask","options":[{"type":3,"name":"message","value":"hello"}]},"user":{"id":"321"}}
    raw = json.dumps(payload).encode()
    stamp = str(int(now.timestamp()))
    headers = {"x-signature-timestamp":stamp,"x-signature-ed25519":signer.sign(stamp.encode()+raw).hex()}
    calls = []
    def peer(request):
        calls.append(request)
        if len(calls) == 1:
            assert request.method == "PATCH" and request.url.raw_path == b"/api/v10/webhooks/123/private-interaction/messages/@original"
            assert json.loads(request.content)["content"] == "Need your answer"
        else:
            assert request.method == "POST" and request.url.raw_path == b"/api/v10/webhooks/123/private-interaction?wait=true"
            assert json.loads(request.content)["content"] == "A separate question"
        return httpx.Response(200 if len(calls) == 1 else followup_status, json={"id":"999","channel_id":"789"})
    async with create_stateless_http_client(transport=httpx.MockTransport(peer)) as http:
        adapter = DiscordAdapter(http)
        inbound = InboundService(test_database.sessions, credentials=lambda tx: CredentialService(tx, keyring),
            adapters=(adapter,), context_codec=context_codec())
        result = await inbound.receive(tenant_id=principal.tenant_id, channel_id=channel.id, body=raw, headers=headers, now=now)
        assert result.reply_context_id is not None and "private-interaction" not in repr(result)
        repeated = await inbound.receive(tenant_id=principal.tenant_id, channel_id=channel.id, body=raw, headers=headers, now=now)
        assert repeated.reply_context_id == result.reply_context_id
        async with transaction_factory() as tx:
            row = await tx.session.scalar(select(ChannelReplyContextRecord).where(ChannelReplyContextRecord.id == result.reply_context_id))
            assert b"private-interaction" not in row.ciphertext
            with pytest.raises(NotFound):
                await ReplyContextRepository(tx, context_codec()).load(tenant_id=uuid4(), agent_id=base.agent_id,
                    channel_id=channel.id, context_id=result.reply_context_id, now=now)
            delivery = await ChannelService(tx).enqueue(tenant_id=principal.tenant_id, channel_id=channel.id,
                kind="session", message_id=message_id, destination="789", delivery_key="slash",
                messages=load_message, reply_context_id=result.reply_context_id)
            session = SessionService(tx)
            previous = await session.get_message_for_delivery(tenant_id=principal.tenant_id, agent_id=base.agent_id, message_id=message_id)
            accepted = await session.accept_input(principal, session_id=previous.session_id, source_key="second", input=InputContent("next"))
            run_id = uuid4()
            await RunService(tx).start(tenant_id=principal.tenant_id, agent_id=base.agent_id, run_id=run_id,
                source=SourceIdentity("session", previous.session_id, str(accepted.link.id)), input=InputContent("next"),
                snapshot=snapshot(principal.tenant_id, base.agent_id, run_id), start_consumer=SessionConsumers())
            await RunService(tx).record_model_step(tenant_id=principal.tenant_id, run_id=run_id,
                payload=ModelStepPayload("step", 1, ModelStepResult("", (), "stop", ModelUsage(), "interaction", False)))
            await RunService(tx).wait(tenant_id=principal.tenant_id, run_id=run_id,
                payload=WaitingPayload("step", "wait2", "A separate question", 1), waiting_consumer=SessionConsumers())
            history = await session.read_history(principal, session_id=previous.session_id)
            second = await ChannelService(tx).enqueue(tenant_id=principal.tenant_id, channel_id=channel.id,
                kind="session", message_id=history.entries[-1].id, destination="789", delivery_key="slash-second",
                messages=load_message, reply_context_id=result.reply_context_id)
        sender = DeliveryService(test_database.sessions, credentials=lambda tx: CredentialService(tx, keyring),
            adapters=(adapter,), messages=load_message, context_codec=context_codec())
        assert (await sender.send(tenant_id=principal.tenant_id, delivery_id=delivery.id)).status == "delivered"
        assert (await sender.send(tenant_id=principal.tenant_id, delivery_id=delivery.id)).status == "delivered"
        assert len(calls) == 1
        assert (await sender.send(tenant_id=principal.tenant_id, delivery_id=second.id)).status == expected
        assert (await sender.send(tenant_id=principal.tenant_id, delivery_id=second.id)).status == expected
        assert len(calls) == 2
    async with transaction_factory() as tx:
        repo = ReplyContextRepository(tx, context_codec())
        assert await repo.clear_expired(tenant_id=principal.tenant_id, now=now + timedelta(hours=1), limit=1) == 1
        row = await tx.session.scalar(select(ChannelReplyContextRecord).where(ChannelReplyContextRecord.id == result.reply_context_id))
        assert row.ciphertext == row.nonce == b""
        with pytest.raises(InvalidInput, match="expired"):
            await repo.load(tenant_id=principal.tenant_id, agent_id=base.agent_id, channel_id=channel.id,
                context_id=result.reply_context_id, now=now + timedelta(hours=1))
