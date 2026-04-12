"""Agent Group Relationships API."""

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.agent import Agent
from app.models.org import AgentGroup
from app.core.security import get_current_user
from app.models.user import User

router = APIRouter(prefix="/agents", tags=["agent-groups"])


class AgentGroupCreate(BaseModel):
    group_name: str = Field(min_length=1, max_length=100)
    chat_id: str = Field(min_length=1, max_length=200)
    channel: str = Field(default="feishu")
    description: str = ""


class AgentGroupOut(BaseModel):
    id: uuid.UUID
    agent_id: uuid.UUID
    group_name: str
    chat_id: str
    channel: str
    description: str
    created_at: datetime

    model_config = {"from_attributes": True}


@router.get("/{agent_id}/relationships/groups", response_model=list[AgentGroupOut])
async def list_groups(
    agent_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    result = await db.execute(
        select(AgentGroup)
        .where(AgentGroup.agent_id == agent_id)
        .order_by(AgentGroup.created_at.desc())
    )
    groups = result.scalars().all()
    return [AgentGroupOut.model_validate(g) for g in groups]


@router.put("/{agent_id}/relationships/groups", response_model=list[AgentGroupOut])
async def update_groups(
    agent_id: uuid.UUID,
    groups: list[AgentGroupCreate],
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    await db.execute(
        delete(AgentGroup).where(AgentGroup.agent_id == agent_id)
    )
    new_groups = []
    for g in groups:
        new_group = AgentGroup(
            agent_id=agent_id,
            group_name=g.group_name,
            chat_id=g.chat_id,
            channel=g.channel,
            description=g.description,
        )
        db.add(new_group)
        new_groups.append(new_group)
    await db.commit()
    for g in new_groups:
        await db.refresh(g)
    return [AgentGroupOut.model_validate(g) for g in new_groups]


@router.delete("/{agent_id}/relationships/groups/{group_id}")
async def delete_group(
    agent_id: uuid.UUID,
    group_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    result = await db.execute(
        delete(AgentGroup).where(
            AgentGroup.id == group_id,
            AgentGroup.agent_id == agent_id,
        )
    )
    await db.commit()
    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail="Group not found")
    return {"status": "ok"}
