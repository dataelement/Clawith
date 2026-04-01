"""Email verification token lifecycle helpers."""

from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import datetime, timedelta, timezone

from app.config import get_settings
from app.core.events import get_redis

# Key prefixes for Redis
TOKEN_PREFIX = "email_verify:token:"
USER_PREFIX = "email_verify:user:"


class EmailVerificationService:
    """Email verification token lifecycle helpers."""

    def _hash_token(self, token: str) -> str:
        """Hash a raw verification token before persistence or lookup."""
        import hashlib
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    async def create_email_verification_token(self, identity_id: uuid.UUID, email: str) -> tuple[str, datetime]:
        """Create a new 6-digit email verification code and store in Redis."""
        redis = await get_redis()
        user_key = f"{USER_PREFIX}{identity_id}"

        # Invalidate previous code for this user if exists
        old_code_hash = await redis.get(user_key)
        if old_code_hash:
            await redis.delete(f"{TOKEN_PREFIX}{old_code_hash}")

        # Generate a random 6-digit code
        import secrets
        raw_code = "".join([str(secrets.randbelow(10)) for _ in range(6)])
        code_hash = self._hash_token(raw_code)

        now = datetime.now(timezone.utc)
        expiry_minutes = get_settings().EMAIL_VERIFICATION_TOKEN_EXPIRE_MINUTES
        expires_at = now + timedelta(minutes=expiry_minutes)

        # Store the new code with user_id and email
        token_key = f"{TOKEN_PREFIX}{code_hash}"
        ttl_seconds = int(expiry_minutes * 60)

        # Store as JSON with identity_id and email
        import json
        token_data = json.dumps({"identity_id": str(identity_id), "email": email})

        async with redis.pipeline(transaction=True) as pipe:
            pipe.setex(token_key, ttl_seconds, token_data)
            pipe.setex(user_key, ttl_seconds, code_hash)
            await pipe.execute()

        return raw_code, expires_at

    async def build_email_verification_url(self, base_url: str, raw_token: str) -> str:
        """Build the user-facing verification URL. Note: now uses 6-digit code."""
        base = base_url.strip().rstrip("/")
        return f"{base}/verify-email?code={raw_token}"

    async def consume_email_verification_token(self, raw_token: str) -> dict | None:
        """Load a valid verification code from Redis and mark it used (by deleting)."""
        import json

        redis = await get_redis()
        token_hash = self._hash_token(raw_token)
        token_key = f"{TOKEN_PREFIX}{token_hash}"

        token_data_str = await redis.get(token_key)
        if not token_data_str:
            return None

        try:
            token_data = json.loads(token_data_str)
            identity_id = uuid.UUID(token_data["identity_id"])
            email = token_data["email"]
        except (json.JSONDecodeError, KeyError, ValueError):
            return None

        user_key = f"{USER_PREFIX}{identity_id}"

        # Atomic delete to ensure single-use
        async with redis.pipeline(transaction=True) as pipe:
            pipe.delete(token_key)
            pipe.delete(user_key)
            await pipe.execute()

        return {"identity_id": identity_id, "email": email}

    async def send_verification_email(
        self,
        to: str,
        display_name: str,
        verification_code: str,
        expiry_minutes: int,
    ) -> None:
        """Send an email verification code using the configured template."""
        from app.services.system_email_service import send_system_email, render_email_template

        variables = {
            "display_name": display_name,
            "verification_code": verification_code,
            "expiry_minutes": str(expiry_minutes),
        }
        subject, body = await render_email_template("email_verification", variables)
        await send_system_email(to, subject, body)

# Global Instance
email_verification_service = EmailVerificationService()
