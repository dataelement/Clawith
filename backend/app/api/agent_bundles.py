"""Agent Bundles API — list / detail / hire endpoints.

POST /bundles/{slug}/hire is a Phase 1 deliverable; this Phase 0 skeleton
returns 501 Not Implemented so the route surface is stable while the hire
orchestration is written separately in ``services.agent_bundle_hire``.
"""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.security import get_current_user
from app.database import get_db
from app.models.agent_bundle import AgentBundle
from app.models.user import User
from app.schemas.agent_bundle import (
    BundleAgentOut,
    BundleDetailOut,
    BundleHireIn,
    BundleHireOut,
    BundleMcpOut,
    BundleRelOut,
    BundleSummaryOut,
)


router = APIRouter(prefix="/bundles", tags=["agent-bundles"])


@router.get("", response_model=list[BundleSummaryOut])
async def list_bundles(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[BundleSummaryOut]:
    """List all available agent bundles (builtin first, by creation order)."""
    result = await db.execute(
        select(AgentBundle)
        .options(
            selectinload(AgentBundle.agents),
            selectinload(AgentBundle.mcp_servers),
            selectinload(AgentBundle.relationships),
        )
        .order_by(AgentBundle.is_builtin.desc(), AgentBundle.created_at.asc())
    )
    bundles = result.scalars().all()
    return [
        BundleSummaryOut(
            id=b.id,
            slug=b.slug,
            name=b.name,
            description=b.description,
            name_en=b.name_en,
            description_en=b.description_en,
            icon=b.icon,
            category=b.category,
            capability_bullets=b.capability_bullets or [],
            capability_bullets_en=b.capability_bullets_en,
            version=b.version,
            language=b.language or "zh",
            is_builtin=b.is_builtin,
            agent_count=len(b.agents),
            mcp_count=len(b.mcp_servers),
            relationship_count=len(b.relationships),
        )
        for b in bundles
    ]


@router.get("/{slug}", response_model=BundleDetailOut)
async def get_bundle(
    slug: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> BundleDetailOut:
    """Full bundle detail incl. each agent's soul, MCP list, relationship matrix."""
    result = await db.execute(
        select(AgentBundle)
        .where(AgentBundle.slug == slug)
        .options(
            selectinload(AgentBundle.agents),
            selectinload(AgentBundle.mcp_servers),
            selectinload(AgentBundle.relationships),
        )
    )
    b = result.scalar_one_or_none()
    if b is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Bundle not found")

    return BundleDetailOut(
        id=b.id,
        slug=b.slug,
        name=b.name,
        description=b.description,
        name_en=b.name_en,
        description_en=b.description_en,
        icon=b.icon,
        category=b.category,
        capability_bullets=b.capability_bullets or [],
        capability_bullets_en=b.capability_bullets_en,
        principal_slug=b.principal_slug,
        version=b.version,
        language=b.language or "zh",
        is_builtin=b.is_builtin,
        agent_count=len(b.agents),
        mcp_count=len(b.mcp_servers),
        relationship_count=len(b.relationships),
        agents=[
            BundleAgentOut(
                slug=a.slug,
                position=a.position,
                name=a.name,
                role_description=a.role_description,
                primary_model_hint=a.primary_model_hint,
                default_skills=a.default_skills or [],
                default_autonomy_policy=a.default_autonomy_policy or {},
                default_mcp_attach=a.default_mcp_attach or [],
                soul_md=a.soul_md,
            )
            for a in sorted(b.agents, key=lambda x: x.position)
        ],
        mcp_servers=[
            BundleMcpOut(
                local_key=m.local_key,
                server_name=m.server_name,
                url=m.url,
                transport=m.transport,
            )
            for m in b.mcp_servers
        ],
        relationships=[
            BundleRelOut(
                from_slug=r.from_slug,
                to_slug=r.to_slug,
                relation=r.relation,
                description=r.description,
            )
            for r in b.relationships
        ],
    )


@router.post(
    "/{slug}/hire",
    response_model=BundleHireOut,
    status_code=status.HTTP_201_CREATED,
)
async def hire_bundle(
    slug: str,
    body: BundleHireIn,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> BundleHireOut:
    """Hire a bundle — atomically creates N agents + R MCP bindings + K relationships.

    Delegates to ``services.agent_bundle_hire.hire_bundle`` which handles
    quota / idempotency precheck, agent creation, MCP binding, relationship
    wiring, and partial-failure cleanup.
    """
    from app.services.agent_bundle_hire import (
        BundleHireConflict,
        BundleHireError,
        hire_bundle as _hire_bundle,
    )

    try:
        result = await _hire_bundle(db, slug, current_user, visibility=body.visibility)
    except BundleHireConflict as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=exc.message) from exc
    except BundleHireError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc

    return BundleHireOut(**result)
