import asyncio
import json

import httpx
import pytest

from app.infrastructure.http import create_stateless_http_client
from app.modules.channel.adapters import SlackAdapter
from app.modules.channel.chunks import send_chunks
from app.modules.channel.contracts import SendOutcome
from app.modules.channel.public import DeliveryService
from app.modules.credential.public import CredentialService

from .test_delivery import context_codec, load_message, setup


async def test_unicode_slices_reconstruct_source_and_keep_bounded_acknowledgements():
    parts = []
    async def send(text, index):
        assert len(text.encode()) <= 8 and len(text) <= 3
        parts.append(text)
        return SendOutcome("delivered", acknowledgement=str(index), provider_reply_ids=(str(index),))
    text = "中🙂文hello"
    result = await send_chunks(text, max_characters=3, max_bytes=8, send=send)
    assert "".join(parts) == text and result.status == "delivered"
    assert json.loads(result.acknowledgement) == {"first":"0","last":str(len(parts)-1)}
    assert result.provider_reply_ids == tuple(str(index) for index in range(len(parts)))


@pytest.mark.parametrize("failure", ["rejected","cancelled"])
async def test_partial_real_slack_send_remains_uncertain_and_is_not_replayed(transaction_factory, test_database, failure):
    principal, _, delivery, keyring, _ = await setup(transaction_factory, question="x" * 8001)
    calls = []
    entered = asyncio.Event()
    async def peer(request):
        calls.append(request)
        assert len(json.loads(request.content)["text"]) == 4000
        if len(calls) == 1:
            return httpx.Response(200, json={"ok":True,"channel":"D1","ts":"1"})
        entered.set()
        if failure == "cancelled":
            await asyncio.Event().wait()
        return httpx.Response(403)
    async with create_stateless_http_client(transport=httpx.MockTransport(peer)) as http:
        sender = DeliveryService(test_database.sessions, credentials=lambda tx: CredentialService(tx, keyring),
            adapters=(SlackAdapter(http),), messages=load_message, context_codec=context_codec())
        task = asyncio.create_task(sender.send(tenant_id=principal.tenant_id, delivery_id=delivery.id))
        await asyncio.wait_for(entered.wait(), timeout=3)
        if failure == "cancelled":
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            assert (await task).status == "uncertain"
            async with transaction_factory() as tx:
                from app.modules.channel.public import ChannelService
                found = await ChannelService(tx).delivered_reply(tenant_id=principal.tenant_id,
                    channel_id=delivery.channel_id, destination="D1", acknowledgement="1")
                assert found is not None and found.status == "uncertain"
        assert (await sender.send(tenant_id=principal.tenant_id, delivery_id=delivery.id)).status == "uncertain"
        assert len(calls) == 2


@pytest.mark.parametrize("identities", [("",), ("中" * 171,), tuple(str(i) for i in range(257)), ("same", "same")])
def test_reply_identity_bounds_reject_invalid_data(identities):
    from app.infrastructure.errors import InvalidInput
    with pytest.raises(InvalidInput):
        SendOutcome("delivered", provider_reply_ids=identities)


def test_reply_identity_limits_accept_complete_bound():
    values = tuple(str(i).zfill(512) for i in range(256))
    assert SendOutcome("delivered", provider_reply_ids=values).provider_reply_ids == values
