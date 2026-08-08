"""Regression coverage for global system-setting authorization."""

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app.api.enterprise import (
    SettingUpdate,
    _require_system_setting_access,
    get_system_setting,
    update_system_setting,
)


def _user(*, role: str, tenant_id: uuid.UUID | None = None, platform_identity: bool = False) -> SimpleNamespace:
    return SimpleNamespace(
        role=role,
        tenant_id=tenant_id,
        identity=SimpleNamespace(is_platform_admin=platform_identity),
    )


def test_member_cannot_read_credential_system_setting() -> None:
    with pytest.raises(HTTPException, match="Platform admin") as error:
        _require_system_setting_access("system_email_platform", _user(role="member"))

    assert error.value.status_code == 403


def test_org_admin_cannot_modify_global_system_setting() -> None:
    with pytest.raises(HTTPException, match="Platform admin") as error:
        _require_system_setting_access("jina_api_key", _user(role="org_admin", tenant_id=uuid.uuid4()))

    assert error.value.status_code == 403


def test_org_admin_can_manage_own_company_intro_only() -> None:
    tenant_id = uuid.uuid4()
    _require_system_setting_access(
        f"company_intro_{tenant_id}",
        _user(role="org_admin", tenant_id=tenant_id),
    )


def test_org_admin_cannot_manage_another_tenant_company_intro() -> None:
    with pytest.raises(HTTPException) as error:
        _require_system_setting_access(
            f"company_intro_{uuid.uuid4()}",
            _user(role="org_admin", tenant_id=uuid.uuid4()),
        )

    assert error.value.status_code == 403


def test_platform_admin_can_manage_global_and_tenant_scoped_settings() -> None:
    platform_admin = _user(role="platform_admin")

    _require_system_setting_access("system_email_platform", platform_admin)
    _require_system_setting_access(f"company_intro_{uuid.uuid4()}", platform_admin)


@pytest.mark.asyncio
async def test_endpoints_reject_unauthorized_credential_access_before_querying_database() -> None:
    db = AsyncMock()
    member = _user(role="member")
    org_admin = _user(role="org_admin", tenant_id=uuid.uuid4())

    with pytest.raises(HTTPException) as get_error:
        await get_system_setting("jina_api_key", current_user=member, db=db)
    with pytest.raises(HTTPException) as put_error:
        await update_system_setting(
            "system_email_platform",
            SettingUpdate(value={"SYSTEM_SMTP_PASSWORD": "attempted-change"}),
            current_user=org_admin,
            db=db,
        )

    assert get_error.value.status_code == 403
    assert put_error.value.status_code == 403
    db.execute.assert_not_awaited()
