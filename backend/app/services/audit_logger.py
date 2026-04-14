"""Helper to write audit log entries from background services."""

import json
import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from loguru import logger

from sqlalchemy import text

from app.database import async_session


class AuditAction(str, Enum):
    """Standard audit action types."""

    # Authentication
    LOGIN = "login"
    LOGIN_FAILED = "login_failed"
    LOGOUT = "logout"
    SSO_LOGIN = "sso_login"
    SSO_LOGIN_FAILED = "sso_login_failed"

    # Identity
    IDENTITY_BIND = "identity_bind"
    IDENTITY_UNBIND = "identity_unbind"
    IDENTITY_CREATE = "identity_create"
    IDENTITY_DELETE = "identity_delete"

    # User
    USER_CREATE = "user_create"
    USER_UPDATE = "user_update"
    USER_DELETE = "user_delete"
    USER_ACTIVATE = "user_activate"
    USER_DEACTIVATE = "user_deactivate"

    # Tenant
    TENANT_CREATE = "tenant_create"
    TENANT_UPDATE = "tenant_update"
    TENANT_DELETE = "tenant_delete"
    TENANT_JOIN = "tenant_join"
    TENANT_LEAVE = "tenant_leave"

    # Role
    ROLE_ASSIGN = "role_assign"
    ROLE_REVOKE = "role_revoke"
    ROLE_CREATE = "role_create"
    ROLE_UPDATE = "role_update"
    ROLE_DELETE = "role_delete"

    # Org sync
    ORG_SYNC = "org_sync"
    ORG_DEPARTMENT_CREATE = "org_department_create"
    ORG_DEPARTMENT_UPDATE = "org_department_update"
    ORG_MEMBER_CREATE = "org_member_create"
    ORG_MEMBER_UPDATE = "org_member_update"

    # Agent
    AGENT_CREATE = "agent_create"
    AGENT_UPDATE = "agent_update"
    AGENT_DELETE = "agent_delete"
    AGENT_START = "agent_start"
    AGENT_STOP = "agent_stop"


async def write_audit_log(
    action: str,
    details: dict | None = None,
    agent_id: uuid.UUID | None = None,
    user_id: uuid.UUID | None = None,
) -> None:
    """Write a single audit log entry using raw SQL.

    Uses raw SQL to avoid ORM foreign-key resolution issues when
    called from background tasks where not all models may be loaded.

    Args:
        action: Short action string, e.g. "supervision_tick", "schedule_execute".
        details: JSON-serialisable dict with extra info.
        agent_id: Optional agent UUID.
        user_id: Optional user UUID.
    """
    await _write_log(action, details, agent_id, user_id, None, None)


async def write_identity_audit_log(
    action: str,
    user_id: uuid.UUID | None = None,
    provider_type: str | None = None,
    provider_user_id: str | None = None,
    success: bool = True,
    error_message: str | None = None,
    tenant_id: uuid.UUID | None = None,
) -> None:
    """Write audit log for identity-related events.

    Args:
        action: Identity action (from AuditAction)
        user_id: User performing or affected by the action
        provider_type: Identity provider type (feishu, dingtalk, etc.)
        provider_user_id: User ID in the external system
        success: Whether the action succeeded
        error_message: Error message if failed
        tenant_id: Tenant ID if applicable
    """
    details = {
        "provider_type": provider_type,
        "provider_user_id": provider_user_id,
        "success": success,
    }
    if error_message:
        details["error"] = error_message

    await _write_log(
        action=action,
        details=details,
        user_id=user_id,
        tenant_id=tenant_id,
    )


async def write_role_audit_log(
    action: str,
    user_id: uuid.UUID | None = None,
    target_user_id: uuid.UUID | None = None,
    role_name: str | None = None,
    tenant_id: uuid.UUID | None = None,
    granted_by: uuid.UUID | None = None,
) -> None:
    """Write audit log for role-related events.

    Args:
        action: Role action (from AuditAction)
        user_id: User performing the action
        target_user_id: User being assigned/revoked role
        role_name: Name of the role
        tenant_id: Tenant ID if applicable
        granted_by: User who granted the role
    """
    details = {
        "target_user_id": str(target_user_id) if target_user_id else None,
        "role_name": role_name,
        "granted_by": str(granted_by) if granted_by else None,
    }

    await _write_log(
        action=action,
        details=details,
        user_id=user_id,
        tenant_id=tenant_id,
    )


async def write_tenant_audit_log(
    action: str,
    user_id: uuid.UUID | None = None,
    tenant_id: uuid.UUID | None = None,
    details: dict | None = None,
) -> None:
    """Write audit log for tenant-related events.

    Args:
        action: Tenant action (from AuditAction)
        user_id: User performing the action
        tenant_id: Tenant ID
        details: Additional details
    """
    await _write_log(
        action=action,
        details=details,
        user_id=user_id,
        tenant_id=tenant_id,
    )


async def _write_log(
    action: str,
    details: dict | None = None,
    agent_id: uuid.UUID | None = None,
    user_id: uuid.UUID | None = None,
    tenant_id: uuid.UUID | None = None,
    organization_id: uuid.UUID | None = None,
) -> None:
    """Internal method to write audit log."""
    try:
        async with async_session() as db:
            # Build details with additional context
            full_details = details or {}
            if tenant_id:
                full_details["tenant_id"] = str(tenant_id)
            if organization_id:
                full_details["organization_id"] = str(organization_id)

            # Use simpler insert that works with existing schema
            await db.execute(
                text(
                    "INSERT INTO audit_logs (id, action, details, agent_id, user_id, created_at) "
                    "VALUES (:id, :action, :details, :agent_id, :user_id, :created_at)"
                ),
                {
                    "id": uuid.uuid4(),
                    "action": action,
                    "details": json.dumps(full_details, ensure_ascii=False, default=str),
                    "agent_id": agent_id,
                    "user_id": user_id,
                    "created_at": datetime.now(timezone.utc),
                },
            )
            await db.commit()
    except Exception as e:
        # Never let audit logging break the caller
        logger.error(f"[audit_logger] WARNING: failed to write audit log: {e}")
