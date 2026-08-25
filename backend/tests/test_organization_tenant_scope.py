import uuid
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.api import organization
from app.schemas.schemas import UserUpdate


class DummyResult:
    def __init__(self, value=None):
        self.value = value

    def scalar_one_or_none(self):
        return self.value

    def scalars(self):
        return self

    def all(self):
        return []


class RecordingDB:
    def __init__(self, responses):
        self.responses = list(responses)
        self.statements = []

    async def execute(self, statement):
        self.statements.append(statement)
        return self.responses.pop(0)


def _org_admin(*, tenant_id: uuid.UUID, identity_id: uuid.UUID) -> SimpleNamespace:
    return SimpleNamespace(
        role="org_admin",
        tenant_id=tenant_id,
        identity_id=identity_id,
        identity=SimpleNamespace(is_platform_admin=False),
    )


@pytest.mark.asyncio
async def test_org_admin_cannot_load_user_from_another_tenant_for_update() -> None:
    tenant_id = uuid.uuid4()
    db = RecordingDB([DummyResult()])

    with pytest.raises(HTTPException) as raised:
        await organization.admin_update_user(
            user_id=uuid.uuid4(),
            data=UserUpdate(display_name="Changed"),
            current_user=_org_admin(tenant_id=tenant_id, identity_id=uuid.uuid4()),
            db=db,
        )

    assert raised.value.status_code == 404
    assert "users.tenant_id" in str(db.statements[0])


@pytest.mark.asyncio
async def test_org_admin_cannot_list_users_from_another_tenant() -> None:
    tenant_id = uuid.uuid4()
    requested_tenant_id = uuid.uuid4()
    db = RecordingDB([DummyResult()])

    users = await organization.list_users(
        tenant_id=requested_tenant_id,
        current_user=_org_admin(tenant_id=tenant_id, identity_id=uuid.uuid4()),
        db=db,
    )

    assert users == []
    assert db.statements[0].compile().params["tenant_id_1"] == tenant_id


@pytest.mark.asyncio
async def test_org_admin_cannot_change_another_members_global_login_email() -> None:
    tenant_id = uuid.uuid4()
    current_identity_id = uuid.uuid4()
    target = SimpleNamespace(id=uuid.uuid4(), tenant_id=tenant_id, identity_id=uuid.uuid4())
    db = RecordingDB([DummyResult(target)])

    with pytest.raises(HTTPException) as raised:
        await organization.admin_update_user(
            user_id=target.id,
            data=UserUpdate(email="new-address@example.com"),
            current_user=_org_admin(tenant_id=tenant_id, identity_id=current_identity_id),
            db=db,
        )

    assert raised.value.status_code == 403
    assert raised.value.detail == "Cannot modify another user's login email"
