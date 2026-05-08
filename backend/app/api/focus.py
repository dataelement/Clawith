"""Structured Focus API for Aware."""

import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.permissions import check_agent_access
from app.core.security import get_current_user
from app.database import get_db
from app.models.user import User
from app.services.focus_service import complete_focus_item, list_focus_items, upsert_focus_item


router = APIRouter(prefix="/agents/{agent_id}/focus", tags=["focus"])


class FocusItemResponse(BaseModel):
    id: str
    agent_id: str
    key: str
    description: str
    status: str
    kind: str
    source: str
    metadata: dict = Field(default_factory=dict)
    sort_order: int
    completed_at: str | None = None
    created_at: str | None = None
    updated_at: str | None = None


class FocusUpsertBody(BaseModel):
    key: str | None = None
    description: str
    status: str = "in_progress"
    kind: str = "normal"
    source: str = "user"
    metadata: dict | None = None


@router.get("/", response_model=list[FocusItemResponse])
async def list_agent_focus(
    agent_id: uuid.UUID,
    include_completed: bool = True,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await check_agent_access(db, current_user, agent_id)
    return await list_focus_items(agent_id, include_completed=include_completed)


@router.post("/", response_model=FocusItemResponse)
async def upsert_agent_focus(
    agent_id: uuid.UUID,
    body: FocusUpsertBody,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await check_agent_access(db, current_user, agent_id)
    if body.status not in {"in_progress", "completed"}:
        raise HTTPException(400, "Invalid focus status")
    if body.kind not in {"normal", "system"}:
        raise HTTPException(400, "Invalid focus kind")
    return await upsert_focus_item(
        agent_id,
        key=body.key,
        description=body.description,
        status=body.status,
        kind=body.kind,
        source=body.source,
        metadata=body.metadata,
    )


@router.post("/{key}/complete", response_model=FocusItemResponse)
async def complete_agent_focus(
    agent_id: uuid.UUID,
    key: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await check_agent_access(db, current_user, agent_id)
    item = await complete_focus_item(agent_id, key=key)
    if not item:
        raise HTTPException(404, "Focus item not found")
    return item
