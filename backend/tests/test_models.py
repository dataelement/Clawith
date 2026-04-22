import pytest
from datetime import datetime
from app.models.user import User
from app.models.agent import Agent
from app.models.tenant import Tenant
from app.core.security import hash_password, verify_password

pytestmark = pytest.mark.unit


def test_user_model_creation():
    """Test basic user model creation"""
    user = User(
        username="testuser",
        email="test@example.com",
        password_hash=hash_password("password123"),
        display_name="Test User"
    )

    assert user.username == "testuser"
    assert user.email == "test@example.com"
    assert user.display_name == "Test User"
    assert user.password_hash is not None
    assert user.is_active is True  # default value
    assert user.created_at is not None
    assert isinstance(user.created_at, datetime)


def test_user_password_hashing():
    """Test user password hashing and verification"""
    plain_password = "mysecretpassword"
    hashed = hash_password(plain_password)

    # Verify the password can be verified correctly
    assert verify_password(plain_password, hashed) is True

    # Verify wrong password fails
    assert verify_password("wrongpassword", hashed) is False

    # Verify same passwords produce different hashes (due to salt)
    hash1 = hash_password(plain_password)
    hash2 = hash_password(plain_password)
    assert hash1 != hash2


def test_agent_model_creation():
    """Test basic agent model creation"""
    agent = Agent(
        name="Test Agent",
        description="A test agent",
        is_active=True
    )

    assert agent.name == "Test Agent"
    assert agent.description == "A test agent"
    assert agent.is_active is True
    assert agent.created_at is not None
    assert isinstance(agent.created_at, datetime)


def test_tenant_model_creation():
    """Test basic tenant model creation"""
    tenant = Tenant(
        name="Test Tenant",
        slug="test-tenant",
        im_provider="web_only"
    )

    assert tenant.name == "Test Tenant"
    assert tenant.slug == "test-tenant"
    assert tenant.im_provider == "web_only"
    assert tenant.created_at is not None
    assert isinstance(tenant.created_at, datetime)
    assert tenant.is_active is True  # default value
