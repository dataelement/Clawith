import pytest
import jwt
from datetime import datetime, timedelta, timezone
from app.core.security import (
    hash_password,
    verify_password,
    create_access_token,
    decode_access_token
)
from app.config import get_settings

pytestmark = pytest.mark.unit


def test_password_hashing():
    """Test password hashing and verification"""
    password = "my_test_password"

    # Test hashing
    hashed = hash_password(password)
    assert hashed is not None
    assert isinstance(hashed, str)
    assert len(hashed) > 0

    # Test verification
    assert verify_password(password, hashed) is True
    assert verify_password("wrong_password", hashed) is False


def test_password_hash_different_salt():
    """Test that same passwords produce different hashes due to salt"""
    password = "same_password"

    hash1 = hash_password(password)
    hash2 = hash_password(password)

    assert hash1 != hash2  # Different salts should produce different hashes


def test_create_access_token():
    """Test creating an access token"""
    settings = get_settings()
    user_id = "test-user-id"
    role = "member"

    token = create_access_token(user_id, role)

    assert token is not None
    assert isinstance(token, str)

    # Decode and verify the token
    decoded = jwt.decode(token, settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM])

    assert decoded["sub"] == user_id
    assert decoded["role"] == role
    assert "exp" in decoded

    # Check expiration is in the future
    exp_time = datetime.fromtimestamp(decoded["exp"], tz=timezone.utc)
    now = datetime.now(timezone.utc)
    assert exp_time > now


def test_create_access_token_with_custom_expiry():
    """Test creating an access token with custom expiry"""
    settings = get_settings()
    user_id = "test-user-id"
    role = "admin"
    expiry_delta = timedelta(minutes=30)

    token = create_access_token(user_id, role, expires_delta=expiry_delta)

    # Decode and verify the token
    decoded = jwt.decode(token, settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM])

    assert decoded["sub"] == user_id
    assert decoded["role"] == role

    # Check that expiration is approximately 30 minutes from now
    exp_time = datetime.fromtimestamp(decoded["exp"], tz=timezone.utc)
    expected_exp = datetime.now(timezone.utc) + expiry_delta

    # Allow for a small time difference due to processing time
    assert abs((exp_time - expected_exp).total_seconds()) < 5


def test_decode_valid_token():
    """Test decoding a valid token"""
    settings = get_settings()
    user_id = "test-user-id"
    role = "member"

    # Create a valid token
    token = create_access_token(user_id, role)

    # Decode it using the security function
    decoded = decode_access_token(token)

    assert decoded["sub"] == user_id
    assert decoded["role"] == role
    assert "exp" in decoded


def test_decode_invalid_token():
    """Test decoding an invalid token raises exception"""
    invalid_token = "invalid.token.string"

    with pytest.raises(Exception):  # JWTError or HTTPException
        decode_access_token(invalid_token)


def test_decode_expired_token():
    """Test decoding an expired token raises exception"""
    settings = get_settings()
    user_id = "test-user-id"
    role = "member"

    # Create a token that expired 1 hour ago
    expired_time = datetime.now(timezone.utc) - timedelta(hours=1)
    expired_payload = {
        "sub": user_id,
        "role": role,
        "exp": expired_time,
    }
    expired_token = jwt.encode(expired_payload, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)

    with pytest.raises(Exception):  # JWTError or HTTPException for expired token
        decode_access_token(expired_token)


def test_token_contains_correct_fields():
    """Test that tokens contain the expected fields"""
    user_id = "some-user-id"
    role = "admin"
    token = create_access_token(user_id, role)

    settings = get_settings()
    decoded = jwt.decode(token, settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM])

    # Verify required fields
    assert "sub" in decoded
    assert "role" in decoded
    assert "exp" in decoded

    # Verify values
    assert decoded["sub"] == user_id
    assert decoded["role"] == role
