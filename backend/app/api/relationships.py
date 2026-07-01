"""Legacy agent relationship management API.

These endpoints are retained for OKR, gateway, and historical compatibility.
They do not decide who appears in the Agent Directory; roster visibility does.
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import and_, delete, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased, selectinload

from app.config import get_settings
from app.core.permissions import (
    build_visible_agents_query,
    check_agent_access,
    evaluate_agent_relationship_status,
    evaluate_human_relationship_status,
    get_agent_accessible_user_ids,
    get_agent_access_level_for_user_id,
)
from app.core.security import get_current_user
from app.database import get_db
from app.models.agent import Agent
from app.models.org import AgentRelationship, AgentAgentRelationship, OrgMember
from app.models.user import Identity, User
from app.services.access_relationships import ensure_access_granted_platform_relationships
from app.services.org_sync_adapter import derive_member_department_paths
from app.services.storage import store_agent_bytes

router = APIRouter(prefix="/agents/{agent_id}/relationships", tags=["legacy-relationships"])

RELATION_LABELS = {
    "direct_leader": "直属上级",
    "collaborator": "协作伙伴",
    "stakeholder": "利益相关者",
    "team_member": "团队成员",
    "subordinate": "下属",
    "mentor": "导师",
    "other": "其他",
}

AGENT_RELATION_LABELS = {
    "peer": "同级协作",
    "supervisor": "上级数字员工",
    "assistant": "助手",
    "collaborator": "协作伙伴",
    "other": "其他",
}


def _can_manage_relationships(current_user: User, access_level: str) -> bool:
    return access_level == "manage" or current_user.role in ("platform_admin", "org_admin")


def _display_provider_name(provider_name: str | None, provider_type: str | None) -> str | None:
    if not provider_name and not provider_type:
        return None
    if (provider_type or "").lower() in ("web", "platform") or (provider_name or "").lower() == "web":
        return "Platform"
    return provider_name


async def _can_manage_agent(db: AsyncSession, user_id: uuid.UUID, agent: Agent) -> bool:
    return (await get_agent_access_level_for_user_id(db, user_id, agent)) == "manage"


async def _get_valid_member_user_id(
    db: AsyncSession,
    member: OrgMember,
    tenant_id: uuid.UUID | None,
) -> uuid.UUID | None:
    """Return the linked platform user only when it belongs to the same tenant."""
    if not member.user_id:
        return None
    result = await db.execute(
        select(User.id).where(
            User.id == member.user_id,
            User.tenant_id == tenant_id,
            User.is_active == True,  # noqa: E712
        )
    )
    return result.scalar_one_or_none()


# ─── Schemas ───────────────────────────────────────────

class RelationshipIn(BaseModel):
    member_id: str
    relation: str = "collaborator"
    description: str = ""


class RelationshipBatchIn(BaseModel):
    relationships: list[RelationshipIn]


class AgentRelationshipIn(BaseModel):
    target_agent_id: str
    relation: str = "collaborator"
    description: str = ""


class AgentRelationshipBatchIn(BaseModel):
    relationships: list[AgentRelationshipIn]


def _dedupe_human_relationships(items: list[RelationshipIn]) -> list[RelationshipIn]:
    deduped: dict[str, RelationshipIn] = {}
    for item in items:
        deduped[item.member_id] = item
    return list(deduped.values())


def _dedupe_agent_relationships(items: list[AgentRelationshipIn], agent_id: uuid.UUID) -> list[AgentRelationshipIn]:
    deduped: dict[str, AgentRelationshipIn] = {}
    for item in items:
        if item.target_agent_id == str(agent_id):
            continue
        deduped[item.target_agent_id] = item
    return list(deduped.values())


# ─── Legacy Human Relationships ────────────────────────

@router.get("/")
async def get_relationships(
    agent_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Legacy: get manually stored human relationship rows for this agent."""
    from app.models.identity import IdentityProvider
    source_agent, _access_level = await check_agent_access(db, current_user, agent_id)
    if await ensure_access_granted_platform_relationships(
        db,
        source_agent,
        created_by_user_id=current_user.id,
    ):
        await _regenerate_relationships_file(db, agent_id)
        await db.commit()
    result = await db.execute(
        select(
            AgentRelationship,
            IdentityProvider.name.label("provider_name"),
            IdentityProvider.provider_type.label("provider_type"),
        )
        .outerjoin(OrgMember, AgentRelationship.member_id == OrgMember.id)
        .outerjoin(IdentityProvider, OrgMember.provider_id == IdentityProvider.id)
        .where(AgentRelationship.agent_id == agent_id)
        .options(selectinload(AgentRelationship.member))
    )
    rows = result.all()
    member_paths = await derive_member_department_paths(
        db,
        [r.member for r, _provider_name, _provider_type in rows if r.member],
    )
    out = []
    for r, provider_name, provider_type in rows:
        linked_user_id = await _get_valid_member_user_id(db, r.member, source_agent.tenant_id) if r.member else None
        out.append({
            "id": str(r.id),
            "member_id": str(r.member_id),
            "relation": r.relation,
            "relation_label": RELATION_LABELS.get(r.relation, r.relation),
            "description": r.description,
            **(await evaluate_human_relationship_status(db, r, source_agent=source_agent)),
            "member": {
                "name": r.member.name,
                "title": r.member.title,
                "department_path": member_paths.get(r.member.id, r.member.department_path),
                "avatar_url": r.member.avatar_url,
                "email": r.member.email,
                "provider_name": _display_provider_name(provider_name, provider_type),
                "provider_type": "platform" if (provider_type or "").lower() == "web" else provider_type,
                "user_id": str(linked_user_id) if linked_user_id else None,
                "is_platform_user": bool(linked_user_id),
            } if r.member else None,
        })
    return out


@router.get("/member-candidates")
async def search_human_relationship_candidates(
    agent_id: uuid.UUID,
    search: str | None = None,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Legacy: search org members that can be stored as relationship rows."""
    from app.models.identity import IdentityProvider

    agent, access_level = await check_agent_access(db, current_user, agent_id)
    if not _can_manage_relationships(current_user, access_level):
        raise HTTPException(status_code=403, detail="Only org admins or managers can modify legacy relationships")

    search_text = (search or "").strip()
    access_mode = getattr(agent, "access_mode", None) or "company"
    LinkedUser = aliased(User)

    query = (
        select(
            OrgMember,
            IdentityProvider.name.label("provider_name"),
            IdentityProvider.provider_type,
            LinkedUser.id.label("linked_user_id"),
        )
        .outerjoin(IdentityProvider, OrgMember.provider_id == IdentityProvider.id)
        .outerjoin(
            LinkedUser,
            and_(
                OrgMember.user_id == LinkedUser.id,
                LinkedUser.tenant_id == agent.tenant_id,
                LinkedUser.is_active == True,  # noqa: E712
            ),
        )
        .where(
            OrgMember.tenant_id == agent.tenant_id,
            OrgMember.status == "active",
            or_(OrgMember.user_id.is_(None), LinkedUser.id.isnot(None)),
        )
    )
    if search_text:
        pattern = f"%{search_text}%"
        query = query.where(
            or_(
                OrgMember.name.ilike(pattern),
                OrgMember.name_translit_full.ilike(pattern),
                OrgMember.name_translit_initial.ilike(pattern),
                OrgMember.email.ilike(pattern),
            )
        )

    allowed_user_ids: set[uuid.UUID] | None = None
    if access_mode != "company":
        allowed_user_ids = await get_agent_accessible_user_ids(db, agent)
        query = query.where(
            or_(
                OrgMember.user_id.is_(None),
                LinkedUser.id.in_(allowed_user_ids),
            )
        )

    result = await db.execute(query.order_by(OrgMember.name).limit(200))
    rows = result.all()
    deduped_filtered = []
    by_user_id: dict[uuid.UUID, tuple[OrgMember, str | None, str | None, uuid.UUID | None]] = {}
    for row in rows:
        member, provider_name, provider_type, linked_user_id = row
        if not linked_user_id:
            deduped_filtered.append(row)
            continue
        existing = by_user_id.get(linked_user_id)
        if not existing:
            by_user_id[linked_user_id] = row
            continue
        existing_type = (existing[2] or "").lower()
        current_type = (provider_type or "").lower()
        if existing_type in ("", "web", "platform") and current_type not in ("", "web", "platform"):
            by_user_id[linked_user_id] = row
    filtered = [*deduped_filtered, *by_user_id.values()]

    filtered = sorted(filtered, key=lambda row: (row[0].name or "").lower())[:100]
    member_paths = await derive_member_department_paths(
        db,
        [m for m, _provider_name, _provider_type, _linked_user_id in filtered],
    )
    org_member_candidates = [
        {
            "id": str(m.id),
            "name": m.name,
            "email": m.email,
            "title": m.title,
            "department_path": member_paths.get(m.id, m.department_path),
            "avatar_url": m.avatar_url,
            "external_id": m.external_id,
            "provider_id": str(m.provider_id) if m.provider_id else None,
            "provider_name": _display_provider_name(provider_name, provider_type) if m.provider_id else None,
            "provider_type": "platform" if (provider_type or "").lower() == "web" else provider_type if m.provider_id else None,
            "user_id": str(linked_user_id) if linked_user_id else None,
            "is_platform_user": bool(linked_user_id),
            "platform_access_level": (
                await get_agent_access_level_for_user_id(db, linked_user_id, agent)
                if linked_user_id
                else None
            ),
        }
        for m, provider_name, provider_type, linked_user_id in filtered
    ]
    return sorted(org_member_candidates, key=lambda item: (item.get("name") or "").lower())[:100]


@router.put("/")
async def save_relationships(
    agent_id: uuid.UUID,
    data: RelationshipBatchIn,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Legacy: replace all manually stored human relationship rows."""
    _agent, access_level = await check_agent_access(db, current_user, agent_id)
    if not _can_manage_relationships(current_user, access_level):
        raise HTTPException(status_code=403, detail="Only org admins or managers can modify legacy relationships")

    existing_result = await db.execute(select(AgentRelationship).where(AgentRelationship.agent_id == agent_id))
    existing_by_member = {r.member_id: r for r in existing_result.scalars().all()}

    await db.execute(
        delete(AgentRelationship).where(AgentRelationship.agent_id == agent_id)
    )

    for r in _dedupe_human_relationships(data.relationships):
        if r.member_id.startswith("platform-user:"):
            platform_user_id = uuid.UUID(r.member_id.split(":", 1)[1])
            user_result = await db.execute(select(User).where(
                User.id == platform_user_id,
                User.tenant_id == _agent.tenant_id,
                User.is_active == True,  # noqa: E712
            ))
            platform_user = user_result.scalar_one_or_none()
            if not platform_user:
                raise HTTPException(status_code=400, detail="Platform user is not available")
            if not await get_agent_access_level_for_user_id(db, platform_user.id, _agent):
                raise HTTPException(status_code=403, detail="Platform user does not have access to this agent")
            member_result = await db.execute(select(OrgMember).where(
                OrgMember.tenant_id == _agent.tenant_id,
                OrgMember.user_id == platform_user.id,
                OrgMember.status == "active",
            ))
            member = member_result.scalar_one_or_none()
            if not member:
                member = OrgMember(
                    tenant_id=_agent.tenant_id,
                    user_id=platform_user.id,
                    external_id=f"platform:{platform_user.id}",
                    name=platform_user.display_name or platform_user.username or platform_user.email or str(platform_user.id),
                    email=platform_user.email,
                    avatar_url=platform_user.avatar_url,
                    title=platform_user.title or "",
                    department_path="",
                    status="active",
                )
                db.add(member)
                await db.flush()
            member_id = member.id
        else:
            member_id = uuid.UUID(r.member_id)
            member_result = await db.execute(select(OrgMember).where(OrgMember.id == member_id))
            member = member_result.scalar_one_or_none()
        if not member or member.tenant_id != _agent.tenant_id or member.status != "active":
            raise HTTPException(status_code=400, detail="Relationship member is not available")
        linked_user_id = await _get_valid_member_user_id(db, member, _agent.tenant_id)
        if member.user_id and not linked_user_id:
            raise HTTPException(status_code=400, detail="Relationship member is linked to an unavailable platform user")
        if linked_user_id and not await get_agent_access_level_for_user_id(db, linked_user_id, _agent):
            raise HTTPException(status_code=403, detail="Platform user does not have access to this agent")
        existing = existing_by_member.get(member_id)
        db.add(AgentRelationship(
            agent_id=agent_id,
            member_id=member_id,
            relation=r.relation,
            description=r.description,
            created_by_user_id=getattr(existing, "created_by_user_id", None) or current_user.id,
            updated_by_user_id=current_user.id,
        ))

    await db.flush()

    # Regenerate file with both types
    await _regenerate_relationships_file(db, agent_id)
    await db.commit()
    return {"status": "ok"}


@router.delete("/{rel_id}")
async def delete_relationship(
    agent_id: uuid.UUID,
    rel_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Delete a single human relationship."""
    _agent, access_level = await check_agent_access(db, current_user, agent_id)
    if not _can_manage_relationships(current_user, access_level):
        raise HTTPException(status_code=403, detail="Only org admins or managers can modify legacy relationships")
    result = await db.execute(
        select(AgentRelationship).where(AgentRelationship.id == rel_id, AgentRelationship.agent_id == agent_id)
    )
    rel = result.scalar_one_or_none()
    if rel:
        await db.delete(rel)
        await db.flush()
        await _regenerate_relationships_file(db, agent_id)
        await db.commit()

    return {"status": "ok"}


# ─── Agent-to-Agent Relationships (new) ───────────────

@router.get("/agent-candidates")
async def search_visible_agents(
    agent_id: uuid.UUID,
    search: str | None = None,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Search manageable agent candidates for relationship creation."""
    source_agent, access_level = await check_agent_access(db, current_user, agent_id)
    if not _can_manage_relationships(current_user, access_level):
        raise HTTPException(status_code=403, detail="Only org admins or managers can modify legacy relationships")

    stmt = build_visible_agents_query(current_user, tenant_id=source_agent.tenant_id).where(Agent.id != agent_id)
    if search:
        stmt = stmt.where(
            or_(
                Agent.name.ilike(f"%{search}%"),
                Agent.role_description.ilike(f"%{search}%"),
            )
        )

    result = await db.execute(stmt.order_by(Agent.created_at.desc()).limit(50))
    agents = [
        agent
        for agent in result.scalars().all()
        if await _can_manage_agent(db, current_user.id, agent)
    ]
    return [
        {
            "id": str(agent.id),
            "name": agent.name,
            "role_description": agent.role_description or "",
            "avatar_url": agent.avatar_url or "",
            "creator_id": str(agent.creator_id),
            "access_mode": getattr(agent, "access_mode", None) or "company",
            "can_manage": True,
        }
        for agent in agents
    ]


@router.get("/agents")
async def get_agent_relationships(
    agent_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Legacy: get manually stored agent-to-agent relationship rows."""
    await check_agent_access(db, current_user, agent_id)
    result = await db.execute(
        select(AgentAgentRelationship)
        .where(AgentAgentRelationship.agent_id == agent_id)
        .options(selectinload(AgentAgentRelationship.target_agent))
    )
    rels = result.scalars().all()
    out = []
    for r in rels:
        status_info = await evaluate_agent_relationship_status(db, r, current_user_id=current_user.id)
        out.append({
            "id": str(r.id),
            "target_agent_id": str(r.target_agent_id),
            "relation": r.relation,
            "relation_label": AGENT_RELATION_LABELS.get(r.relation, r.relation),
            "description": r.description,
            **status_info,
            "target_agent": {
                "id": str(r.target_agent.id),
                "name": r.target_agent.name,
                "role_description": r.target_agent.role_description or "",
                "avatar_url": r.target_agent.avatar_url or "",
                "access_mode": getattr(r.target_agent, "access_mode", None) or "company",
            } if r.target_agent else None,
        })
    return out


@router.get("/agents/candidates")
async def get_agent_relationship_candidates(
    agent_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Legacy: backward-compatible alias for searchable agent candidates."""
    return await search_visible_agents(
        agent_id=agent_id,
        search=None,
        current_user=current_user,
        db=db,
    )


@router.put("/agents")
async def save_agent_relationships(
    agent_id: uuid.UUID,
    data: AgentRelationshipBatchIn,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Legacy: replace all manually stored agent-to-agent relationship rows."""
    source_agent, access_level = await check_agent_access(db, current_user, agent_id)
    if not _can_manage_relationships(current_user, access_level):
        raise HTTPException(status_code=403, detail="Only org admins or managers can modify legacy relationships")

    existing_result = await db.execute(select(AgentAgentRelationship).where(AgentAgentRelationship.agent_id == agent_id))
    existing_by_target = {r.target_agent_id: r for r in existing_result.scalars().all()}

    await db.execute(
        delete(AgentAgentRelationship).where(AgentAgentRelationship.agent_id == agent_id)
    )

    for r in _dedupe_agent_relationships(data.relationships, agent_id):
        target_id = uuid.UUID(r.target_agent_id)
        target_result = await db.execute(
            build_visible_agents_query(current_user, tenant_id=source_agent.tenant_id).where(Agent.id == target_id)
        )
        target_agent = target_result.scalar_one_or_none()
        if not target_agent:
            raise HTTPException(status_code=403, detail="Target agent is not visible to the current user")
        if not await _can_manage_agent(db, current_user.id, target_agent):
            raise HTTPException(status_code=403, detail="You must manage both agents to create this relationship")
        existing = existing_by_target.get(target_id)
        db.add(AgentAgentRelationship(
            agent_id=agent_id,
            target_agent_id=target_id,
            relation=r.relation,
            description=r.description,
            created_by_user_id=getattr(existing, "created_by_user_id", None) or current_user.id,
            updated_by_user_id=current_user.id,
        ))

    await db.flush()
    await _regenerate_relationships_file(db, agent_id)
    await db.commit()
    return {"status": "ok"}


@router.delete("/agents/{rel_id}")
async def delete_agent_relationship(
    agent_id: uuid.UUID,
    rel_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Legacy: delete a single manually stored agent-to-agent relationship row."""
    _agent, access_level = await check_agent_access(db, current_user, agent_id)
    if not _can_manage_relationships(current_user, access_level):
        raise HTTPException(status_code=403, detail="Only org admins or managers can modify legacy relationships")
    result = await db.execute(
        select(AgentAgentRelationship).where(
            AgentAgentRelationship.id == rel_id,
            AgentAgentRelationship.agent_id == agent_id,
        )
    )
    rel = result.scalar_one_or_none()
    if rel:
        await db.delete(rel)
        await db.flush()
        await _regenerate_relationships_file(db, agent_id)
        await db.commit()

    return {"status": "ok"}


# ─── Legacy relationships.md Generation ────────────────

async def _regenerate_relationships_file(db: AsyncSession, agent_id: uuid.UUID):
    """Obsolete. relationships.md is no longer generated as relationships are read directly from the database."""
    pass
