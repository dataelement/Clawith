"""PostgreSQL guards continuation ownership without relying on application validation."""

from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select, text, update
from sqlalchemy.exc import DBAPIError, IntegrityError
from test_product_input_constraints import graph  # noqa: F401
from test_schema_wave_S2 import _put, _run

from app.infrastructure.database import Base


def attachment(g, owner, *, creator="run"):
    values = {owner + "_id": g[owner], "upload_source_key": str(uuid4()), "filename": "report.txt",
        "media_type": "text/plain", "byte_size": 4, "sha256": "a" * 64, "storage_key": str(uuid4()),
        "storage_revision": "revision", "published_at": g["seed"]["now"],
        "unbound_expires_at": g["seed"]["now"] + timedelta(hours=24)}
    if creator == "run":
        values["created_by_run_id"] = g["run" if owner == "session" else "group_run"]
    else:
        values["uploader_membership_id"] = g["member"]
    return values


async def reject(db, table, g, values, *, check=None):
    with pytest.raises(IntegrityError, match=check or "foreign key constraint"):
        async with db.begin_nested():
            await _put(db, table, g["seed"], **values)


@pytest.mark.parametrize("owner", ["session", "group"])
@pytest.mark.parametrize("case", ["both_creators", "no_creator", "human_bound_message", "run_with_human_origin", "different_run", "different_agent"])
async def test_attachment_creator_and_bound_message_must_agree(db_session, graph, owner, case):  # noqa: F811
    g = graph
    values = attachment(g, owner)
    message = g["reply" if owner == "session" else "group_reply"]
    check = None
    if case == "both_creators":
        values["uploader_membership_id"] = g["member"]
        check = f"ck_{owner}_attachment_creator"
    elif case == "no_creator":
        values.pop("created_by_run_id")
        check = f"ck_{owner}_attachment_creator"
    elif case == "human_bound_message":
        values = attachment(g, owner, creator="human")
        values["bound_message_id"] = message
        check = f"ck_{owner}_attachment_creator"
    elif case == "run_with_human_origin":
        values["origin_input_id" if owner == "session" else "origin_event_id"] = g["inputs" if owner == "session" else "events"][0]
        check = f"ck_{owner}_attachment_creator"
    else:
        values["bound_message_id"] = message
        values["created_by_run_id"] = await _run(db_session, g["seed"], g["agent"] if case == "different_run" else g["other"])
    await reject(db_session, owner + "_attachments", g, values, check=check)


@pytest.mark.parametrize("owner", ["session", "group"])
@pytest.mark.parametrize("binding", ["input", "message"])
async def test_cleanup_cannot_claim_either_kind_of_bound_attachment(db_session, graph, owner, binding):  # noqa: F811
    g = graph
    values = attachment(g, owner, creator="human" if binding == "input" else "run")
    if binding == "input":
        values["origin_input_id" if owner == "session" else "origin_event_id"] = g["inputs" if owner == "session" else "events"][0]
    else:
        values["bound_message_id"] = g["reply" if owner == "session" else "group_reply"]
    identity = await _put(db_session, owner + "_attachments", g["seed"], **values)
    table = Base.metadata.tables[owner + "_attachments"]
    with pytest.raises(IntegrityError, match=f"ck_{owner}_attachment_cleanup"):
        async with db_session.begin_nested():
            await db_session.execute(update(table).where(table.c.id == identity).values(cleanup_claimed_at=g["seed"]["now"]))


@pytest.mark.parametrize("owner", ["session", "group"])
async def test_scheduled_reply_has_real_run_without_a_human_input(db_session, graph, owner):  # noqa: F811
    g = graph
    source = await _run(db_session, g["seed"], g["agent"])
    runs = Base.metadata.tables["agent_runs"]
    await db_session.execute(update(runs).where(runs.c.id == source).values(initiator_kind="trigger"))
    values = {owner + "_id": g[owner], "position": 4, "kind": "reply", "source_run_id": source,
        "agent_id": g["agent"], "message_key": "scheduled", "payload_version": 1, "payload": {}}
    if owner == "group":
        values["conversation_id"] = g["topics"][0]
    table_name = "session_entries" if owner == "session" else "group_events"
    message = await _put(db_session, table_name, g["seed"], **values)
    file = attachment(g, owner)
    file.update(created_by_run_id=source, bound_message_id=message)
    await _put(db_session, owner + "_attachments", g["seed"], **file)
    wrong_agent_run = await _run(db_session, g["seed"], g["other"])
    await reject(db_session, table_name, g, {**values, "position": 5, "message_key": "wrong-agent", "source_run_id": wrong_agent_run})
    await reject(db_session, table_name, g, {**values, "position": 5, "message_key": "missing-run", "source_run_id": uuid4()})


def request(g, **extra):
    return {"source_agent_id": g["agent"], "source_run_id": g["run"], "source_call_id": str(uuid4()),
        "target_agent_id": g["other"], "intent": "consult", "payload_version": 1, "payload": {},
        "delegation_version": 1, "delegated_connections": [], "admission": "pending", "result_version": 1,
        "source_delivery": "awaiting_result", **extra}


async def test_a2a_delivery_recipient_must_belong_to_original_source_agent(db_session, graph):  # noqa: F811
    g = graph
    same_agent = await _run(db_session, g["seed"], g["agent"])
    await _put(db_session, "a2a_requests", g["seed"], **request(g, delivery_run_id=same_agent))
    other_agent = await _run(db_session, g["seed"], g["other"])
    await reject(db_session, "a2a_requests", g, request(g, delivery_run_id=other_agent))


@pytest.mark.parametrize("invalid", [[], {"padding": "x" * 65536}])
async def test_temp_manifest_requires_a_bounded_object(db_session, graph, invalid):  # noqa: F811
    g = graph
    await _put(db_session, "a2a_requests", g["seed"], **request(g, temp_files_manifest={"files": []}))
    await reject(db_session, "a2a_requests", g, request(g, temp_files_manifest=invalid), check="ck_a2a_temp_files_manifest")


@pytest.mark.parametrize("invalid", [{}, "not-array", list(map(str, range(257)))])
async def test_reply_identity_array_type_and_count_are_database_bounded(db_session, graph, invalid):  # noqa: F811
    g = graph
    values = {"agent_id": g["agent"], "channel_configuration_id": g["channels"][0], "session_reply_id": g["reply"],
        "destination": "D1", "delivery_key": str(uuid4()), "attempt_count": 0, "delivery_status": "pending",
        "provider_reply_ids": invalid}
    with pytest.raises(DBAPIError) as failed:
        async with db_session.begin_nested():
            await _put(db_session, "channel_deliveries", g["seed"], **values)
    assert getattr(failed.value.orig, "sqlstate", None) in ("23514", "22023")


async def test_reply_identity_bound_and_gin_index_exist(db_session, graph, test_database):  # noqa: F811
    g = graph
    identities = [str(index).zfill(512) for index in range(256)]
    identity = await _put(db_session, "channel_deliveries", g["seed"], agent_id=g["agent"],
        channel_configuration_id=g["channels"][0], session_reply_id=g["reply"], destination="D1", delivery_key="bounded",
        attempt_count=1, delivery_status="delivered", provider_reply_ids=identities)
    table = Base.metadata.tables["channel_deliveries"]
    assert await db_session.scalar(select(table.c.id).where(table.c.provider_reply_ids.contains([identities[128]]))) == identity
    index = await db_session.scalar(text("SELECT indexdef FROM pg_indexes WHERE schemaname = :schema AND indexname = 'ix_channel_delivery_reply_ids'"), {"schema": test_database.schema})
    assert index is not None and "USING gin (provider_reply_ids)" in index
