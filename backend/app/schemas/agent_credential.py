"""Pydantic schemas for AgentCredential CRUD operations.

These schemas ensure that sensitive fields (cookies_json)
are never returned to the frontend in API responses.
"""

import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class AgentCredentialCreate(BaseModel):
    """Schema for creating a new credential."""

    credential_type: str = Field(default="website", description="Type: website|email|social|api_key")
    platform: str = Field(..., description="Domain name e.g. 'baidu.com'")
    display_name: str = Field(default="", description="Human-readable label")
    cookies_json: str | None = Field(default=None, description="JSON array of cookies")


class AgentCredentialUpdate(BaseModel):
    """Schema for updating an existing credential."""

    credential_type: str | None = None
    platform: str | None = None
    display_name: str | None = None
    cookies_json: str | None = None
    status: str | None = None


class AgentCredentialResponse(BaseModel):
    """Schema for credential API responses.

    Note: cookies_json is NEVER included in responses.
    """

    id: uuid.UUID
    agent_id: uuid.UUID
    credential_type: str
    platform: str
    display_name: str
    status: str
    cookies_updated_at: datetime | None = None
    last_login_at: datetime | None = None
    last_injected_at: datetime | None = None
    has_cookies: bool = False  # indicates whether cookies_json is stored
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
