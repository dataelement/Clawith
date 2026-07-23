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
) -> bool:
    if getattr(model, "deleted_at", None) is not None or not model.enabled:
        return False
    if model.tenant_id not in {None, tenant_id}:
        return False
    return True


async def load_active_model(
    db: AsyncSession,
    *,
    model_id: uuid.UUID | None,
    tenant_id: uuid.UUID | None,
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
    ):
        return None
    return model


async def active_agent_model_candidates(
    db: AsyncSession,
    agent: Agent,
) -> tuple[LLMModel, ...]:
    """Resolve primary, fallback, then tenant default without rewriting stored IDs."""
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
    if not candidate_ids:
        return ()

    result = await db.execute(
        select(LLMModel).where(
            LLMModel.id.in_(candidate_ids),
            LLMModel.deleted_at.is_(None),
            LLMModel.enabled.is_(True),
            or_(LLMModel.tenant_id.is_(None), LLMModel.tenant_id == agent.tenant_id),
        )
    )
    models_by_id = {model.id: model for model in result.scalars().all()}
    return tuple(
        model
        for model_id in candidate_ids
        if (model := models_by_id.get(model_id)) is not None
        and _is_usable(
            model,
            tenant_id=agent.tenant_id,
        )
    )


async def resolve_active_agent_model(
    db: AsyncSession,
    agent: Agent,
) -> LLMModel | None:
    candidates = await active_agent_model_candidates(db, agent)
    return candidates[0] if candidates else None


__all__ = [
    "active_agent_model_candidates",
    "load_active_model",
    "resolve_active_agent_model",
]
