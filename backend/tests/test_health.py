import pytest

pytestmark = pytest.mark.smoke


def test_health_check(client):
    """Test the health check endpoint"""
    response = client.get("/api/health")
    assert response.status_code == 200

    data = response.json()
    assert "status" in data
    assert data["status"] == "ok"
    assert "version" in data
    assert isinstance(data["version"], str)


def test_health_check_response_model(client):
    """Test that health check returns expected model structure"""
    response = client.get("/api/health")
    assert response.status_code == 200

    data = response.json()
    expected_keys = {"status", "version"}
    assert set(data.keys()) >= expected_keys
