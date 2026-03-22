"""Schemas for the virtual organization API."""

import uuid
from dataclasses import asdict, is_dataclass
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class VirtualOrgAgentSummary(BaseModel):
    id: uuid.UUID
    name: str
    template_id: uuid.UUID | None = None
    department_id: uuid.UUID
    department_name: str
    title: str
    level: str
    org_bucket: str
    manager_agent_id: uuid.UUID | None = None
    is_locked: bool = False
    tags: list[str] = Field(default_factory=list)


class VirtualOrgDepartmentOut(BaseModel):
    id: uuid.UUID
    name: str
    slug: str
    sort_order: int = 0
    org_level: str = "department"
    is_core: bool = True
    leader: VirtualOrgAgentSummary | None = None
    core_agents: list[VirtualOrgAgentSummary] = Field(default_factory=list)
    expert_agents: list[VirtualOrgAgentSummary] = Field(default_factory=list)
    expert_count: int = 0
    children: list["VirtualOrgDepartmentOut"] = Field(default_factory=list)


class VirtualOrgExpertPoolOut(BaseModel):
    count: int = 0
    agents: list[VirtualOrgAgentSummary] = Field(default_factory=list)


class VirtualOrgOverviewOut(BaseModel):
    executives: list[VirtualOrgAgentSummary] = Field(default_factory=list)
    departments: list[VirtualOrgDepartmentOut] = Field(default_factory=list)
    expert_pool: VirtualOrgExpertPoolOut = Field(default_factory=VirtualOrgExpertPoolOut)
    cross_functional: list[VirtualOrgAgentSummary] = Field(default_factory=list)


class VirtualOrgDepartmentCreate(BaseModel):
    name: str
    slug: str
    parent_id: uuid.UUID | None = None
    sort_order: int = 0
    org_level: str = "department"
    is_core: bool = True


class VirtualOrgDepartmentPatch(BaseModel):
    name: str | None = None
    slug: str | None = None
    parent_id: uuid.UUID | None = None
    sort_order: int | None = None
    org_level: str | None = None
    is_core: bool | None = None


class VirtualOrgAgentPatch(BaseModel):
    department_id: uuid.UUID | None = None
    title: str | None = None
    level: str | None = None
    org_bucket: str | None = None
    manager_agent_id: uuid.UUID | None = None
    is_locked: bool | None = None
    tags: list[str] | None = None


class VirtualOrgAgentListOut(BaseModel):
    items: list[VirtualOrgAgentSummary] = Field(default_factory=list)
    total: int = 0
    page: int = 1
    page_size: int = 50


class VirtualOrgBootstrapRequest(BaseModel):
    force: bool = False


class VirtualOrgBootstrapResponse(BaseModel):
    created_departments: int = 0
    updated_assignments: int = 0
    created_primary_agents: int = 0
    created_tags: int = 0
    warnings: list[str] = Field(default_factory=list)

    @classmethod
    def from_result(cls, result: Any) -> "VirtualOrgBootstrapResponse":
        if isinstance(result, cls):
            return result
        if is_dataclass(result):
            return cls(**asdict(result))
        if isinstance(result, dict):
            return cls(**result)
        raise TypeError(f"Unsupported bootstrap result type: {type(result)!r}")


class VirtualOrgDepartmentOutRecursive(VirtualOrgDepartmentOut):
    created_at: datetime | None = None
