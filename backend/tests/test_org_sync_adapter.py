import asyncio
import uuid
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from app.services.org_sync_adapter import (
    BaseOrgSyncAdapter,
    ExternalUser,
    GoogleWorkspaceOrgSyncAdapter,
    SYNC_ADAPTER_CLASSES,
    build_department_path_map,
)


class _DummyAdapter(BaseOrgSyncAdapter):
    provider_type = "feishu"

    @property
    def api_base_url(self) -> str:
        return "https://example.com"

    async def get_access_token(self) -> str:
        return "token"

    async def fetch_departments(self):
        return []

    async def fetch_users(self, department_external_id: str):
        return []


class _FakeDB:
    def __init__(self):
        self.flush_calls = 0

    @asynccontextmanager
    async def begin_nested(self):
        yield

    async def flush(self):
        self.flush_calls += 1


class _SyncAdapterWithFailure(_DummyAdapter):
    def __init__(self):
        super().__init__()
        self.reconcile_called = False
        self.member_counts_updated = False
        self.provider = SimpleNamespace(id="provider-1", config={})

    async def _ensure_provider(self, db):
        return self.provider

    async def _upsert_department(self, db, provider, dept):
        return None

    async def _upsert_member(self, db, provider, user, department_external_id):
        raise ValueError("unionid is required")

    async def _reconcile(self, db, provider_id, sync_start):
        self.reconcile_called = True

    async def _update_member_counts(self, db, provider_id):
        self.member_counts_updated = True

    async def _rebuild_department_paths(self, db, provider_id):
        return {}

    async def _refresh_member_department_paths(self, db, provider_id):
        return None

    async def fetch_departments(self):
        return [SimpleNamespace(external_id="dept-1", name="Dept 1")]

    async def fetch_users(self, department_external_id: str):
        return [ExternalUser(external_id="user-1", name="Alice", unionid="")]


def test_validate_member_identifiers_requires_unionid_for_feishu():
    adapter = _DummyAdapter()
    provider = SimpleNamespace(provider_type="feishu")
    user = ExternalUser(external_id="ou_123", name="Alice", unionid="")

    with pytest.raises(ValueError, match="unionid is required"):
        adapter._validate_member_identifiers(provider, user)


def test_validate_member_identifiers_rejects_unionid_equal_to_external_id():
    adapter = _DummyAdapter()
    provider = SimpleNamespace(provider_type="dingtalk")
    user = ExternalUser(external_id="same-id", name="Bob", unionid="same-id")

    with pytest.raises(ValueError, match="must not equal external_id"):
        adapter._validate_member_identifiers(provider, user)


def test_validate_member_identifiers_allows_wecom_without_unionid():
    adapter = _DummyAdapter()
    provider = SimpleNamespace(provider_type="wecom")
    user = ExternalUser(external_id="zhangsan", name="Zhang San", unionid="")

    adapter._validate_member_identifiers(provider, user)


def test_sync_org_structure_skips_reconcile_after_member_failure():
    adapter = _SyncAdapterWithFailure()
    db = _FakeDB()

    result = asyncio.run(adapter.sync_org_structure(db))

    assert adapter.reconcile_called is False
    assert adapter.member_counts_updated is True
    assert "Reconcile skipped due to partial sync failures" in result["errors"]


def test_google_workspace_adapter_parses_legacy_service_account_json_string():
    adapter = GoogleWorkspaceOrgSyncAdapter(
        config={
            "customer_id": "my_customer",
            "client_secret": '{"client_email":"svc@example.iam.gserviceaccount.com","private_key":"-----BEGIN PRIVATE KEY-----\\\\nabc\\\\n-----END PRIVATE KEY-----\\\\n"}',
            "delegated_admin_email": "admin@example.com",
        }
    )

    assert adapter.customer_id == "my_customer"
    assert adapter.delegated_admin_email == "admin@example.com"
    assert adapter.service_account["client_email"] == "svc@example.iam.gserviceaccount.com"


def test_google_workspace_adapter_uses_admin_authorization_email_as_primary_identity():
    adapter = GoogleWorkspaceOrgSyncAdapter(
        config={
            "client_id": "oauth-client-id.apps.googleusercontent.com",
            "client_secret": "oauth-client-secret",
            "google_admin_authorized_email": "admin@example.com",
        }
    )

    assert adapter.client_id == "oauth-client-id.apps.googleusercontent.com"
    assert adapter.client_secret == "oauth-client-secret"
    assert adapter.delegated_admin_email == "admin@example.com"
    assert adapter.service_account == {}


def test_google_workspace_adapter_registered():
    assert SYNC_ADAPTER_CLASSES["google_workspace"] is GoogleWorkspaceOrgSyncAdapter


def test_build_department_path_map_reconstructs_name_chain_from_internal_tree():
    root_id = uuid.uuid4()
    child_id = uuid.uuid4()
    leaf_id = uuid.uuid4()

    departments = [
        SimpleNamespace(id=leaf_id, external_id="leaf", name="平台组", parent_id=child_id),
        SimpleNamespace(id=child_id, external_id="child", name="研发部", parent_id=root_id),
        SimpleNamespace(id=root_id, external_id="root", name="总部", parent_id=None),
    ]

    path_map = build_department_path_map(departments)

    assert path_map[root_id] == "总部"
    assert path_map[child_id] == "总部/研发部"
    assert path_map[leaf_id] == "总部/研发部/平台组"


def test_build_department_path_map_treats_external_zero_root_as_empty_path():
    root_id = uuid.uuid4()
    child_id = uuid.uuid4()

    departments = [
        SimpleNamespace(id=child_id, external_id="200", name="研发部", parent_id=root_id),
        SimpleNamespace(id=root_id, external_id="0", name="Root", parent_id=None),
    ]

    path_map = build_department_path_map(departments)

    assert path_map[root_id] == ""
    assert path_map[child_id] == "研发部"
