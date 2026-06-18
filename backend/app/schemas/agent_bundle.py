"""Pydantic schemas for the Agent Bundle API."""

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field


# ─── Read shapes ──────────────────────────────────────────────────


class BundleAgentOut(BaseModel):
    slug: str
    position: int
    name: str
    role_description: str
    primary_model_hint: str | None = None
    default_skills: list[str] = Field(default_factory=list)
    default_autonomy_policy: dict = Field(default_factory=dict)
    default_mcp_attach: list[str] = Field(default_factory=list)
    # Soul markdown intentionally NOT in list views; included in detail view only.
    soul_md: str | None = None

    class Config:
        from_attributes = True


class BundleMcpOut(BaseModel):
    local_key: str
    server_name: str
    url: str
    transport: str

    class Config:
        from_attributes = True


class BundleRelOut(BaseModel):
    from_slug: str
    to_slug: str
    relation: str
    description: str

    class Config:
        from_attributes = True


class BundleSummaryOut(BaseModel):
    """List-view: card-grade info, no soul.md content."""

    id: UUID
    slug: str
    name: str
    description: str
    # Optional English-language counterparts. Frontend renders the CN field
    # when *_en is None, so legacy zh-only bundles continue to work.
    name_en: str | None = None
    description_en: str | None = None
    icon: str
    category: str
    capability_bullets: list[str] = Field(default_factory=list)
    capability_bullets_en: list[str] | None = None
    version: str
    # Author-declared content language ("zh" or "en"). Frontend filters the
    # Talent Market so an EN user only sees EN-native bundles and a CN user
    # only sees CN-native ones — the agent soul / skills / names are
    # native-language as a unit, not a localised card stuck onto an
    # opposite-language soul.
    language: str = "zh"
    is_builtin: bool
    agent_count: int
    mcp_count: int
    relationship_count: int

    class Config:
        from_attributes = True


class BundleDetailOut(BundleSummaryOut):
    """Detail view: full agent souls, mcp list, relationship matrix."""

    # Which bundle-agent slug is the principal (point of contact). Modal /
    # BundleCard can highlight that agent so the user knows who to chat first
    # after hire.
    principal_slug: str | None = None
    agents: list[BundleAgentOut] = Field(default_factory=list)
    mcp_servers: list[BundleMcpOut] = Field(default_factory=list)
    relationships: list[BundleRelOut] = Field(default_factory=list)


# ─── Hire ─────────────────────────────────────────────────────────


class BundleHireIn(BaseModel):
    visibility: Literal["only_me", "company", "custom"] = "only_me"


class HiredAgentOut(BaseModel):
    agent_id: UUID
    slug: str
    name: str


class BundleHireOut(BaseModel):
    bundle_slug: str
    # Bundle-local slug of the principal (point-of-contact, ★). Without this
    # field the response_model would strip it from hire_bundle()'s return, so
    # the frontend would fall back to landing on agents[0] instead of the
    # intended entry agent (e.g. Research Manager).
    principal_slug: str | None = None
    agents: list[HiredAgentOut] = Field(default_factory=list)
    relationship_count: int
    mcp_attach_count: int
    # Number of ``on_message`` triggers auto-seeded from the bundle's
    # relationship graph — one per (from→to) edge so the recipient
    # auto-wakes on A2A messages instead of needing user kick-through.
    trigger_count: int = 0
