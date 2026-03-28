"""Agent relationship management API — human + agent-to-agent."""

import json
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import get_settings
from app.core.security import get_current_user
from app.core.permissions import check_agent_access
from app.database import get_db
from app.models.org import AgentRelationship, AgentAgentRelationship, OrgMember
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
                "department_path": r.member.department_path,
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

    await db.execute(
        delete(AgentRelationship).where(AgentRelationship.agent_id == agent_id)
    )

    for r in data.relationships:
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


@router.put("/agents")
async def save_agent_relationships(
    agent_id: uuid.UUID,
    data: AgentRelationshipBatchIn,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Replace all agent-to-agent relationships."""
    await check_agent_access(db, current_user, agent_id)

    await db.execute(
        delete(AgentAgentRelationship).where(AgentAgentRelationship.agent_id == agent_id)
    )

    for r in data.relationships:
        target_id = uuid.UUID(r.target_agent_id)
        if target_id == agent_id:
            continue  # skip self-reference
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
            lines.append(f"- 可以用 send_agent_message 工具给 {a.name} 发消息协作")
            if r.description:
                lines.append(f"- {r.description}")
            lines.append("")

    (ws / "relationships.md").write_text("\n".join(lines), encoding="utf-8")
