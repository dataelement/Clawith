"""Product correlations are rejected by PostgreSQL even without service validation."""

from uuid import uuid4

import pytest
from sqlalchemy.exc import IntegrityError
from test_schema_wave_S1 import _seed_to_agent
from test_schema_wave_S2 import _put, _run, _second_agent


@pytest.fixture
async def graph(db_session):
    seed = await _seed_to_agent(db_session)
    agent, member = seed["agent"].id, seed["membership"].id
    other = (await _second_agent(db_session, seed)).id
    session = await _put(db_session, "sessions", seed, agent_id=agent, membership_id=member, next_position=4,
        goal_enabled=False, goal_configuration_version=1, goal_configuration={})
    inputs = [await _put(db_session, "session_entries", seed, session_id=session, agent_id=agent,
        position=i + 1, kind="input", source_key=str(i), payload_version=1, payload={}) for i in range(2)]
    run = await _run(db_session, seed, agent)
    await _put(db_session, "session_run_links", seed, session_id=session, agent_id=agent, input_id=inputs[0],
        source_key="start", history_cutoff=1, run_id=run, admission="started", result_version=1)
    reply = await _put(db_session, "session_entries", seed, session_id=session, agent_id=agent, position=3,
        kind="reply", origin_input_id=inputs[0], source_run_id=run, message_key="reply", payload_version=1, payload={})
    group = await _put(db_session, "groups", seed, name="Group", announcement="", next_position=4, enabled=True)
    topics = [await _put(db_session, "group_conversations", seed, group_id=group, title=str(i),
        is_default=i == 0, enabled=True, created_by_membership_id=member) for i in range(2)]
    events = [await _put(db_session, "group_events", seed, group_id=group, conversation_id=topics[i],
        position=i + 1, kind="input", source_key=str(i), membership_id=member, payload_version=1, payload={}) for i in range(2)]
    group_run = await _run(db_session, seed, agent)
    await _put(db_session, "group_run_links", seed, group_id=group, conversation_id=topics[0], event_id=events[0],
        agent_id=agent, run_id=group_run, admission="started", result_version=1)
    group_reply = await _put(db_session, "group_events", seed, group_id=group, conversation_id=topics[0],
        position=3, kind="reply", agent_id=agent, origin_event_id=events[0], source_run_id=group_run,
        message_key="reply", payload_version=1, payload={})
    channels = [await _put(db_session, "agent_channel_configurations", seed, agent_id=current, provider="example",
        external_identity=str(current), configuration_version=1, non_secret_config={}, enabled=True) for current in (agent, other)]
    contexts = [await _put(db_session, "channel_reply_contexts", seed, agent_id=current, channel_configuration_id=channel,
        external_event_id="event", context_version=1, key_version="v1", nonce=b"n" * 12,
        ciphertext=b"c" * 17, expires_at=seed["now"]) for current, channel in zip((agent, other), channels, strict=True)]
    return {"seed": seed, "agent": agent, "member": member, "other": other, "session": session, "inputs": inputs, "run": run, "reply": reply,
        "group": group, "topics": topics, "events": events, "group_run": group_run, "group_reply": group_reply, "channels": channels, "contexts": contexts}


async def rejected(db, table, seed, values, expected):
    with pytest.raises(IntegrityError, match=expected):
        async with db.begin_nested():
            await _put(db, table, seed, **values)


async def test_session_reply_must_use_its_source_runs_input(db_session, graph):
    g = graph
    values = {"session_id": g["session"], "agent_id": g["agent"], "position": 4, "kind": "reply",
        "origin_input_id": g["inputs"][1], "source_run_id": g["run"], "message_key": "wrong", "payload_version": 1, "payload": {}}
    await rejected(db_session, "session_entries", g["seed"], values, "fk_session_entries_source_run")


@pytest.mark.parametrize("owner", ["session", "group"])
async def test_human_input_cannot_claim_execution_message_source(db_session, graph, owner):
    g = graph
    values = {owner + "_id": g[owner], "position": 4, "kind": "input", "source_key": "human",
        "payload_version": 1, "payload": {}, "source_run_id": g["run" if owner == "session" else "group_run"]}
    if owner == "session":
        values["agent_id"] = g["agent"]
    else:
        values.update({"conversation_id": g["topics"][0], "membership_id": g["member"]})
    await rejected(db_session, "session_entries" if owner == "session" else "group_events", g["seed"], values,
        "ck_session_entries_execution_source" if owner == "session" else "ck_group_events_execution_source")


@pytest.mark.parametrize("drift", ["agent", "input", "conversation", "null_conversation"])
async def test_group_reply_must_match_exact_source_link(db_session, graph, drift):
    g = graph
    values = {"group_id": g["group"], "conversation_id": g["topics"][0], "position": 4, "kind": "reply", "agent_id": g["agent"],
        "origin_event_id": g["events"][0], "source_run_id": g["group_run"], "message_key": "wrong", "payload_version": 1, "payload": {}}
    field, value = {"agent": ("agent_id", g["other"]), "input": ("origin_event_id", g["events"][1]),
        "conversation": ("conversation_id", g["topics"][1]), "null_conversation": ("conversation_id", None)}[drift]
    values[field] = value
    await rejected(db_session, "group_events", g["seed"], values,
        "ck_group_events_execution_source" if drift == "null_conversation" else "fk_group_events_source_run")


async def test_group_run_link_cannot_move_input_to_another_topic(db_session, graph):
    g = graph
    await rejected(db_session, "group_run_links", g["seed"], {"group_id": g["group"],
        "conversation_id": g["topics"][1], "event_id": g["events"][0], "agent_id": g["other"],
        "admission": "pending", "result_version": 1}, "foreign key constraint")


@pytest.mark.parametrize("drift", ["other_context", "reply_as_input", "two_sources", "no_source"])
async def test_channel_route_requires_correct_agent_context_and_one_input(db_session, graph, drift):
    g = graph
    values = {"agent_id": g["agent"], "channel_configuration_id": g["channels"][0], "external_event_id": "route",
        "session_input_id": g["inputs"][0], "reply_context_id": g["contexts"][0], "destination": "person"}
    if drift == "other_context":
        values["reply_context_id"] = g["contexts"][1]
    elif drift == "reply_as_input":
        values["session_input_id"] = g["reply"]
    elif drift == "two_sources":
        values["group_input_id"] = g["events"][0]
    else:
        values["session_input_id"] = None
    await rejected(db_session, "channel_input_routes", g["seed"], values,
        "ck_channel_input_route_source" if drift in ("two_sources", "no_source") else "foreign key constraint")


async def test_one_original_channel_delivery_but_multiple_followups(db_session, graph):
    g = graph
    values = {"agent_id": g["agent"], "channel_configuration_id": g["channels"][0], "session_reply_id": g["reply"],
        "reply_context_id": g["contexts"][0], "reply_operation": "original", "destination": "person", "attempt_count": 0, "delivery_status": "pending"}
    await _put(db_session, "channel_deliveries", g["seed"], **values, delivery_key="first")
    await rejected(db_session, "channel_deliveries", g["seed"], {**values, "delivery_key": "second"}, "uq_channel_original_reply")
    for index in range(2):
        await _put(db_session, "channel_deliveries", g["seed"], **{**values, "reply_operation": "followup"}, delivery_key=f"followup-{index}")


@pytest.mark.parametrize("owner", ["session", "group"])
@pytest.mark.parametrize("drift", ["half_publication", "claimed_bound", "reply_origin"])
async def test_attachment_publication_cleanup_and_origin_constraints(db_session, graph, owner, drift):
    g = graph
    values = {owner + "_id": g[owner], "uploader_membership_id": g["member"], "upload_source_key": "upload",
        "filename": "file", "media_type": "text/plain", "byte_size": 1, "sha256": "a" * 64,
        "storage_key": "attachments/" + str(uuid4()), "unbound_expires_at": g["seed"]["now"]}
    if drift == "half_publication":
        values["storage_revision"] = "rev"
        expected = f"ck_{owner}_attachment_publication"
    else:
        field = "origin_input_id" if owner == "session" else "origin_event_id"
        values[field] = (g["inputs"][0] if owner == "session" else g["events"][0]) if drift == "claimed_bound" else g["reply" if owner == "session" else "group_reply"]
        if drift == "claimed_bound":
            values["cleanup_claimed_at"] = g["seed"]["now"]
        expected = f"ck_{owner}_attachment_cleanup" if drift == "claimed_bound" else "foreign key constraint"
    await rejected(db_session, owner + "_attachments", g["seed"], values, expected)


async def test_group_has_only_one_enabled_default_conversation(db_session, graph):
    g = graph
    values = {"group_id": g["group"], "title": "Duplicate", "is_default": True, "enabled": True, "created_by_membership_id": g["member"]}
    await rejected(db_session, "group_conversations", g["seed"], values, "uq_group_default_conversation")
    await rejected(db_session, "group_conversations", g["seed"], {**values, "enabled": False}, "ck_group_default_conversation_enabled")
