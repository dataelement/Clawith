import pytest
from unittest.mock import MagicMock, patch
from fastapi import HTTPException
from types import SimpleNamespace

from app.api import admin as admin_api
from app.api import tenants as tenants_api
from app.services.platform_service import platform_service
from tests.test_auth import RecordingDB, DummyResult

@pytest.mark.asyncio
async def test_get_platform_settings_sso_toggle_default():
    """Verify that get_platform_settings returns sso_custom_domain_redirect_enabled by default."""
    db = RecordingDB(responses=[
        DummyResult(),  # allow_self_create_company lookup -> None (default True)
        DummyResult(),  # invitation_code_enabled lookup -> None (default False)
        DummyResult(),  # sso_custom_domain_redirect_enabled lookup -> None (default True)
    ])
    
    current_user = MagicMock()
    settings = await admin_api.get_platform_settings(current_user=current_user, db=db)
    
    assert settings.sso_custom_domain_redirect_enabled is True
    assert settings.allow_self_create_company is True
    assert settings.invitation_code_enabled is False


@pytest.mark.asyncio
async def test_get_platform_settings_sso_toggle_disabled():
    """Verify that get_platform_settings returns sso_custom_domain_redirect_enabled False if set."""
    setting_record = SimpleNamespace(key="sso_custom_domain_redirect_enabled", value={"enabled": False})
    db = RecordingDB(responses=[
        DummyResult(),  # allow_self_create_company -> None
        DummyResult(),  # invitation_code_enabled -> None
        DummyResult(values=[setting_record]),  # sso_custom_domain_redirect_enabled -> disabled
    ])
    
    current_user = MagicMock()
    settings = await admin_api.get_platform_settings(current_user=current_user, db=db)
    assert settings.sso_custom_domain_redirect_enabled is False


@pytest.mark.asyncio
async def test_resolve_tenant_by_domain_sso_toggle():
    """Verify that resolve_tenant_by_domain respects the sso_custom_domain_redirect_enabled toggle."""
    # When enabled, custom domain lookup should match the tenant by domain
    active_tenant = SimpleNamespace(id="tenant-id", name="Acme", slug="acme", sso_enabled=True, sso_domain="https://acme.com", is_active=True)
    
    # Check 1: SSO toggle enabled, matches tenant
    db_enabled = RecordingDB(responses=[
        DummyResult(),  # sso_custom_domain_redirect_enabled -> None (default True)
        DummyResult(values=[active_tenant]),  # Match for https://acme.com
    ])
    res = await tenants_api.resolve_tenant_by_domain(domain="acme.com", db=db_enabled)
    assert res["id"] == "tenant-id"
    assert res["sso_domain"] == "https://acme.com"

    # Check 2: SSO toggle disabled, does not match tenant by domain, falls back or fails
    setting_disabled = SimpleNamespace(key="sso_custom_domain_redirect_enabled", value={"enabled": False})
    db_disabled = RecordingDB(responses=[
        DummyResult(values=[setting_disabled]),  # sso_custom_domain_redirect_enabled -> False
        DummyResult(),  # Fallback search slug (which fails)
    ])
    with pytest.raises(HTTPException) as exc:
        await tenants_api.resolve_tenant_by_domain(domain="acme.com", db=db_disabled)
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_get_tenant_sso_base_url_toggle():
    """Verify that get_tenant_sso_base_url respects the sso_custom_domain_redirect_enabled toggle."""
    tenant = SimpleNamespace(slug="acme", sso_domain="https://acme.com")
    
    # 1. Enabled: returns the custom sso_domain
    db_enabled = RecordingDB(responses=[
        DummyResult(),  # sso_custom_domain_redirect_enabled -> None (default True)
    ])
    url = await platform_service.get_tenant_sso_base_url(db=db_enabled, tenant=tenant)
    assert url == "https://acme.com"

    # 2. Disabled: falls back to generated slug domain URL
    setting_disabled = SimpleNamespace(key="sso_custom_domain_redirect_enabled", value={"enabled": False})
    db_disabled = RecordingDB(responses=[
        DummyResult(values=[setting_disabled]),  # sso_custom_domain_redirect_enabled -> False
    ])
    with patch.object(platform_service, "get_public_base_url", return_value="https://try.clawith.ai"):
        url = await platform_service.get_tenant_sso_base_url(db=db_disabled, tenant=tenant)
        assert url == "https://acme.clawith.ai"
