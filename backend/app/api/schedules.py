"""Schedule API — CRUD for agent cron jobs."""

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.dao import query_dao
from app.core.permissions import check_agent_access, is_agent_creator, is_agent_expired
from app.core.security import get_current_user
from app.database import get_db
from app.models.schedule import AgentSchedule
from app.models.user import User
from app.services.heartbeat_runtime import enqueue_schedule_runtime
from app.services.scheduler import compute_next_run

router = APIRouter(prefix="/agents/{agent_id}/schedules", tags=["schedules"])


class ScheduleCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    instruction: str = Field(default='', max_length=5000)
    cron_expr: str = Field(min_length=1, max_length=100)
    is_enabled: bool = True


class ScheduleUpdate(BaseModel):
    name: str | None = None
    instruction: str | None = None
    cron_expr: str | None = None
    is_enabled: bool | None = None


class ScheduleOut(BaseModel):
    id: uuid.UUID
    agent_id: uuid.UUID
    name: str
    instruction: str
    cron_expr: str
    is_enabled: bool
    last_run_at: datetime | None = None
    next_run_at: datetime | None = None
    run_count: int
    created_by: uuid.UUID | None = None
    creator_username: str | None = None
    created_at: datetime | None = None

    model_config = {"from_attributes": True}


@router.get("/", response_model=list[ScheduleOut])
async def list_schedules(
    agent_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List all schedules for an agent."""
    await check_agent_access(db, current_user, agent_id)
    result = await query_dao.execute(db, 
        select(AgentSchedule)
        .where(AgentSchedule.agent_id == agent_id)
        .order_by(AgentSchedule.created_at.desc())
    )
    schedules = result.scalars().all()
    # Batch-load creator usernames
    creator_ids = {s.created_by for s in schedules if s.created_by}
    creator_map = {}
    if creator_ids:
        users_result = await query_dao.execute(db, select(User).where(User.id.in_(creator_ids)))
        creator_map = {u.id: u.username for u in users_result.scalars().all()}
    out_list = []
    for s in schedules:
        s_out = ScheduleOut.model_validate(s)
        s_out.creator_username = creator_map.get(s.created_by)
        out_list.append(s_out)
    return out_list


@router.post("/", response_model=ScheduleOut, status_code=status.HTTP_201_CREATED)
async def create_schedule(
    agent_id: uuid.UUID,
    data: ScheduleCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Create a new schedule for an agent."""
    agent, _access = await check_agent_access(db, current_user, agent_id)
    if not is_agent_creator(current_user, agent):
        raise HTTPException(status_code=403, detail="Only creator can manage schedules")

    # Validate cron expression
    next_run = compute_next_run(data.cron_expr)
    if not next_run:
        raise HTTPException(status_code=400, detail=f"Invalid cron expression: {data.cron_expr}")

    sched = AgentSchedule(
        agent_id=agent_id,
        name=data.name,
        instruction=data.instruction,
        cron_expr=data.cron_expr,
        is_enabled=data.is_enabled,
        next_run_at=next_run if data.is_enabled else None,
        created_by=current_user.id,
    )
    query_dao.add(db, sched)
    await query_dao.flush(db)
    return ScheduleOut.model_validate(sched)


@router.patch("/{schedule_id}", response_model=ScheduleOut)
async def update_schedule(
    agent_id: uuid.UUID,
    schedule_id: uuid.UUID,
    data: ScheduleUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Update a schedule."""
    agent, _access = await check_agent_access(db, current_user, agent_id)
    if not is_agent_creator(current_user, agent):
        raise HTTPException(status_code=403, detail="Only creator can manage schedules")

    result = await query_dao.execute(db, 
        select(AgentSchedule).where(AgentSchedule.id == schedule_id, AgentSchedule.agent_id == agent_id)
    )
    sched = result.scalar_one_or_none()
    if not sched:
        raise HTTPException(status_code=404, detail="Schedule not found")

    updates = data.model_dump(exclude_unset=True)
    for field, value in updates.items():
        setattr(sched, field, value)

    # Recompute next_run if cron or enabled changed
    if "cron_expr" in updates or "is_enabled" in updates:
        if sched.is_enabled:
            sched.next_run_at = compute_next_run(sched.cron_expr)
        else:
            sched.next_run_at = None

    await query_dao.flush(db)
    return ScheduleOut.model_validate(sched)


@router.delete("/{schedule_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_schedule(
    agent_id: uuid.UUID,
    schedule_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Delete a schedule."""
    agent, _access = await check_agent_access(db, current_user, agent_id)
    if not is_agent_creator(current_user, agent):
        raise HTTPException(status_code=403, detail="Only creator can manage schedules")

    result = await query_dao.execute(db, 
        select(AgentSchedule).where(AgentSchedule.id == schedule_id, AgentSchedule.agent_id == agent_id)
    )
    sched = result.scalar_one_or_none()
    if not sched:
        raise HTTPException(status_code=404, detail="Schedule not found")

    await query_dao.delete(db, sched)
    await query_dao.flush(db)


@router.post("/{schedule_id}/run")
async def trigger_schedule(
    agent_id: uuid.UUID,
    schedule_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Manually trigger a schedule execution."""
    agent, _access = await check_agent_access(db, current_user, agent_id)
    if is_agent_expired(agent):
        raise HTTPException(status_code=403, detail="Agent has expired and cannot be triggered.")

    result = await query_dao.execute(db, 
        select(AgentSchedule).where(AgentSchedule.id == schedule_id, AgentSchedule.agent_id == agent_id)
    )
    sched = result.scalar_one_or_none()
    if not sched:
        raise HTTPException(status_code=404, detail="Schedule not found")

    handle = await enqueue_schedule_runtime(
        db,
        agent=agent,
        schedule_id=sched.id,
        occurrence_id=uuid.uuid4(),
        instruction=sched.instruction,
    )
    if handle is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Unified Agent Runtime is not enabled for schedules",
        )

    sched.last_run_at = datetime.now(timezone.utc)
    sched.run_count = (sched.run_count or 0) + 1
    await query_dao.flush(db)

    return {
        "status": "queued",
        "schedule_id": str(schedule_id),
        "run_id": str(handle.run_id),
    }


@router.get("/{schedule_id}/history")
async def get_schedule_history(
    agent_id: uuid.UUID,
    schedule_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get execution history for a schedule from activity logs."""
    await check_agent_access(db, current_user, agent_id)
    from app.models.activity_log import AgentActivityLog
    result = await query_dao.execute(db, 
        select(AgentActivityLog)
        .where(
            AgentActivityLog.agent_id == agent_id,
            AgentActivityLog.action_type == "schedule_run",
        )
        .order_by(AgentActivityLog.created_at.desc())
    )
    logs = result.scalars().all()
    # Filter by schedule_id in detail_json
    history = []
    for log in logs:
        detail = log.detail_json or {}
        if detail.get("schedule_id") == str(schedule_id):
            history.append({
                "id": str(log.id),
                "created_at": log.created_at.isoformat() if log.created_at else None,
                "summary": log.summary,
                "instruction": detail.get("instruction", ""),
                "reply": detail.get("reply", ""),
            })
        if len(history) >= 20:
            break
    return history
