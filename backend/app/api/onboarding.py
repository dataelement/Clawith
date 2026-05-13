"""Company onboarding APIs."""

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import get_current_user
from app.database import get_db
from app.models.agent import Agent, AgentPermission, AgentTemplate
from app.models.llm import LLMModel
from app.models.onboarding import UserTenantOnboarding
from app.models.participant import Participant
from app.models.tenant import Tenant
from app.models.user import User
from app.services.access_relationships import ensure_access_granted_platform_relationships

router = APIRouter(prefix="/onboarding", tags=["onboarding"])


class OnboardingStartRequest(BaseModel):
    entry_mode: str = Field(default="create", pattern="^(create|join)$")


class PersonalAssistantRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    personality: str = Field(default="warm", max_length=64)
    work_style: str = Field(default="concise", max_length=64)
    boundaries: str = Field(default="", max_length=1000)


def _status_payload(row: UserTenantOnboarding | None) -> dict:
    return {
        "exists": row is not None,
        "status": row.status if row else "not_started",
        "current_step": row.current_step if row else "company",
        "entry_mode": row.entry_mode if row else None,
        "personal_assistant_agent_id": str(row.personal_assistant_agent_id) if row and row.personal_assistant_agent_id else None,
        "completed_at": row.completed_at.isoformat() if row and row.completed_at else None,
    }


async def _get_row(db: AsyncSession, user: User) -> UserTenantOnboarding | None:
    if not user.tenant_id:
        return None
    result = await db.execute(
        select(UserTenantOnboarding).where(
            UserTenantOnboarding.user_id == user.id,
            UserTenantOnboarding.tenant_id == user.tenant_id,
        )
    )
    return result.scalar_one_or_none()


async def _ensure_row(db: AsyncSession, user: User, entry_mode: str) -> UserTenantOnboarding:
    if not user.tenant_id:
        raise HTTPException(status_code=400, detail="Company is required before onboarding")
    row = await _get_row(db, user)
    if row:
        if row.status == "completed":
            return row
        row.entry_mode = entry_mode
        if row.current_step == "company":
            row.current_step = "assistant"
        return row

    await db.execute(
        pg_insert(UserTenantOnboarding)
        .values(
            id=uuid.uuid4(),
            user_id=user.id,
            tenant_id=user.tenant_id,
            entry_mode=entry_mode,
            current_step="assistant",
            status="in_progress",
        )
        .on_conflict_do_nothing(constraint="uq_user_tenant_onboarding")
    )

    row = await _get_row(db, user)
    if not row:
        raise HTTPException(status_code=500, detail="Failed to start onboarding")
    if row.status != "completed":
        row.entry_mode = entry_mode
        if row.current_step == "company":
            row.current_step = "assistant"
    return row


async def _tenant_default_model_id(db: AsyncSession, tenant_id: uuid.UUID | None) -> uuid.UUID | None:
    if not tenant_id:
        return None
    tenant_result = await db.execute(select(Tenant).where(Tenant.id == tenant_id))
    tenant = tenant_result.scalar_one_or_none()
    if tenant and tenant.default_model_id:
        return tenant.default_model_id
    model_result = await db.execute(
        select(LLMModel.id).where(
            LLMModel.tenant_id == tenant_id,
            LLMModel.enabled == True,  # noqa: E712
        ).order_by(LLMModel.created_at.asc())
    )
    return model_result.scalar_one_or_none()


async def _create_personal_assistant(
    db: AsyncSession,
    user: User,
    data: PersonalAssistantRequest,
) -> Agent:
    if not user.tenant_id:
        raise HTTPException(status_code=400, detail="Company is required before creating a personal assistant")

    template_result = await db.execute(
        select(AgentTemplate).where(AgentTemplate.name == "Private Assistant")
    )
    template = template_result.scalar_one_or_none()
    primary_model_id = await _tenant_default_model_id(db, user.tenant_id)
    personality_note = f"Personality: {data.personality}. Work style: {data.work_style}."
    boundaries = data.boundaries.strip()
    bio = (
        "A private assistant for daily coordination, notes, follow-ups, drafts, and light planning. "
        f"{personality_note}"
        + (f" Boundaries: {boundaries}" if boundaries else "")
    )

    agent = Agent(
        name=data.name.strip(),
        role_description="Private Assistant",
        bio=bio,
        creator_id=user.id,
        tenant_id=user.tenant_id,
        agent_type="native",
        primary_model_id=primary_model_id,
        template_id=template.id if template else None,
        status="creating",
        access_mode="private",
        company_access_level="use",
    )
    if template and template.default_autonomy_policy:
        agent.autonomy_policy = template.default_autonomy_policy

    db.add(agent)
    await db.flush()

    db.add(Participant(type="agent", ref_id=agent.id, display_name=agent.name, avatar_url=agent.avatar_url))
    db.add(AgentPermission(agent_id=agent.id, scope_type="user", scope_id=user.id, access_level="manage"))
    await db.flush()
    await ensure_access_granted_platform_relationships(db, agent, created_by_user_id=user.id)

    from app.services.agent_manager import agent_manager
    await agent_manager.initialize_agent_files(
        db,
        agent,
        personality=personality_note,
        boundaries=boundaries,
    )
    from app.api.relationships import _regenerate_relationships_file
    await _regenerate_relationships_file(db, agent.id)

    try:
        await agent_manager.start_container(db, agent)
    except Exception:
        agent.status = "error"
        raise

    await db.flush()
    return agent


@router.get("/status")
async def get_onboarding_status(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Return onboarding state for the current user/company."""
    return _status_payload(await _get_row(db, current_user))


@router.post("/start")
async def start_onboarding(
    data: OnboardingStartRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Start or resume onboarding for the current user/company."""
    row = await _ensure_row(db, current_user, data.entry_mode)
    await db.commit()
    return _status_payload(row)


@router.post("/personal-assistant", status_code=status.HTTP_201_CREATED)
async def create_personal_assistant(
    data: PersonalAssistantRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Create the user's private assistant and advance onboarding."""
    row = await _ensure_row(db, current_user, "join")
    if row.personal_assistant_agent_id:
        result = await db.execute(select(Agent).where(Agent.id == row.personal_assistant_agent_id))
        existing = result.scalar_one_or_none()
        if existing:
            row.current_step = "opening"
            await db.commit()
            return {"agent": {"id": str(existing.id), "name": existing.name}, "onboarding": _status_payload(row)}

    agent = await _create_personal_assistant(db, current_user, data)
    row.personal_assistant_agent_id = agent.id
    row.current_step = "opening"
    row.status = "in_progress"
    await db.commit()
    return {"agent": {"id": str(agent.id), "name": agent.name}, "onboarding": _status_payload(row)}


@router.post("/complete")
async def complete_onboarding(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Mark the current user/company onboarding as completed."""
    row = await _get_row(db, current_user)
    if not row:
        row = await _ensure_row(db, current_user, "join")
    row.status = "completed"
    row.current_step = "completed"
    row.completed_at = datetime.now(timezone.utc)
    await db.commit()
    return _status_payload(row)
