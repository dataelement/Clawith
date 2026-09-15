from uuid import uuid4

import pytest

from app.infrastructure.errors import AccessDenied
from app.modules.identity_tenant.public import (
    PlatformPrincipal,
    TenantPrincipal,
    require_admin,
    require_same_tenant,
)


def test_captured_tenant_principal_holds_identity_role_tenant_and_permission_data() -> None:
    tenant_id = uuid4()
    allowed_agent_id = uuid4()
    principal = TenantPrincipal(
        account_id=uuid4(),
        membership_id=uuid4(),
        tenant_id=tenant_id,
        role="member",
        allowed_agent_ids=frozenset({allowed_agent_id}),
    )

    assert principal.allowed_agent_ids == frozenset({allowed_agent_id})
    require_same_tenant(principal, tenant_id)
    with pytest.raises(AccessDenied):
        require_admin(principal)
    with pytest.raises(AccessDenied):
        require_same_tenant(principal, uuid4())


def test_captured_tenant_admin_derives_all_agent_management() -> None:
    principal = TenantPrincipal(
        account_id=uuid4(),
        membership_id=uuid4(),
        tenant_id=uuid4(),
        role="tenant_admin",
    )

    assert principal.can_manage_all_agents is True
    require_admin(principal)


def test_platform_principal_is_excluded_from_tenant_authorization_helpers() -> None:
    principal = PlatformPrincipal(
        account_id=uuid4(), target_tenant_id=uuid4(), platform_role="platform_admin"
    )

    with pytest.raises(AccessDenied):
        require_admin(principal)
    with pytest.raises(AccessDenied):
        require_same_tenant(principal, principal.target_tenant_id)
