"""Experience Library REST API — management + distillation endpoints.

Covers CRUD, review, publish/retire, reference stats, and the human-initiated
distillation flow (`POST /drafts`, LLM draft generation). The AI-side retrieval
(`search_experience` / `read_experience`) lives in services/experience_retrieval.
"""

import json
import re
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from loguru import logger
from pydantic import BaseModel, Field
from sqlalchemy import select, func, desc, or_, and_

from app.api.auth import get_current_user
from app.database import async_session
from app.models.agent import Agent
from app.models.experience import ExperienceEntry
from app.models.experience_reference import ExperienceReference
from app.models.llm import LLMModel
from app.models.org import OrgDepartment, OrgMember
from app.models.user import User

router = APIRouter(prefix="/api/experience", tags=["experience"])

FOUR_PARTS = ("scenario", "problem", "solution", "applicability")
VISIBILITY_SCOPES = ("company", "department", "user")


# ── Schemas ─────────────────────────────────────────

class EntryCreate(BaseModel):
    title: str = Field("", max_length=200)
    scenario: str = ""
    problem: str = ""
    solution: str = ""
    applicability: str = ""
    tags: list[str] = Field(default_factory=list)
    visibility_scope: str = "company"
    visibility_scope_id: uuid.UUID | None = None
    origin_session_id: uuid.UUID | None = None
    origin_agent_id: uuid.UUID | None = None


class DraftFromContent(BaseModel):
    agent_id: uuid.UUID
    content: str
    session_id: uuid.UUID | None = None


class EntryUpdate(BaseModel):
    title: str | None = Field(None, max_length=200)
    scenario: str | None = None
    problem: str | None = None
    solution: str | None = None
    applicability: str | None = None
    tags: list[str] | None = None
    visibility_scope: str | None = None
    visibility_scope_id: uuid.UUID | None = None


class EntryOut(BaseModel):
    id: uuid.UUID
    tenant_id: uuid.UUID | None
    title: str
    scenario: str
    problem: str
    solution: str
    applicability: str
    status: str
    tags: list[str]
    visibility_scope: str
    visibility_scope_id: uuid.UUID | None
    origin: str
    origin_session_id: uuid.UUID | None
    origin_agent_id: uuid.UUID | None
    created_by: uuid.UUID
    reviewed_by: uuid.UUID | None
    last_reviewed_at: datetime | None
    created_at: datetime
    updated_at: datetime | None
    # Display-only (PRD v3 dual creator): resolved names for the publisher + source agent.
    created_by_name: str | None = None
    origin_agent_name: str | None = None

    class Config:
        from_attributes = True


class ReferenceStats(BaseModel):
    entry_id: uuid.UUID
    read_count: int
    cited_count: int


# ── Helpers ─────────────────────────────────────────

def _effective_tenant_id(current_user: User) -> str | None:
    return str(current_user.tenant_id) if current_user.tenant_id else None


def _is_admin(current_user: User) -> bool:
    return current_user.role in ("platform_admin", "org_admin")


async def _agent_creator_id(db, agent_id: uuid.UUID | None) -> uuid.UUID | None:
    """The user who created the agent this entry was distilled from (P0-7)."""
    if not agent_id:
        return None
    return (await db.execute(select(Agent.creator_id).where(Agent.id == agent_id))).scalar_one_or_none()


async def _user_department_ids(db, current_user: User) -> set[uuid.UUID]:
    """Departments the human viewer belongs to (User → OrgMember.department_id)."""
    rows = await db.execute(
        select(OrgMember.department_id).where(
            OrgMember.user_id == current_user.id,
            OrgMember.tenant_id == current_user.tenant_id,
            OrgMember.department_id.isnot(None),
        )
    )
    return {r[0] for r in rows.all() if r[0]}


def _human_visibility_condition(dept_ids: set[uuid.UUID], user_id: uuid.UUID):
    """P0-6 filter for a human viewer: company always; own department; own user-scoped."""
    conds = [ExperienceEntry.visibility_scope == "company"]
    if dept_ids:
        conds.append(
            and_(
                ExperienceEntry.visibility_scope == "department",
                ExperienceEntry.visibility_scope_id.in_(dept_ids),
            )
        )
    conds.append(
        and_(ExperienceEntry.visibility_scope == "user", ExperienceEntry.visibility_scope_id == user_id)
    )
    return or_(*conds)


# ── P0-7: operation permissions (orthogonal to P0-6 visibility) ──
# chat initiator (created_by): may publish + edit
# agent creator (origin_agent_id → creator): may edit + retire; retire is theirs alone
# admins act as a governance backstop across all three.

def _can_edit(current_user: User, entry: ExperienceEntry, agent_creator: uuid.UUID | None) -> bool:
    return (
        _is_admin(current_user)
        or entry.created_by == current_user.id
        or (agent_creator is not None and agent_creator == current_user.id)
    )


def _can_publish(current_user: User, entry: ExperienceEntry) -> bool:
    return _is_admin(current_user) or entry.created_by == current_user.id


def _can_retire(current_user: User, agent_creator: uuid.UUID | None) -> bool:
    return _is_admin(current_user) or (agent_creator is not None and agent_creator == current_user.id)


async def _resolve_publish_visibility(db, entry: ExperienceEntry) -> tuple[str, uuid.UUID | None]:
    """Apply the P0-6 degrade rule at publish time.

    department/user scopes require a target id and a synced org; when the org
    hierarchy is empty (e.g. Feishu org sync not connected) visibility degrades
    to company.
    """
    scope = entry.visibility_scope or "company"
    scope_id = entry.visibility_scope_id
    if scope not in VISIBILITY_SCOPES:
        scope = "company"
    if scope == "department":
        has_departments = (await db.execute(select(OrgDepartment.id).limit(1))).first() is not None
        if not scope_id or not has_departments:
            scope, scope_id = "company", None
    elif scope == "user":
        if not scope_id:
            scope, scope_id = "company", None
    else:  # company
        scope_id = None
    return scope, scope_id


async def _serialize_entries(db, entries: list[ExperienceEntry]) -> list[EntryOut]:
    """EntryOut list with the publisher + source-agent names resolved (display only)."""
    user_ids = {e.created_by for e in entries if e.created_by}
    agent_ids = {e.origin_agent_id for e in entries if e.origin_agent_id}
    users = {}
    if user_ids:
        # Use the real `display_name` column only — `User.username` is an association_proxy
        # to Identity and must not be touched in this async path.
        users = {
            u.id: (u.display_name or None)
            for u in (await db.execute(select(User).where(User.id.in_(user_ids)))).scalars().all()
        }
    agents = {}
    if agent_ids:
        agents = {
            a.id: a.name
            for a in (await db.execute(select(Agent).where(Agent.id.in_(agent_ids)))).scalars().all()
        }
    out = []
    for e in entries:
        o = EntryOut.model_validate(e)
        o.created_by_name = users.get(e.created_by)
        o.origin_agent_name = agents.get(e.origin_agent_id) if e.origin_agent_id else None
        out.append(o)
    return out


async def _get_entry_scoped(db, entry_id: uuid.UUID, current_user: User) -> ExperienceEntry:
    """Fetch an entry, enforcing tenant isolation. Raises 404 if not visible."""
    q = select(ExperienceEntry).where(ExperienceEntry.id == entry_id)
    eff = _effective_tenant_id(current_user)
    if eff and current_user.role != "platform_admin":
        q = q.where(ExperienceEntry.tenant_id == eff)
    entry = (await db.execute(q)).scalar_one_or_none()
    if not entry:
        raise HTTPException(404, "Experience entry not found")
    return entry


# ── Routes ──────────────────────────────────────────

@router.get("/entries", response_model=list[EntryOut])
async def list_entries(
    view: str = "team",
    status: str | None = None,
    tag: str | None = None,
    q: str | None = None,
    limit: int = 50,
    offset: int = 0,
    current_user: User = Depends(get_current_user),
):
    """List experience entries, scoped to the caller's tenant.

    `view`:
      - team    (default): published entries visible to me (P0-6 human filter). The
                 "公司最新经验" feed / 团队经验 view.
      - mine    : entries I can manage (I distilled, or I created the source agent).
      - history : the 历史沉淀 (待整理) partition (origin=legacy_plaza).
      - all     : whole tenant, no visibility filter (admins).
    """
    eff = _effective_tenant_id(current_user)
    order_col = desc(ExperienceEntry.last_reviewed_at) if view == "team" else desc(ExperienceEntry.updated_at)
    async with async_session() as db:
        query = select(ExperienceEntry).order_by(order_col)
        if eff:
            query = query.where(ExperienceEntry.tenant_id == eff)

        if view == "history":
            query = query.where(ExperienceEntry.origin == "legacy_plaza")
        else:
            query = query.where(ExperienceEntry.origin != "legacy_plaza")

        if view == "team":
            query = query.where(ExperienceEntry.status == "published")
            if not _is_admin(current_user):
                dept_ids = await _user_department_ids(db, current_user)
                query = query.where(_human_visibility_condition(dept_ids, current_user.id))
        elif view == "mine":
            managed_agent_ids = (
                await db.execute(select(Agent.id).where(Agent.creator_id == current_user.id))
            ).scalars().all()
            mine_cond = [ExperienceEntry.created_by == current_user.id]
            if managed_agent_ids:
                mine_cond.append(ExperienceEntry.origin_agent_id.in_(managed_agent_ids))
            query = query.where(or_(*mine_cond))

        if status:
            query = query.where(ExperienceEntry.status == status)
        if q:
            like = f"%{q}%"
            query = query.where(or_(ExperienceEntry.title.ilike(like), ExperienceEntry.scenario.ilike(like)))
        query = query.offset(offset).limit(limit)
        entries = (await db.execute(query)).scalars().all()
        # tag filter is applied in Python to stay portable across JSON backends
        if tag:
            entries = [e for e in entries if tag in (e.tags or [])]
        return await _serialize_entries(db, entries)


@router.post("/entries", response_model=EntryOut)
async def create_entry(body: EntryCreate, current_user: User = Depends(get_current_user)):
    """Create a draft entry. Publishing (making it retrievable) is a separate, explicit step."""
    eff = _effective_tenant_id(current_user)
    scope = body.visibility_scope if body.visibility_scope in VISIBILITY_SCOPES else "company"
    async with async_session() as db:
        entry = ExperienceEntry(
            tenant_id=eff,
            title=body.title[:200],
            scenario=body.scenario,
            problem=body.problem,
            solution=body.solution,
            applicability=body.applicability,
            tags=body.tags or [],
            status="draft",
            visibility_scope=scope,
            visibility_scope_id=body.visibility_scope_id if scope != "company" else None,
            origin="chat",
            origin_session_id=body.origin_session_id,
            origin_agent_id=body.origin_agent_id,
            created_by=current_user.id,
        )
        db.add(entry)
        await db.commit()
        await db.refresh(entry)
        return EntryOut.model_validate(entry)


_DISTILL_SYSTEM = (
    "你是经验沉淀助手。基于用户选中的一段工作内容，把它抽取成一条可复用的团队经验。"
    "严格只输出一个 JSON 对象，不要任何解释或 markdown 代码块，字段如下：\n"
    '{"title": "", "scenario": "", "problem": "", "solution": "", "applicability": "", "tags": []}\n'
    "- scenario 场景、problem 遇到的问题、solution 解决方式。\n"
    "- applicability（适用条件与失效信号）必须写明：此经验在什么前提下成立、出现什么信号说明它已过时失效。\n"
    "- 信息不足的字段留空字符串，不要编造；tags 给 1-3 个简短标签。"
)


def _parse_draft_json(text: str) -> dict:
    """Extract the JSON object from the LLM reply; tolerate code fences / prose."""
    if not text:
        return {}
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        return {}
    try:
        data = json.loads(m.group(0))
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        return {}


@router.post("/drafts", response_model=EntryOut)
async def create_draft_from_content(body: DraftFromContent, current_user: User = Depends(get_current_user)):
    """P0-2: distill selected chat content into a structured draft (never auto-published).

    On LLM/parse failure, returns a draft with the raw content in `problem` so the
    human can still fill it in — the flow never hard-fails.
    """
    if not body.content.strip():
        raise HTTPException(400, "Content cannot be empty")
    eff = _effective_tenant_id(current_user)
    async with async_session() as db:
        agent = (await db.execute(select(Agent).where(Agent.id == body.agent_id))).scalar_one_or_none()
        if not agent or (eff and str(agent.tenant_id) != eff):
            raise HTTPException(404, "Agent not found")

        fields: dict = {}
        try:
            model_id = agent.primary_model_id or agent.fallback_model_id
            model = (
                (await db.execute(select(LLMModel).where(LLMModel.id == model_id))).scalar_one_or_none()
                if model_id else None
            )
            if model:
                from app.services.llm import get_model_api_key
                from app.services.llm.client import chat_complete

                resp = await chat_complete(
                    provider=model.provider,
                    api_key=get_model_api_key(model),
                    model=model.model,
                    base_url=model.base_url,
                    messages=[
                        {"role": "system", "content": _DISTILL_SYSTEM},
                        {"role": "user", "content": body.content[:6000]},
                    ],
                    temperature=0.2,
                )
                fields = _parse_draft_json(resp["choices"][0]["message"].get("content") or "")
        except Exception as e:
            logger.warning(f"Experience distillation LLM call failed: {e}")

        tags = fields.get("tags") or []
        if not isinstance(tags, list):
            tags = []
        # Graceful fallback: if the model produced nothing usable, seed problem with the raw text.
        problem = (fields.get("problem") or "").strip()
        if not any((fields.get(k) or "").strip() for k in FOUR_PARTS):
            problem = body.content[:2000]

        entry = ExperienceEntry(
            tenant_id=eff,
            title=(fields.get("title") or "")[:200],
            scenario=fields.get("scenario") or "",
            problem=problem,
            solution=fields.get("solution") or "",
            applicability=fields.get("applicability") or "",
            tags=[str(t)[:40] for t in tags][:5],
            status="draft",
            visibility_scope="company",
            origin="chat",
            origin_session_id=body.session_id,
            origin_agent_id=body.agent_id,
            created_by=current_user.id,
        )
        db.add(entry)
        await db.commit()
        await db.refresh(entry)
        return EntryOut.model_validate(entry)


@router.get("/entries/{entry_id}", response_model=EntryOut)
async def get_entry(entry_id: uuid.UUID, current_user: User = Depends(get_current_user)):
    async with async_session() as db:
        entry = await _get_entry_scoped(db, entry_id, current_user)
        return (await _serialize_entries(db, [entry]))[0]


@router.patch("/entries/{entry_id}", response_model=EntryOut)
async def update_entry(entry_id: uuid.UUID, body: EntryUpdate, current_user: User = Depends(get_current_user)):
    """Edit any field. Allowed for admins and the entry's initiator (P0-2 / P0-5)."""
    async with async_session() as db:
        entry = await _get_entry_scoped(db, entry_id, current_user)
        agent_creator = await _agent_creator_id(db, entry.origin_agent_id)
        if not _can_edit(current_user, entry, agent_creator):
            raise HTTPException(403, "Not allowed to edit this entry")
        data = body.model_dump(exclude_unset=True)
        if "visibility_scope" in data and data["visibility_scope"] not in VISIBILITY_SCOPES:
            raise HTTPException(422, "Invalid visibility_scope")
        for field, value in data.items():
            if field == "title" and value is not None:
                value = value[:200]
            setattr(entry, field, value)
        if entry.visibility_scope == "company":
            entry.visibility_scope_id = None
        await db.commit()
        await db.refresh(entry)
        return EntryOut.model_validate(entry)


@router.post("/entries/{entry_id}/publish", response_model=EntryOut)
async def publish_entry(entry_id: uuid.UUID, current_user: User = Depends(get_current_user)):
    """Publish a draft. Enforces the P0-3 hard constraint: all four parts must be present."""
    async with async_session() as db:
        entry = await _get_entry_scoped(db, entry_id, current_user)
        if not _can_publish(current_user, entry):
            raise HTTPException(403, "Not allowed to publish this entry")
        if entry.origin == "legacy_plaza":
            # History imports must be triaged (four parts filled) before entering the live library.
            raise HTTPException(409, "Legacy entries must be edited into a normal draft before publishing")
        missing = [f for f in FOUR_PARTS if not (getattr(entry, f) or "").strip()]
        if missing:
            raise HTTPException(422, f"Cannot publish — required parts are empty: {', '.join(missing)}")
        # P0-6 degrade rule applied at publish time.
        entry.visibility_scope, entry.visibility_scope_id = await _resolve_publish_visibility(db, entry)
        entry.status = "published"
        entry.reviewed_by = current_user.id
        entry.last_reviewed_at = datetime.now(timezone.utc)
        await db.commit()
        await db.refresh(entry)
        logger.info(f"Experience entry {entry_id} published by {current_user.id}")
        return EntryOut.model_validate(entry)


@router.post("/entries/{entry_id}/retire", response_model=EntryOut)
async def retire_entry(entry_id: uuid.UUID, current_user: User = Depends(get_current_user)):
    """Retire an entry so it is no longer returned by search_experience (P0-5).

    P0-7: retiring affects others' reuse, so it is restricted to the agent's creator
    (admins retained as a governance backstop).
    """
    async with async_session() as db:
        entry = await _get_entry_scoped(db, entry_id, current_user)
        agent_creator = await _agent_creator_id(db, entry.origin_agent_id)
        if not _can_retire(current_user, agent_creator):
            raise HTTPException(403, "Only the agent's creator can retire this entry")
        entry.status = "retired"
        await db.commit()
        await db.refresh(entry)
        logger.info(f"Experience entry {entry_id} retired by {current_user.id}")
        return EntryOut.model_validate(entry)


@router.post("/entries/{entry_id}/review", response_model=EntryOut)
async def review_entry(entry_id: uuid.UUID, current_user: User = Depends(get_current_user)):
    """Re-affirm that a published entry is still valid, refreshing its review time (P1-2)."""
    async with async_session() as db:
        entry = await _get_entry_scoped(db, entry_id, current_user)
        agent_creator = await _agent_creator_id(db, entry.origin_agent_id)
        if not _can_edit(current_user, entry, agent_creator):
            raise HTTPException(403, "Not allowed to review this entry")
        entry.last_reviewed_at = datetime.now(timezone.utc)
        entry.reviewed_by = current_user.id
        await db.commit()
        await db.refresh(entry)
        return EntryOut.model_validate(entry)


@router.get("/entries/{entry_id}/references", response_model=ReferenceStats)
async def entry_references(entry_id: uuid.UUID, current_user: User = Depends(get_current_user)):
    """Reuse stats for an entry: read vs cited counted separately (adoption uses cited only)."""
    async with async_session() as db:
        await _get_entry_scoped(db, entry_id, current_user)  # enforce visibility
        counts = dict(
            (row[0], row[1])
            for row in (
                await db.execute(
                    select(ExperienceReference.kind, func.count(ExperienceReference.id))
                    .where(ExperienceReference.entry_id == entry_id)
                    .group_by(ExperienceReference.kind)
                )
            ).all()
        )
        return ReferenceStats(
            entry_id=entry_id,
            read_count=counts.get("read", 0),
            cited_count=counts.get("cited", 0),
        )


class LibraryStats(BaseModel):
    total: int
    today: int
    cited: int
    top_contributors: list[dict]


@router.get("/stats", response_model=LibraryStats)
async def library_stats(current_user: User = Depends(get_current_user)):
    """Header stats for the 公司最新经验 feed, over entries visible to the caller.

    total = published visible entries; today = of those, created today;
    cited = adoption events on them; top_contributors = publishers by entry count.
    """
    eff = _effective_tenant_id(current_user)
    today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    async with async_session() as db:
        base = [ExperienceEntry.status == "published", ExperienceEntry.origin != "legacy_plaza"]
        if eff:
            base.append(ExperienceEntry.tenant_id == eff)
        if not _is_admin(current_user):
            dept_ids = await _user_department_ids(db, current_user)
            base.append(_human_visibility_condition(dept_ids, current_user.id))

        total = (await db.execute(select(func.count(ExperienceEntry.id)).where(*base))).scalar() or 0
        today = (
            await db.execute(select(func.count(ExperienceEntry.id)).where(*base, ExperienceEntry.created_at >= today_start))
        ).scalar() or 0

        visible_ids = select(ExperienceEntry.id).where(*base)
        cited = (
            await db.execute(
                select(func.count(ExperienceReference.id)).where(
                    ExperienceReference.kind == "cited",
                    ExperienceReference.entry_id.in_(visible_ids),
                )
            )
        ).scalar() or 0

        rows = (
            await db.execute(
                select(ExperienceEntry.created_by, func.count(ExperienceEntry.id).label("n"))
                .where(*base)
                .group_by(ExperienceEntry.created_by)
                .order_by(desc("n"))
                .limit(5)
            )
        ).all()
        contributors = []
        if rows:
            uids = [r[0] for r in rows]
            users = {
                u.id: (u.display_name or u.username or "—")
                for u in (await db.execute(select(User).where(User.id.in_(uids)))).scalars().all()
            }
            contributors = [{"name": users.get(r[0], "—"), "count": r[1]} for r in rows]

        return LibraryStats(total=total, today=today, cited=cited, top_contributors=contributors)
