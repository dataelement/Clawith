"""Security utilities: JWT, password hashing, and authentication dependencies."""

import base64
import os
import uuid
from datetime import datetime, timedelta, timezone

import bcrypt
from Crypto.Cipher import AES
from Crypto.Util.Padding import pad, unpad
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import get_settings
from app.database import get_db

settings = get_settings()

# Bearer token scheme
security = HTTPBearer()


def encrypt_data(plaintext: str, key: str) -> str:
    """Encrypt a string using AES-256-CBC with the given key.

    Args:
        plaintext: The string to encrypt
        key: The encryption key (will be hashed to 32 bytes)

    Returns:
        Base64-encoded encrypted string with IV prefix
    """
    if not plaintext:
        return ""

    # Derive 32-byte key from the secret key
    key_bytes = key.encode("utf-8")
    # Use SHA-256 hash to get exactly 32 bytes for AES-256
    import hashlib

    aes_key = hashlib.sha256(key_bytes).digest()

    # Generate random 16-byte IV
    iv = os.urandom(16)

    # Create cipher and encrypt
    cipher = AES.new(aes_key, AES.MODE_CBC, iv)
    padded_data = pad(plaintext.encode("utf-8"), AES.block_size)
    encrypted = cipher.encrypt(padded_data)

    # Prepend IV to ciphertext and encode as base64
    result = base64.b64encode(iv + encrypted).decode("utf-8")
    return result


def decrypt_data(ciphertext: str, key: str) -> str:
    """Decrypt a string encrypted with encrypt_data.

    Args:
        ciphertext: Base64-encoded encrypted string with IV prefix
        key: The encryption key (must match the key used for encryption)

    Returns:
        Decrypted plaintext string

    Raises:
        ValueError: If decryption fails (wrong key, corrupted data, etc.)
    """
    if not ciphertext:
        return ""

    try:
        # Decode base64
        raw = base64.b64decode(ciphertext)

        # Extract IV (first 16 bytes) and ciphertext
        iv = raw[:16]
        encrypted = raw[16:]

        # Derive key
        import hashlib

        aes_key = hashlib.sha256(key.encode("utf-8")).digest()

        # Decrypt
        cipher = AES.new(aes_key, AES.MODE_CBC, iv)
        padded_data = cipher.decrypt(encrypted)
        plaintext = unpad(padded_data, AES.block_size).decode("utf-8")

        return plaintext
    except Exception as e:
        raise ValueError(f"Decryption failed: {e}") from e


def hash_password(password: str) -> str:
    """Hash a password using bcrypt."""
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a password against its hash."""
    return bcrypt.checkpw(plain_password.encode("utf-8"), hashed_password.encode("utf-8"))


def create_access_token(user_id: str, role: str, expires_delta: timedelta | None = None) -> str:
    """Create a JWT access token."""
    expire = datetime.now(timezone.utc) + (
        expires_delta or timedelta(minutes=settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES)
    )
    to_encode = {
        "sub": user_id,
        "role": role,
        "exp": expire,
    }
    return jwt.encode(to_encode, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def decode_access_token(token: str) -> dict:
    """Decode and validate a JWT access token."""
    try:
        payload = jwt.decode(token, settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM])
        return payload
    except JWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
        )


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: AsyncSession = Depends(get_db),
):
    """Dependency to get the current authenticated and active user."""
    from app.models.user import User

    payload = decode_access_token(credentials.credentials)
    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")

    result = await db.execute(
        select(User)
        .where(User.id == uuid.UUID(user_id))
        .options(selectinload(User.identity))
    )
    user = result.scalar_one_or_none()
    if not user or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found or inactive")
    return user


async def get_authenticated_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: AsyncSession = Depends(get_db),
):
    """Dependency to get the current authenticated user (even if not active yet)."""
    from app.models.user import User

    payload = decode_access_token(credentials.credentials)
    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")

    result = await db.execute(
        select(User)
        .where(User.id == uuid.UUID(user_id))
        .options(selectinload(User.identity))
    )
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")
    return user


async def get_current_admin(current_user=Depends(get_current_user)):
    """Dependency to require admin role (platform_admin or org_admin)."""
    identity_is_platform_admin = bool(getattr(getattr(current_user, "identity", None), "is_platform_admin", False))
    if current_user.role not in ("platform_admin", "org_admin") and not identity_is_platform_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required")
    return current_user


# Role hierarchy: higher index = more privileges
ROLE_HIERARCHY = ["member", "agent_admin", "org_admin", "platform_admin"]


def require_role(*allowed_roles: str):
    """Factory to create a dependency that checks if the user has one of the allowed roles.

    Usage:
        @router.post("/", dependencies=[Depends(require_role("org_admin", "platform_admin"))])
        async def my_endpoint(...):
    """
    async def _check(current_user=Depends(get_current_user)):
        identity_is_platform_admin = bool(getattr(getattr(current_user, "identity", None), "is_platform_admin", False))
        if current_user.role not in allowed_roles and not ("platform_admin" in allowed_roles and identity_is_platform_admin):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"需要以下角色之一: {', '.join(allowed_roles)}",
            )
        return current_user
    return _check
