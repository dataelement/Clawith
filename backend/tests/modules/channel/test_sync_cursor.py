import json

import pytest
from sqlalchemy import select

from app.infrastructure.errors import Conflict, InvalidInput
from app.modules.channel.models import ChannelSyncCursorRecord
from app.modules.channel.public import ChannelService, ChannelSyncCursors
from app.modules.credential.public import CredentialService, Secret

from .test_delivery import context_codec, setup


async def test_encrypted_notice_cursors_are_independent_deduplicated_and_compare_and_swap(transaction_factory):
    principal, base, _, keys, _ = await setup(transaction_factory)
    codec = context_codec()
    async with transaction_factory() as tx:
        credential = await CredentialService(tx, keys).create(principal, kind="channel", provider="wecom", label="KF",
            secret=Secret("test-only"), owner_kind="agent", owner_id=base.agent_id)
        channel = await ChannelService(tx).configure(principal, agent_id=base.agent_id, provider="wecom",
            external_identity="corp:kf:K1", credential_id=credential.id,
            settings_json=json.dumps({"connection_mode": "customer_service", "corp_id": "corp", "open_kfid": "K1"}))
        owner = ChannelSyncCursors(tx, codec)
        one = await owner.accept(channel, event_id="one", open_kfid="K1", event_token=Secret("private-event-token"))
        assert (await owner.accept(channel, event_id="one", open_kfid="K1", event_token=Secret("ignored duplicate"))).id == one.id
        two = await owner.accept(channel, event_id="two", open_kfid="K1", event_token=Secret("second-token"))
        assert two.id != one.id
        next_page = await owner.advance(one, next_cursor=Secret("private-next-cursor"))
        assert next_page.kind == "cursor" and next_page.coordinate.value == "private-next-cursor"
        with pytest.raises(Conflict):
            await owner.advance(one, next_cursor=None)
        row = await tx.session.scalar(select(ChannelSyncCursorRecord).where(ChannelSyncCursorRecord.id == one.id))
        assert b"private" not in row.ciphertext
        row.external_event_id = "tampered"
        with pytest.raises(InvalidInput, match="authentication"):
            owner._read(row)
        row.external_event_id = "one"
        done = await owner.advance(next_page, next_cursor=None)
        assert done.kind == "done" and done.coordinate is None
        assert [item.id for item in await owner.pending()] == [two.id]


def test_customer_service_settings_do_not_require_fabricated_agent_id():
    from app.modules.channel.settings import validate_settings
    valid = {"connection_mode": "customer_service", "corp_id": "corp", "open_kfid": "kf"}
    assert "customer_service" in validate_settings("wecom", json.dumps(valid))
    for invalid in ({**valid, "agent_id": 1}, {"connection_mode": "customer_service", "corp_id": "corp"}):
        with pytest.raises(InvalidInput):
            validate_settings("wecom", json.dumps(invalid))
