import uuid
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.api.enterprise import _llm_management_tenant_id, _llm_model_scope


def _user(tenant_id: uuid.UUID, role: str = "org_admin") -> SimpleNamespace:
    return SimpleNamespace(tenant_id=tenant_id, role=role)


def test_org_admin_cannot_select_another_tenant_for_llm_models() -> None:
    user = _user(uuid.uuid4())

    with pytest.raises(HTTPException, match="Cannot manage another tenant's models") as error:
        _llm_management_tenant_id(user, str(uuid.uuid4()))

    assert error.value.status_code == 403


def test_platform_admin_can_select_another_tenant_for_llm_models() -> None:
    target_tenant_id = uuid.uuid4()

    assert _llm_management_tenant_id(_user(uuid.uuid4(), "platform_admin"), str(target_tenant_id)) == target_tenant_id


def test_org_admin_model_mutation_query_is_tenant_scoped() -> None:
    tenant_id = uuid.uuid4()
    statement = _llm_model_scope(uuid.uuid4(), _user(tenant_id))
    where_clause = " ".join(str(criteria) for criteria in statement._where_criteria)

    assert "llm_models.tenant_id" in where_clause
    assert tenant_id.hex in str(statement.compile(compile_kwargs={"literal_binds": True}))


def test_platform_admin_model_mutation_query_is_not_tenant_scoped() -> None:
    statement = _llm_model_scope(uuid.uuid4(), _user(uuid.uuid4(), "platform_admin"))
    where_clause = " ".join(str(criteria) for criteria in statement._where_criteria)

    assert "llm_models.tenant_id" not in where_clause
