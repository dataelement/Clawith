"""Input provenance is metadata and cannot become receiver Workspace authority."""

from dataclasses import replace
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from modules.a2a.test_service import setup
from modules.a2a.test_takeover import group_main, session_main
from modules.run.test_lifecycle import snapshot
from runtime.test_engine import with_tools

from app.infrastructure.errors import AccessDenied, InvalidInput, NotFound
from app.modules.a2a.public import A2AInputVisibility, A2AService
from app.modules.group.public import GroupService
from app.modules.model.public import ModelStepResult, ModelToolCall, ModelUsage
from app.modules.permission.public import PermissionService
from app.modules.run.public import InputContent, ModelStepPayload, RunService, SourceIdentity
from app.modules.tool.public import CredentialBinding
from app.modules.trigger.public import TriggerConfig, TriggerService
from app.modules.workspace.public import WorkspaceSubject


async def test_private_membership_visibility_survives_nested_a2a_agent_outputs(transaction_factory):
    p, seeded, target = await setup(transaction_factory)
    original, _ = await session_main(transaction_factory, p, seeded.agent_id)
    async with transaction_factory() as tx:
        service = A2AService(tx)
        first = await service.accept(tenant_id=p.tenant_id, source_run_id=original.id, step_id="step", call_id="call",
            target_agent_id=target, intent="consult", input=InputContent("Explicit private input"))
        assert (await service.input_visibility(tenant_id=p.tenant_id, request_id=first.id)).subject == WorkspaceSubject("membership", p.membership_id)
        target_id = uuid4()
        await RunService(tx).start(tenant_id=p.tenant_id, agent_id=target, run_id=target_id,
            snapshot=with_tools(snapshot(p.tenant_id, target, target_id), "send_message_to_agent"), input=first.input,
            source=SourceIdentity("a2a", first.id, "target"), start_consumer=service)
        await RunService(tx).record_model_step(tenant_id=p.tenant_id, run_id=target_id,
            payload=ModelStepPayload("step", 1, ModelStepResult("", (ModelToolCall("call", "send_message_to_agent", "{}"),),
                "tool_calls", ModelUsage(), "step", False)))
        await PermissionService(tx).set_visibility(p, agent_id=seeded.agent_id, visibility="tenant")
        second = await service.accept(tenant_id=p.tenant_id, source_run_id=target_id, step_id="step", call_id="call",
            target_agent_id=seeded.agent_id, intent="consult", input=InputContent("Only selected input"))
        visibility = await service.input_visibility(tenant_id=p.tenant_id, request_id=second.id)
        assert visibility.subject == WorkspaceSubject("membership", p.membership_id) and visibility.conversation_id is None
        captured = await RunService(tx).read_snapshot(tenant_id=p.tenant_id, run_id=target_id)
        assert captured.workspace.output == WorkspaceSubject("agent", target)


async def test_group_visibility_retains_its_exact_topic(transaction_factory):
    p, seeded, target = await setup(transaction_factory)
    async with transaction_factory() as tx:
        owner = GroupService(tx)
        group = await owner.create(p, name="Group")
        topic = await owner.create_conversation(p, group_id=group.id, title="Topic")
        await owner.set_agent(p, group_id=group.id, agent_id=seeded.agent_id, enabled=True)
    source = await group_main(transaction_factory, p, seeded.agent_id, group.id, topic.id)
    async with transaction_factory() as tx:
        service = A2AService(tx)
        request = await service.accept(tenant_id=p.tenant_id, source_run_id=source.id, step_id="step", call_id="call",
            target_agent_id=target, intent="consult", input=InputContent("Group input"))
        visibility = await service.input_visibility(tenant_id=p.tenant_id, request_id=request.id)
        assert visibility.subject == WorkspaceSubject("group", group.id) and visibility.conversation_id == topic.id


async def test_true_agent_source_keeps_original_agent_visibility(transaction_factory):
    p, source, target = await setup(transaction_factory)
    async with transaction_factory() as tx:
        service = A2AService(tx)
        request = await service.accept(tenant_id=p.tenant_id, source_run_id=source.id, step_id="step", call_id="call",
            target_agent_id=target, intent="consult", input=InputContent("Agent-owned input"))
        assert (await service.input_visibility(tenant_id=p.tenant_id, request_id=request.id)).subject == WorkspaceSubject("agent", source.agent_id)
        with pytest.raises(NotFound):
            await service.input_visibility(tenant_id=uuid4(), request_id=request.id)


async def test_visibility_hop_limit_is_explicit_not_public_fallback(transaction_factory):
    p, source, first_target = await setup(transaction_factory)
    original_agent = source.agent_id
    async with transaction_factory() as tx:
        await PermissionService(tx).set_visibility(p, agent_id=original_agent, visibility="tenant")
        owner = A2AService(tx)
        for index in range(17):
            target = first_target if source.agent_id == original_agent else original_agent
            request = await owner.accept(tenant_id=p.tenant_id, source_run_id=source.id, step_id="step", call_id="call",
                target_agent_id=target, intent="consult", input=InputContent("Agent input"))
            if index == 15:
                assert (await owner.input_visibility(tenant_id=p.tenant_id, request_id=request.id)).subject == WorkspaceSubject("agent", original_agent)
            if index == 16:
                with pytest.raises(InvalidInput, match="sixteen"):
                    await owner.input_visibility(tenant_id=p.tenant_id, request_id=request.id)
                break
            run_id = uuid4()
            source = (await RunService(tx).start(tenant_id=p.tenant_id, agent_id=target, run_id=run_id,
                snapshot=with_tools(snapshot(p.tenant_id, target, run_id), "send_message_to_agent"), input=request.input,
                source=SourceIdentity("a2a", request.id, "target"), start_consumer=owner)).run
            await RunService(tx).record_model_step(tenant_id=p.tenant_id, run_id=run_id,
                payload=ModelStepPayload("step", 1, ModelStepResult("", (ModelToolCall("call", "send_message_to_agent", "{}"),),
                    "tool_calls", ModelUsage(), "step", False)))


async def test_agent_output_with_captured_personal_binding_retains_membership_visibility(transaction_factory):
    p, seeded, target = await setup(transaction_factory)
    run_id = uuid4()
    captured = with_tools(snapshot(p.tenant_id, seeded.agent_id, run_id), "send_message_to_agent")
    first, *remaining = captured.tools.tools
    captured = replace(captured, tools=replace(captured.tools, tools=(replace(first,
        credential=CredentialBinding(uuid4(), "membership", p.membership_id)), *remaining)))
    async with transaction_factory() as tx:
        source = (await RunService(tx).start(tenant_id=p.tenant_id, agent_id=seeded.agent_id, run_id=run_id,
            snapshot=captured, input=InputContent("Account-derived input"),
            source=SourceIdentity("trigger", uuid4(), "occurrence"))).run
        await RunService(tx).record_model_step(tenant_id=p.tenant_id, run_id=run_id,
            payload=ModelStepPayload("step", 1, ModelStepResult("", (ModelToolCall("call", "send_message_to_agent", "{}"),),
                "tool_calls", ModelUsage(), "step", False)))
        owner = A2AService(tx)
        request = await owner.accept(tenant_id=p.tenant_id, source_run_id=source.id, step_id="step", call_id="call",
            target_agent_id=target, intent="consult", input=InputContent("Selected input"))
        assert (await owner.input_visibility(tenant_id=p.tenant_id, request_id=request.id)).subject == WorkspaceSubject("membership", p.membership_id)


async def test_private_message_trigger_between_a2a_hops_requires_product_origin_resolver(transaction_factory):
    p, seeded, agent_b = await setup(transaction_factory)
    user_run, _ = await session_main(transaction_factory, p, seeded.agent_id)
    async with transaction_factory() as tx:
        a2a = A2AService(tx)
        first = await a2a.accept(tenant_id=p.tenant_id, source_run_id=user_run.id, step_id="step", call_id="call",
            target_agent_id=agent_b, intent="consult", input=InputContent("Private user request"))
        receiver_id = uuid4()
        await RunService(tx).start(tenant_id=p.tenant_id, agent_id=agent_b, run_id=receiver_id,
            snapshot=snapshot(p.tenant_id, agent_b, receiver_id), input=first.input,
            source=SourceIdentity("a2a", first.id, "target"), start_consumer=a2a)
        origin = await a2a.input_visibility(tenant_id=p.tenant_id, request_id=first.id)
        trigger = TriggerService(tx)
        config = await trigger.create(p, agent_id=agent_b, config=TriggerConfig("relay", "on_message", "Relay input"))
        occurrence = await trigger.accept(tenant_id=p.tenant_id, trigger_id=config.id, source_key="message:" + str(first.id),
            now=datetime.now(UTC), event_kind="on_message", source_agent_id=seeded.agent_id, input=first.input,
            origin=origin.subject)
        run_id = uuid4()
        captured = with_tools(snapshot(p.tenant_id, agent_b, run_id), "send_message_to_agent")
        captured = replace(captured, workspace=replace(captured.workspace,
            allow_shared_memory_writes=False, allow_shared_file_writes=False))
        source = (await RunService(tx).start(tenant_id=p.tenant_id, agent_id=agent_b, run_id=run_id,
            snapshot=captured, input=occurrence.input, source=occurrence.source, start_consumer=trigger)).run
        await RunService(tx).record_model_step(tenant_id=p.tenant_id, run_id=source.id,
            payload=ModelStepPayload("step", 1, ModelStepResult("", (ModelToolCall("call", "send_message_to_agent", "{}"),),
                "tool_calls", ModelUsage(), "step", False)))
        await PermissionService(tx).set_visibility(p, agent_id=seeded.agent_id, visibility="tenant")
        relayed = await a2a.accept(tenant_id=p.tenant_id, source_run_id=source.id, step_id="step", call_id="call",
            target_agent_id=seeded.agent_id, intent="consult", input=InputContent("Private relayed content"))
        with pytest.raises(AccessDenied, match="origin"):
            await a2a.input_visibility(tenant_id=p.tenant_id, request_id=relayed.id)
        async def unresolved(transaction, *, run):
            return None
        with pytest.raises(AccessDenied, match="origin"):
            await a2a.input_visibility(tenant_id=p.tenant_id, request_id=relayed.id, resolve_product_origin=unresolved)

        async def resolve_trigger_origin(transaction, *, run):
            saved = await TriggerService(transaction).get_occurrence(tenant_id=run.tenant_id, occurrence_id=run.source.owner_id)
            assert saved.run_id == run.id and saved.agent_id == run.agent_id
            return A2AInputVisibility(WorkspaceSubject(saved.origin_kind, saved.origin_id), saved.origin_conversation_id)

        resolved = await a2a.input_visibility(tenant_id=p.tenant_id, request_id=relayed.id,
            resolve_product_origin=resolve_trigger_origin)
        assert resolved.subject == WorkspaceSubject("membership", p.membership_id)
        assert (await RunService(tx).get(tenant_id=p.tenant_id, run_id=receiver_id)).status == "Running"
        assert (await RunService(tx).read_snapshot(tenant_id=p.tenant_id, run_id=source.id)).workspace.output == WorkspaceSubject("agent", agent_b)


async def test_group_output_trigger_uses_frozen_origin_topic_before_workspace_inference(transaction_factory):
    p, seeded, target = await setup(transaction_factory)
    async with transaction_factory() as tx:
        group = await GroupService(tx).create(p, name="Private group")
        topic = await GroupService(tx).create_conversation(p, group_id=group.id, title="Exact topic")
        trigger = TriggerService(tx)
        config = await trigger.create(p, agent_id=seeded.agent_id, config=TriggerConfig("topic", "on_message", "Process"))
        occurrence = await trigger.accept(tenant_id=p.tenant_id, trigger_id=config.id, source_key="group-message",
            now=datetime.now(UTC), event_kind="on_message", source_membership_id=p.membership_id, input=InputContent("Group content"),
            origin=WorkspaceSubject("group", group.id), origin_conversation_id=topic.id)
        run_id = uuid4()
        captured = with_tools(snapshot(p.tenant_id, seeded.agent_id, run_id), "send_message_to_agent")
        captured = replace(captured, workspace=replace(captured.workspace, output=WorkspaceSubject("group", group.id),
            allow_shared_memory_writes=False, allow_shared_file_writes=False))
        await RunService(tx).start(tenant_id=p.tenant_id, agent_id=seeded.agent_id, run_id=run_id,
            snapshot=captured, input=occurrence.input, source=occurrence.source, start_consumer=trigger)
        await RunService(tx).record_model_step(tenant_id=p.tenant_id, run_id=run_id,
            payload=ModelStepPayload("step", 1, ModelStepResult("", (ModelToolCall("call", "send_message_to_agent", "{}"),),
                "tool_calls", ModelUsage(), "step", False)))
        request = await A2AService(tx).accept(tenant_id=p.tenant_id, source_run_id=run_id, step_id="step", call_id="call",
            target_agent_id=target, intent="consult", input=InputContent("Selected group content"))
        calls = []
        async def resolve_origin(transaction, *, run):
            calls.append(run.id)
            saved = await TriggerService(transaction).get_occurrence(tenant_id=run.tenant_id, occurrence_id=run.source.owner_id)
            assert saved.run_id == run.id
            return A2AInputVisibility(WorkspaceSubject(saved.origin_kind, saved.origin_id), saved.origin_conversation_id)
        origin = await A2AService(tx).input_visibility(tenant_id=p.tenant_id, request_id=request.id,
            resolve_product_origin=resolve_origin)
        assert calls == [run_id] and origin.subject == WorkspaceSubject("group", group.id) and origin.conversation_id == topic.id
