"""Shared Active-model resolution for Agent calls."""

from __future__ import annotations

import uuid

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent import Agent
from app.models.llm import LLMModel
from app.models.tenant import Tenant


def _is_usable(
    model: LLMModel,
    *,
    tenant_id: uuid.UUID | None,
    require_tool_calling: bool,
) -> bool:
    if getattr(model, "deleted_at", None) is not None or not model.enabled:
        return False
    if model.tenant_id not in {None, tenant_id}:
        return False
    if require_tool_calling and model.supports_tool_calling is not True:
        return False
    return True


async def load_active_model(
    db: AsyncSession,
    *,
    model_id: uuid.UUID | None,
    tenant_id: uuid.UUID | None,
    require_tool_calling: bool = False,
) -> LLMModel | None:
    """Load one enabled, non-deleted model valid for the requested tenant."""
    if model_id is None:
        return None
    result = await db.execute(
        select(LLMModel).where(
            LLMModel.id == model_id,
            LLMModel.deleted_at.is_(None),
            LLMModel.enabled.is_(True),
            or_(LLMModel.tenant_id.is_(None), LLMModel.tenant_id == tenant_id),
        )
    )
    model = result.scalar_one_or_none()
    if model is None or not _is_usable(
        model,
        tenant_id=tenant_id,
        require_tool_calling=require_tool_calling,
    ):
        return None
    return model


async def active_agent_model_candidates(
    db: AsyncSession,
    agent: Agent,
    *,
    require_tool_calling: bool = False,
) -> tuple[LLMModel, ...]:
    """Resolve configured models, then recover from stale logical-delete pointers.

    Primary, fallback, and tenant-default IDs retain their configured priority.
    Logical deletion deliberately preserves those IDs for auditability, so when
    every configured pointer is inactive we fall back to the tenant's newest
    active models instead of treating the company as having no model at all.
    """
    if getattr(agent, "deleted_at", None) is not None:
        return ()

    default_model_id: uuid.UUID | None = None
    if agent.tenant_id is not None:
        default_result = await db.execute(
            select(Tenant.default_model_id).where(Tenant.id == agent.tenant_id)
        )
        default_model_id = default_result.scalar_one_or_none()

    candidate_ids = tuple(
        dict.fromkeys(
            model_id
            for model_id in (
                agent.primary_model_id,
                agent.fallback_model_id,
                default_model_id,
            )
            if model_id is not None
        )
    )
    resolved: tuple[LLMModel, ...] = ()
    if candidate_ids:
        result = await db.execute(
            select(LLMModel).where(
                LLMModel.id.in_(candidate_ids),
                LLMModel.deleted_at.is_(None),
                LLMModel.enabled.is_(True),
                or_(LLMModel.tenant_id.is_(None), LLMModel.tenant_id == agent.tenant_id),
            )
        )
        models_by_id = {model.id: model for model in result.scalars().all()}
        resolved = tuple(
            model
            for model_id in candidate_ids
            if (model := models_by_id.get(model_id)) is not None
            and _is_usable(
                model,
                tenant_id=agent.tenant_id,
                require_tool_calling=require_tool_calling,
            )
        )
    if resolved or agent.tenant_id is None:
        return resolved

    fallback_query = select(LLMModel).where(
        LLMModel.tenant_id == agent.tenant_id,
        LLMModel.deleted_at.is_(None),
        LLMModel.enabled.is_(True),
    )
    if require_tool_calling:
        fallback_query = fallback_query.where(LLMModel.supports_tool_calling.is_(True))
    fallback_result = await db.execute(
        fallback_query.order_by(LLMModel.created_at.desc()).limit(2)
    )
    return tuple(
        model
        for model in fallback_result.scalars().all()
        if _is_usable(
            model,
            tenant_id=agent.tenant_id,
            require_tool_calling=require_tool_calling,
        )
    )


async def resolve_active_agent_model(
    db: AsyncSession,
    agent: Agent,
    *,
    require_tool_calling: bool = False,
) -> LLMModel | None:
    candidates = await active_agent_model_candidates(
        db,
        agent,
        require_tool_calling=require_tool_calling,
    )
    return candidates[0] if candidates else None


__all__ = [
    "active_agent_model_candidates",
    "load_active_model",
    "resolve_active_agent_model",
]
