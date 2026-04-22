import pytest
from app.models.user import User
from app.models.participant import Participant
from sqlalchemy import select

pytestmark = [pytest.mark.integration, pytest.mark.db]


def test_health_endpoint(client):
    """Test the health check endpoint"""
    response = client.get("/api/health")
    assert response.status_code == 200
    assert "status" in response.json()


def test_registration_config_endpoint(client):
    """Test the registration config endpoint"""
    response = client.get("/api/auth/registration-config")
    assert response.status_code == 200
    assert "invitation_code_required" in response.json()


def test_login_nonexistent_user(client):
    """Test login with non-existent user"""
    login_data = {
        "username": "nonexistent_user",
        "password": "wrong_password"
    }
    response = client.post("/api/auth/login", json=login_data)
    assert response.status_code == 401
    assert "Invalid credentials" in response.json()["detail"]


def test_get_me_unauthorized(client):
    """Test accessing /me endpoint without authentication"""
    response = client.get("/api/auth/me")
    assert response.status_code == 401  # Unauthorized


@pytest.mark.asyncio
async def test_register_new_user(async_client, db_session):
    """Test registering a new user through the API."""
    payload = {
        "username": "new_user",
        "email": "new_user@example.com",
        "password": "password123",
        "display_name": "New User",
    }

    response = await async_client.post("/api/auth/register", json=payload)

    assert response.status_code == 201
    data = response.json()
    assert "access_token" in data
    assert data["user"]["username"] == payload["username"]

    user_result = await db_session.execute(select(User).where(User.username == payload["username"]))
    participant_result = await db_session.execute(
        select(Participant).where(Participant.display_name == payload["display_name"])
    )

    assert user_result.scalar_one_or_none() is not None
    assert participant_result.scalar_one_or_none() is not None
