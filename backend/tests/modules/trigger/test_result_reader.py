"""Private Group result scope is checked before any terminal-body fragment read."""

from dataclasses import replace
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from modules.group.test_service import setup
from modules.run.test_lifecycle import snapshot, step

from app.infrastructure.errors import AccessDenied, NotFound
from app.modules.group.public import GroupService
from app.modules.run.public import InputContent, RunService, SourceIdentity
from app.modules.trigger.public import TriggerConfig, TriggerService
from app.modules.workspace.public import WorkspaceSubject


async def test_group_origin_result_is_not_granted_by_agent_visibility_or_agent_output_scope(
        transaction_factory, monkeypatch):
    principal, outsider, group, agent = await setup(transaction_factory)
    now = datetime.now(UTC)
    async with transaction_factory() as tx:
        group_owner = GroupService(tx)
        accepted = await group_owner.accept_input(principal, group_id=group.id,
            source_key="source", input=InputContent("Private group source"), agent_ids=(agent,))
        config = await TriggerService(tx).create(principal, agent_id=agent,
            config=TriggerConfig("Group result", "on_message", "Read group input"))
        occurrence = await TriggerService(tx).accept(tenant_id=principal.tenant_id, trigger_id=config.id,
            source_key="message:" + str(accepted.event.id), now=now, event_kind="on_message",
            source_membership_id=principal.membership_id, origin=WorkspaceSubject("group", group.id),
            origin_conversation_id=accepted.event.conversation_id, input=accepted.event.input)
        assert await TriggerService(tx).read_result(principal, trigger_id=config.id, occurrence_id=occurrence.id) is None
        run_id = uuid4()
        captured = snapshot(principal.tenant_id, agent, run_id)
        captured = replace(captured, allow_human_input=False,
            workspace=replace(captured.workspace, output=WorkspaceSubject("group", group.id)))
        run = (await RunService(tx).start(tenant_id=principal.tenant_id, agent_id=agent, run_id=run_id,
            snapshot=captured, source=occurrence.source, input=occurrence.input, start_consumer=TriggerService(tx))).run
    await step(transaction_factory, principal.tenant_id, run_id)
    async with transaction_factory() as tx:
        await RunService(tx).complete(tenant_id=principal.tenant_id, run_id=run_id, step_id="step",
            output="private output" * 1000, consumer=TriggerService(tx))
        source = (await RunService(tx).start(tenant_id=principal.tenant_id, agent_id=agent, run_id=(source_id := uuid4()),
            snapshot=snapshot(principal.tenant_id, agent, source_id), source=SourceIdentity("scope_probe", uuid4(), "agent"),
            input=InputContent("Agent output is not a Group history grant"))).run
    reads = []
    actual_read = RunService.read_history_fragment
    async def observed(self, **kwargs):
        reads.append(kwargs["run_id"])
        return await actual_read(self, **kwargs)
    monkeypatch.setattr(RunService, "read_history_fragment", observed)
    async with transaction_factory() as tx:
        owner = TriggerService(tx)
        with pytest.raises(AccessDenied):
            await owner.read_result(outsider, trigger_id=config.id, occurrence_id=occurrence.id)
        with pytest.raises(AccessDenied):
            await owner.read_result_for_run(source, trigger_id=config.id, occurrence_id=occurrence.id)
        with pytest.raises(NotFound):
            await owner.read_result(principal, trigger_id=config.id, occurrence_id=uuid4())
        assert reads == []
        assert (await owner.read_result(principal, trigger_id=config.id, occurrence_id=occurrence.id)).kind == "terminal_outcome"
        assert (await owner.read_result_for_run(run, trigger_id=config.id, occurrence_id=occurrence.id)).kind == "terminal_outcome"
        assert reads == [run_id, run_id]
