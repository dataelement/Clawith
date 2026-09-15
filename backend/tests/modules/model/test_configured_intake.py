"""Autonomous intake consumes the stored Model protocol without execution or fallback."""

from datetime import UTC, datetime
from uuid import uuid4

import httpx
import pytest
from database.test_schema_wave_S1 import _seed_to_agent
from sqlalchemy import event

from app.infrastructure.errors import InvalidInput, NotFound
from app.infrastructure.http import create_stateless_http_client
from app.modules.credential.public import CredentialKeyring
from app.modules.model.models import ModelRecord
from app.modules.model.public import ModelExecutionService


@pytest.mark.parametrize("protocol", ["openai_chat", "openai_responses", "anthropic", "gemini"])
async def test_configured_protocol_is_read_once_without_http_or_admin(transaction_factory, test_database, protocol):
    async with transaction_factory() as tx:
        seed = await _seed_to_agent(tx.session)
        model = seed["model"]
        model.capabilities, model.settings = {"supports_tool_calling": True}, {"protocol": protocol}
        tenant_id, model_id = seed["tenant"].id, model.id
    def no_http(request):
        pytest.fail("Resolving stored policy must not execute HTTP or validate another Provider")
    statements = []
    def before(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)
    async with create_stateless_http_client(transport=httpx.MockTransport(no_http)) as client:
        service = ModelExecutionService(test_database.sessions, http_client=client,
            credential_keyring=CredentialKeyring(active_key_version="v1", keys={"v1": b"k" * 32}),
            active_continuation_key="v1", continuation_keys={"v1": b"c" * 32})
        event.listen(test_database.engine.sync_engine, "before_cursor_execute", before)
        try:
            value = await service.resolve_configured_policy(tenant_id=tenant_id, model_id=model_id)
            assert value.policy.protocol == protocol and value.profile.model_id == model_id
            assert len(statements) == 1
        finally:
            event.remove(test_database.engine.sync_engine, "before_cursor_execute", before)
        with pytest.raises(NotFound):
            await service.resolve_configured_policy(tenant_id=uuid4(), model_id=model_id)
        for settings, enabled, archived in (({}, True, None), ({"protocol": "future"}, True, None),
                ({"protocol": protocol}, False, None), ({"protocol": protocol}, True, datetime.now(UTC))):
            async with transaction_factory() as tx:
                row = await tx.session.get(ModelRecord, model_id)
                row.settings, row.enabled, row.archived_at = settings, enabled, archived
            with pytest.raises(InvalidInput):
                await service.resolve_configured_policy(tenant_id=tenant_id, model_id=model_id)
