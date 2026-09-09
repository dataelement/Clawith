"""Group roster and topic boundaries use the real PostgreSQL owner path."""

import asyncio
import json
from dataclasses import replace
from uuid import uuid4

import pytest
from modules.group.test_service import setup, started
from modules.run.test_lifecycle import snapshot
from runtime.test_engine import with_tools

from app.infrastructure.errors import AccessDenied, InvalidInput, NotFound
from app.modules.group.public import GroupService
from app.modules.model.public import ModelStepResult, ModelToolCall, ModelUsage
from app.modules.run.public import (
    InputContent,
    InputReference,
    ModelStepPayload,
    RunService,
    SourceIdentity,
    derive_child,
)


async def test_roster_never_grants_visibility_and_requires_explicit_invitation(transaction_factory):
    creator, peer, group, agent = await setup(transaction_factory)
    async with transaction_factory() as tx:
        service = GroupService(tx)
        await service.set_membership(creator, group_id=group.id, membership_id=peer.membership_id, enabled=True)
        blind = replace(peer, allowed_agent_ids=frozenset())
        assert await service.list_members(blind, group_id=group.id, kind="agent") == ()
        assert await service.invitation_candidates(blind, group_id=group.id, kind="agent") == ()
        with pytest.raises(AccessDenied):
            await service.accept_input(blind, group_id=group.id, source_key="blind", input=InputContent("x"), agent_ids=(agent,))
        await service.set_agent(creator, group_id=group.id, agent_id=agent, enabled=False)
        with pytest.raises(AccessDenied):
            await service.accept_input(creator, group_id=group.id, source_key="not-member", input=InputContent("x"), agent_ids=(agent,))
        await service.set_agent(creator, group_id=group.id, agent_id=agent, enabled=True)
        assert len(await service.list_members(creator, group_id=group.id, kind="agent")) == 1
        people = await service.invitation_candidates(creator, group_id=group.id, kind="human", limit=1)
        assert len(people) == 1
        assert not hasattr(people[0], "account_id")
        with pytest.raises(InvalidInput):
            await service.invitation_candidates(creator, group_id=group.id, kind="human", limit=101)


async def test_topics_history_watermarks_and_mentions_are_independent(transaction_factory):
    creator, peer, group, agent = await setup(transaction_factory)
    async with transaction_factory() as tx:
        service = GroupService(tx)
        await service.set_membership(creator, group_id=group.id, membership_id=peer.membership_id, enabled=True)
        default = (await service.list_conversations(creator, group_id=group.id))[0]
        other = await service.create_conversation(creator, group_id=group.id, title="Research")
        one = await service.accept_input(peer, group_id=group.id, source_key="one", input=InputContent("hello"),
            agent_ids=(), mentioned_membership_ids=(creator.membership_id,))
        two = await service.accept_input(peer, group_id=group.id, conversation_id=other.id,
            source_key="two", input=InputContent("research"), agent_ids=(agent,))
        assert one.event.mentioned_membership_ids == (creator.membership_id,) and not one.links
        assert two.links[0].conversation_id == other.id
        assert [value.id for value in await service.list_events(creator, group_id=group.id)] == [one.event.id]
        assert [value.id for value in await service.list_events(creator, group_id=group.id, conversation_id=other.id)] == [two.event.id]
        assert not await service.list_events(creator, group_id=group.id, through_position=one.event.position, conversation_id=other.id)
        topics = {value.id: value for value in await service.list_conversations(creator, group_id=group.id)}
        assert topics[default.id].unread_count == topics[other.id].unread_count == 1
        assert topics[default.id].head_position == 1 and topics[other.id].head_position == 2
        with pytest.raises(InvalidInput):
            await service.mark_read(creator, group_id=group.id, conversation_id=default.id, through_position=2)
        assert await service.mark_read(creator, group_id=group.id, conversation_id=default.id, through_position=1) == 1
        assert await service.mark_read(creator, group_id=group.id, conversation_id=default.id, through_position=0) == 1
        topics = {value.id: value for value in await service.list_conversations(creator, group_id=group.id)}
        assert topics[default.id].unread_count == 0 and topics[other.id].unread_count == 1
        assert len(await service.list_work(creator, group_id=group.id, conversation_id=other.id)) == 1
        assert not await service.list_work(creator, group_id=group.id)
        with pytest.raises(AccessDenied):
            await service.update_conversation(peer, group_id=group.id, conversation_id=default.id, title="x", enabled=False)
        await service.update_conversation(creator, group_id=group.id, conversation_id=other.id, title="Archived", enabled=False)
        with pytest.raises(NotFound):
            await service.accept_input(creator, group_id=group.id, conversation_id=other.id,
                source_key="removed", input=InputContent("x"), agent_ids=())


async def test_work_cancel_commits_owner_result_without_extra_message(transaction_factory):
    creator, peer, group, agent = await setup(transaction_factory)
    _, run = await started(transaction_factory, creator, group, agent)
    async with transaction_factory() as tx:
        service = GroupService(tx)
        with pytest.raises(AccessDenied):
            await service.cancel_work(peer, group_id=group.id, run_id=run.id)
        changed = await service.cancel_work(creator, group_id=group.id, run_id=run.id)
        assert changed.run.status == "Cancelled"
        assert (await service.list_work(creator, group_id=group.id))[0].result["status"] == "Cancelled"
        assert len(await service.list_events(creator, group_id=group.id)) == 1


async def test_concurrent_read_watermarks_do_not_regress(transaction_factory):
    creator, _, group, _ = await setup(transaction_factory)
    async with transaction_factory() as tx:
        service = GroupService(tx)
        default = (await service.list_conversations(creator, group_id=group.id))[0]
        for index in range(3):
            await service.accept_input(creator, group_id=group.id, source_key=str(index), input=InputContent(str(index)), agent_ids=())

    async def advance(position):
        async with transaction_factory() as tx:
            return await GroupService(tx).mark_read(creator, group_id=group.id,
                conversation_id=default.id, through_position=position)

    await asyncio.gather(advance(3), advance(1), advance(2))
    async with transaction_factory() as tx:
        assert (await GroupService(tx).list_conversations(creator, group_id=group.id))[0].read_position == 3


async def test_foreign_topic_and_nonmember_mention_are_rejected(transaction_factory):
    creator, peer, group, _ = await setup(transaction_factory)
    async with transaction_factory() as tx:
        service = GroupService(tx)
        foreign = await service.create(creator, name="Other group")
        topic = (await service.list_conversations(creator, group_id=foreign.id))[0]
        with pytest.raises(NotFound):
            await service.accept_input(creator, group_id=group.id, conversation_id=topic.id,
                source_key="wrong-topic", input=InputContent("x"), agent_ids=())
        with pytest.raises(AccessDenied):
            await service.accept_input(creator, group_id=group.id, source_key="wrong-human",
                input=InputContent("x"), agent_ids=(), mentioned_membership_ids=(peer.membership_id,))
        with pytest.raises(AccessDenied):
            await service.invitation_candidates(peer, group_id=group.id, kind="human")
        with pytest.raises(NotFound):
            await service.list_events(creator, group_id=group.id, conversation_id=uuid4())


async def test_reply_keeps_initiating_topic_even_with_newer_other_topic_messages(transaction_factory):
    creator, _, group, agent = await setup(transaction_factory)
    run_id = uuid4()
    async with transaction_factory() as tx:
        service = GroupService(tx)
        topic = await service.create_conversation(creator, group_id=group.id, title="Research")
        accepted = await service.accept_input(creator, group_id=group.id, conversation_id=topic.id,
            source_key="research", input=InputContent("research"), agent_ids=(agent,))
        await RunService(tx).start(tenant_id=creator.tenant_id, agent_id=agent, run_id=run_id,
            snapshot=with_tools(snapshot(creator.tenant_id, agent, run_id), "send_message"), input=accepted.event.input,
            source=SourceIdentity("group", accepted.event.id, str(agent)), start_consumer=service)
        await RunService(tx).record_model_step(tenant_id=creator.tenant_id, run_id=run_id,
            payload=ModelStepPayload("step", 1, ModelStepResult("", (ModelToolCall("call", "send_message", "{}"),),
                "tool_calls", ModelUsage(), "step", False)))
        await service.accept_input(creator, group_id=group.id, source_key="general", input=InputContent("general"), agent_ids=())
        message = await service.accept_message(tenant_id=creator.tenant_id, run_id=run_id,
            step_id="step", call_id="call", input=InputContent("Research response"))
        assert message.conversation_id == topic.id
        assert len(await service.list_events(creator, group_id=group.id, conversation_id=topic.id)) == 2
        assert len(await service.list_events(creator, group_id=group.id)) == 1


async def test_delete_closes_pending_admission_and_preserves_terminal_settlement(transaction_factory):
    creator, _, group, agent = await setup(transaction_factory)
    _, run = await started(transaction_factory, creator, group, agent)
    child_id = uuid4()
    async with transaction_factory() as tx:
        parent_snapshot = await RunService(tx).read_snapshot(tenant_id=creator.tenant_id, run_id=run.id)
        await RunService(tx).start(tenant_id=creator.tenant_id, agent_id=agent, run_id=child_id,
            snapshot=derive_child(parent_snapshot, run_id=child_id), input=InputContent("child work"),
            source=SourceIdentity("task", run.id, "child"), parent_run_id=run.id)
        service = GroupService(tx)
        topic = (await service.list_conversations(creator, group_id=group.id))[0]
        pending = await service.accept_input(creator, group_id=group.id, source_key="pending",
            input=InputContent("pending"), agent_ids=(agent,))
        await service.delete_conversation(creator, group_id=group.id, conversation_id=topic.id)
        replacement = (await service.list_conversations(creator, group_id=group.id))[0]
        assert replacement.id != topic.id and replacement.is_default
        assert (await service.links(creator, group_id=group.id, event_id=pending.event.id))[0].admission == "failed"
        ids = await service.conversation_cancellation_page(creator, group_id=group.id, conversation_id=topic.id)
        assert ids == (run.id,)
    pending_run = uuid4()
    with pytest.raises(NotFound):
        async with transaction_factory() as tx:
            await RunService(tx).start(tenant_id=creator.tenant_id, agent_id=agent, run_id=pending_run,
                snapshot=snapshot(creator.tenant_id, agent, pending_run), input=pending.event.input,
                source=SourceIdentity("group", pending.event.id, str(agent)), start_consumer=GroupService(tx))
    async with transaction_factory() as tx:
        service = GroupService(tx)
        result = await service.cancel_removed_conversation_work(creator, group_id=group.id,
            conversation_id=topic.id, run_id=run.id)
        assert result.run.status == "Cancelled"
        assert (await RunService(tx).get(tenant_id=creator.tenant_id, run_id=child_id)).status == "Cancelled"
        assert not (await service.cancel_removed_conversation_work(creator, group_id=group.id,
            conversation_id=topic.id, run_id=run.id)).changed
        await service.delete_conversation(creator, group_id=group.id, conversation_id=topic.id)
        assert len(await service.list_conversations(creator, group_id=group.id)) == 1


async def test_delete_wins_race_with_uncommitted_start_consumer(transaction_factory):
    creator, _, group, agent = await setup(transaction_factory)
    entered, release = asyncio.Event(), asyncio.Event()
    run_id = uuid4()
    async with transaction_factory() as tx:
        service = GroupService(tx)
        topic = (await service.list_conversations(creator, group_id=group.id))[0]
        pending = await service.accept_input(creator, group_id=group.id, source_key="racing",
            input=InputContent("work"), agent_ids=(agent,))

    class PausedStart:
        async def record_started(self, transaction, *, run):
            entered.set()
            await release.wait()
            await GroupService(transaction).record_started(transaction, run=run)

    async def starting():
        with pytest.raises(NotFound):
            async with transaction_factory() as tx:
                await RunService(tx).start(tenant_id=creator.tenant_id, agent_id=agent, run_id=run_id,
                    snapshot=snapshot(creator.tenant_id, agent, run_id), input=pending.event.input,
                    source=SourceIdentity("group", pending.event.id, str(agent)), start_consumer=PausedStart())

    task = asyncio.create_task(starting())
    await asyncio.wait_for(entered.wait(), 3)
    try:
        async with transaction_factory() as tx:
            await GroupService(tx).delete_conversation(creator, group_id=group.id, conversation_id=topic.id)
    finally:
        release.set()
    await asyncio.wait_for(task, 3)
    async with transaction_factory() as tx:
        with pytest.raises(NotFound):
            await RunService(tx).get(tenant_id=creator.tenant_id, run_id=run_id)


async def test_context_history_preserves_references_and_marks_oversized_entries(transaction_factory):
    creator, _, group, _ = await setup(transaction_factory)
    async with transaction_factory() as tx:
        service = GroupService(tx)
        topic = (await service.list_conversations(creator, group_id=group.id))[0]
        large = await service.accept_input(creator, group_id=group.id, source_key="large",
            input=InputContent("x" * 20000), agent_ids=())
        small = await service.accept_input(creator, group_id=group.id, source_key="small",
            input=InputContent("Read source", (InputReference("source:report", "report", "text/plain"),)), agent_ids=())
        await service.accept_input(creator, group_id=group.id, source_key="future", input=InputContent("FUTURE"), agent_ids=())
        text = await service.read_context_history(creator, group_id=group.id, conversation_id=topic.id,
            through_position=small.event.position, max_bytes=1024)
        assert len(text.encode()) <= 1024 and "FUTURE" not in text
        parsed = json.loads(text)
        assert parsed["entries"][0] == {"event_id": str(large.event.id), "position": large.event.position, "reference_only": True}
        assert parsed["entries"][1]["input"]["references"][0]["reference"] == "source:report"


async def test_realtime_page_head_ignores_newer_other_topic_positions(transaction_factory):
    creator, _, group, _ = await setup(transaction_factory)
    async with transaction_factory() as tx:
        service = GroupService(tx)
        default = await service.resolve_conversation(creator, group_id=group.id)
        other = await service.create_conversation(creator, group_id=group.id, title="Other")
        one = await service.accept_input(creator, group_id=group.id, source_key="one", input=InputContent("one"), agent_ids=())
        await service.accept_input(creator, group_id=group.id, conversation_id=other.id,
            source_key="other", input=InputContent("other"), agent_ids=())
        page = await service.read_event_page(creator, group_id=group.id, conversation_id=default)
        assert page.next_after_position == one.event.position and not page.has_more
        empty = await service.read_event_page(creator, group_id=group.id, conversation_id=default,
            after_position=one.event.position)
        assert not empty.entries and not empty.has_more
