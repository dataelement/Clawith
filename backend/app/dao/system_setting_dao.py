"""DAO for the system_settings key-value table."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select

from app.dao.base import BaseDAO
from app.models.system_settings import SystemSetting


class SystemSettingDAO(BaseDAO[SystemSetting]):
    """Typed access layer for platform-level system settings."""

    def __init__(self) -> None:
        super().__init__(SystemSetting)

    async def get_by_key(self, key: str) -> SystemSetting | None:
        """Fetch a single SystemSetting row by its primary key."""
        async with self.session() as db:
            result = await db.execute(select(SystemSetting).where(SystemSetting.key == key))
            return result.scalar_one_or_none()

    async def get_value(self, key: str, default: Any = None) -> Any:
        """Return the JSON value for a key, or *default* when the row is absent."""
        setting = await self.get_by_key(key)
        if setting is None:
            return default
        return setting.value

    async def is_invitation_code_enabled(self) -> bool:
        """Return whether invitation-code enforcement is active."""
        value = await self.get_value("invitation_code_enabled", {})
        return bool(value.get("enabled", False))

    async def is_sso_custom_domain_redirect_enabled(self) -> bool:
        """Return whether cross-domain SSO redirect is globally enabled."""
        value = await self.get_value("sso_custom_domain_redirect_enabled", {})
        return bool(value.get("enabled", True))


system_setting_dao = SystemSettingDAO()
