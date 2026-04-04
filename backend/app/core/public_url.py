"""Utility functions for getting platform public URL."""

import os
from urllib.parse import urlparse


def get_public_base_url_sync() -> str:
    """Get the platform public base URL (sync version - only checks env var).

    For async version with database lookup, use get_public_base_url_async().
    """
    env_url = os.environ.get("PUBLIC_BASE_URL", "").strip()
    if env_url:
        return env_url.rstrip("/")
    return ""


async def get_public_base_url_async(db) -> str:
    """Get the platform public base URL from database.

    Args:
        db: Database session

    Returns:
        The public base URL or empty string if not set
    """
    try:
        from sqlalchemy import select
        from app.models.system_settings import SystemSetting

        result = await db.execute(select(SystemSetting).where(SystemSetting.key == "platform"))
        setting = result.scalar_one_or_none()
        if setting and setting.value:
            url = setting.value.get("public_base_url", "")
            if url:
                return url.rstrip("/")
    except Exception:
        pass
    return ""


def get_sso_domain_from_slug(slug: str, public_url: str = "") -> str:
    """Generate SSO domain from slug using the platform public URL.

    Args:
        slug: The tenant slug (subdomain)
        public_url: Optional pre-fetched public URL

    Returns:
        Full SSO domain like "slug.example.com" or "slug.example.com:3008"
    """
    if public_url:
        parsed = urlparse(public_url)
        return f"{slug}.{parsed.netloc}"
    else:
        return f"{slug}.clawith.ai"
