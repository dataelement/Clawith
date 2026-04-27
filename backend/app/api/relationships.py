"""Agent relationship management API — human + agent-to-agent."""

import json
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import delete, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import get_settings
from app.core.permissions import build_visible_agents_query, check_agent_access
from app.core.security import get_current_user
from app.database import get_db
from app.models.agent import Agent
from app.models.org import AgentRelationship, AgentAgentRelationship, OrgMember
from app.services.org_sync_adapter import derive_member_department_paths
from app.models.user import User

settings = get_settings()
router = APIRouter(prefix="/agents/{agent_id}/relationships", tags=["relationships"])

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


# ─── Human Relationships (existing) ───────────────────

@router.get("/")
async def get_relationships(
    agent_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get all human relationships for this agent."""
    from app.models.identity import IdentityProvider
    result = await db.execute(
        select(AgentRelationship, IdentityProvider.name.label("provider_name"))
        .outerjoin(OrgMember, AgentRelationship.member_id == OrgMember.id)
        .outerjoin(IdentityProvider, OrgMember.provider_id == IdentityProvider.id)
        .where(AgentRelationship.agent_id == agent_id)
        .options(selectinload(AgentRelationship.member))
    )
    rows = result.all()
    member_paths = await derive_member_department_paths(
        db,
        [r.member for r, _provider_name in rows if r.member],
    )
    return [
        {
            "id": str(r.id),
            "member_id": str(r.member_id),
            "relation": r.relation,
            "relation_label": RELATION_LABELS.get(r.relation, r.relation),
            "description": r.description,
            "member": {
                "name": r.member.name,
                "title": r.member.title,
                "department_path": member_paths.get(r.member.id, r.member.department_path),
                "avatar_url": r.member.avatar_url,
                "email": r.member.email,
                "provider_name": provider_name,
            } if r.member else None,
        }
        for r, provider_name in rows
    ]


@router.put("/")
async def save_relationships(
    agent_id: uuid.UUID,
    data: RelationshipBatchIn,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Replace all human relationships for this agent."""
    await check_agent_access(db, current_user, agent_id)

    deduped_relationships: list[RelationshipIn] = []
    seen_member_ids: set[str] = set()
    for relationship in data.relationships:
        member_id = str(uuid.UUID(relationship.member_id))
        if member_id in seen_member_ids:
            continue
        seen_member_ids.add(member_id)
        deduped_relationships.append(relationship)

    await db.execute(
        delete(AgentRelationship).where(AgentRelationship.agent_id == agent_id)
    )

    for r in _dedupe_human_relationships(data.relationships):
        db.add(AgentRelationship(
            agent_id=agent_id,
            member_id=uuid.UUID(r.member_id),
            relation=r.relation,
            description=r.description,
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
    await check_agent_access(db, current_user, agent_id)
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
    """Search visible agent candidates for relationship creation."""
    source_agent, _ = await check_agent_access(db, current_user, agent_id)

    stmt = build_visible_agents_query(current_user, tenant_id=source_agent.tenant_id).where(Agent.id != agent_id)
    if search:
        stmt = stmt.where(
            or_(
                Agent.name.ilike(f"%{search}%"),
                Agent.role_description.ilike(f"%{search}%"),
            )
        )

    result = await db.execute(stmt.order_by(Agent.created_at.desc()).limit(50))
    agents = result.scalars().all()
    return [
        {
            "id": str(agent.id),
            "name": agent.name,
            "role_description": agent.role_description or "",
            "avatar_url": agent.avatar_url or "",
            "creator_id": str(agent.creator_id),
        }
        for agent in agents
    ]


@router.get("/agents")
async def get_agent_relationships(
    agent_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get all agent-to-agent relationships."""
    await check_agent_access(db, current_user, agent_id)
    result = await db.execute(
        select(AgentAgentRelationship)
        .where(AgentAgentRelationship.agent_id == agent_id)
        .options(selectinload(AgentAgentRelationship.target_agent))
    )
    rels = result.scalars().all()
    return [
        {
            "id": str(r.id),
            "target_agent_id": str(r.target_agent_id),
            "relation": r.relation,
            "relation_label": AGENT_RELATION_LABELS.get(r.relation, r.relation),
            "description": r.description,
            "target_agent": {
                "id": str(r.target_agent.id),
                "name": r.target_agent.name,
                "role_description": r.target_agent.role_description or "",
                "avatar_url": r.target_agent.avatar_url or "",
            } if r.target_agent else None,
        }
        for r in rels
    ]


@router.get("/agents/candidates")
async def get_agent_relationship_candidates(
    agent_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Backward-compatible alias for searchable agent candidates."""
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
    """Replace all agent-to-agent relationships."""
    source_agent, _ = await check_agent_access(db, current_user, agent_id)

    await db.execute(
        delete(AgentAgentRelationship).where(AgentAgentRelationship.agent_id == agent_id)
    )

    for r in _dedupe_agent_relationships(data.relationships, agent_id):
        target_id = uuid.UUID(r.target_agent_id)
        target_result = await db.execute(
            build_visible_agents_query(current_user, tenant_id=source_agent.tenant_id).where(Agent.id == target_id)
        )
        visible_target = target_result.scalar_one_or_none()
        if not visible_target:
            raise HTTPException(status_code=403, detail="Target agent is not visible to the current user")
        db.add(AgentAgentRelationship(
            agent_id=agent_id,
            target_agent_id=target_id,
            relation=r.relation,
            description=r.description,
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
    """Delete a single agent-to-agent relationship."""
    await check_agent_access(db, current_user, agent_id)
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


# ─── relationships.md Generation ──────────────────────

async def _regenerate_relationships_file(db: AsyncSession, agent_id: uuid.UUID):
    """Regenerate relationships.md with both human and agent relationships."""
    from app.models.identity import IdentityProvider
    # Load human relationships with provider name
    h_result = await db.execute(
        select(AgentRelationship, IdentityProvider.name.label("provider_name"))
        .outerjoin(OrgMember, AgentRelationship.member_id == OrgMember.id)
        .outerjoin(IdentityProvider, OrgMember.provider_id == IdentityProvider.id)
        .where(AgentRelationship.agent_id == agent_id)
        .options(selectinload(AgentRelationship.member))
    )
    human_rows = h_result.all()

    # Load agent relationships
    a_result = await db.execute(
        select(AgentAgentRelationship)
        .where(AgentAgentRelationship.agent_id == agent_id)
        .options(selectinload(AgentAgentRelationship.target_agent))
    )
    agent_rels = a_result.scalars().all()

    ws = Path(settings.AGENT_DATA_DIR) / str(agent_id)
    ws.mkdir(parents=True, exist_ok=True)

    if not human_rows and not agent_rels:
        (ws / "relationships.md").write_text("# 关系网络\n\n_暂无配置的关系。_\n", encoding="utf-8")
        return

    lines = ["# 关系网络\n"]

    # Human relationships
    if human_rows:
        lines.append("## 👤 人类同事\n")
        for r, provider_name in human_rows:
            m = r.member
            if not m:
                continue
            label = RELATION_LABELS.get(r.relation, r.relation)
            source = f"（通过 {provider_name} 同步）" if provider_name else ""
            lines.append(f"### {m.name} — {m.title or '未设置职位'}{source}")
            lines.append(f"- 部门：{m.department_path or '未设置'}")
            lines.append(f"- 关系：{label}")
            if m.open_id:
                lines.append(f"- OpenID：{m.open_id}")
            if m.email:
                lines.append(f"- 邮箱：{m.email}")
            if r.description:
                lines.append(f"- {r.description}")
            lines.append("")

    # Agent relationships
    if agent_rels:
        lines.append("## 🤖 数字员工同事\n")
        for r in agent_rels:
            a = r.target_agent
            if not a:
                continue
            label = AGENT_RELATION_LABELS.get(r.relation, r.relation)
            lines.append(f"### {a.name} — {a.role_description or '数字员工'}")
            lines.append(f"- 关系：{label}")
            lines.append(f"- 可以用 send_message_to_agent 工具给 {a.name} 发消息协作")
            if r.description:
                lines.append(f"- {r.description}")
            lines.append("")

    (ws / "relationships.md").write_text("\n".join(lines), encoding="utf-8")
