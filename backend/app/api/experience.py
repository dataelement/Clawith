"""Experience Library REST API — management + distillation endpoints.

Covers CRUD, review, publish/retire, reference stats, and the human-initiated
distillation flow (`POST /drafts`, LLM draft generation). The AI-side retrieval
(`search_experience` / `read_experience`) lives in services/experience_retrieval.
"""

import json
import re
import uuid
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from loguru import logger
from pydantic import BaseModel, Field
from sqlalchemy import cast, desc, func, or_, select
from sqlalchemy.dialects.postgresql import JSONB

from app.api.auth import get_current_user
from app.database import async_session
from app.models.agent import Agent
from app.models.experience import ExperienceEntry
from app.models.experience_reference import ExperienceReference
from app.models.llm import LLMModel
from app.models.user import User

router = APIRouter(prefix="/api/experience", tags=["experience"])

# Required to publish. `applicability` is the hard one: it is the candidate preview
# `search_experience` shows the agent, so an entry without it can never be skipped
# cheaply — it would have to be read in full to find out it doesn't apply.
REQUIRED_PARTS = (("title", "标题"), ("body", "正文"), ("applicability", "适用条件与失效信号"))
VISIBILITY_SCOPES = ("company", "department", "user")


# ── Schemas ─────────────────────────────────────────

class EntryCreate(BaseModel):
    title: str = Field("", max_length=200)
    body: str = ""
    applicability: str = ""
    tags: list[str] = Field(default_factory=list)
    # Accepted for legacy clients; published Experience is always tenant-wide.
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
    body: str | None = None
    applicability: str | None = None
    tags: list[str] | None = None
    # Accepted for legacy clients but cannot make published Experience private.
    visibility_scope: str | None = None
    visibility_scope_id: uuid.UUID | None = None


class EntryOut(BaseModel):
    id: uuid.UUID
    draft_of_id: uuid.UUID | None
    tenant_id: uuid.UUID | None
    title: str
    body: str
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
    retired_at: datetime | None
    created_at: datetime
    updated_at: datetime | None
    # Display-only (PRD v3 dual creator): resolved names for the publisher + source agent.
    created_by_name: str | None = None
    origin_agent_name: str | None = None
    # Whether the caller may edit / review / retire / re-publish this entry (same permission
    # set: initiator, source-agent creator, or admin). Populated on single-entry fetch so the
    # UI can hide actions the user can't perform. None in list responses.
    can_manage: bool | None = None

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


# ── Management permissions (independent from tenant-wide published reads) ──
# chat initiator (created_by): may publish + edit + retire
# agent creator (origin_agent_id → creator): may edit + retire + re-publish
# admins act as a governance backstop across all three.
# Retire is shared by the initiator and the agent creator: an initiator who
# sedimented a mistake must be able to take it down themselves.

def _can_edit(current_user: User, entry: ExperienceEntry, agent_creator: uuid.UUID | None) -> bool:
    return (
        _is_admin(current_user)
        or entry.created_by == current_user.id
        or (agent_creator is not None and agent_creator == current_user.id)
    )


def _can_publish(current_user: User, entry: ExperienceEntry) -> bool:
    return _is_admin(current_user) or entry.created_by == current_user.id


def _can_retire(current_user: User, entry: ExperienceEntry, agent_creator: uuid.UUID | None) -> bool:
    return (
        _is_admin(current_user)
        or entry.created_by == current_user.id
        or (agent_creator is not None and agent_creator == current_user.id)
    )


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
    """Fetch an entry with tenant isolation only; mutation routes add permission checks."""
    q = select(ExperienceEntry).where(ExperienceEntry.id == entry_id)
    eff = _effective_tenant_id(current_user)
    if eff and current_user.role != "platform_admin":
        q = q.where(ExperienceEntry.tenant_id == eff)
    entry = (await db.execute(q)).scalar_one_or_none()
    if not entry:
        raise HTTPException(404, "Experience entry not found")
    return entry


async def _get_entry_readable(
    db,
    entry_id: uuid.UUID,
    current_user: User,
) -> tuple[ExperienceEntry, uuid.UUID | None]:
    """Fetch an entry under the human read contract.

    Published, non-legacy experience is public to every member in the tenant.
    Draft and retired entries remain visible only to an existing manager. A 404
    hides the existence of entries the caller cannot read.
    """
    entry = await _get_entry_scoped(db, entry_id, current_user)
    agent_creator = await _agent_creator_id(db, entry.origin_agent_id)
    can_manage = _can_edit(current_user, entry, agent_creator)
    if can_manage or (entry.status == "published" and entry.origin != "legacy_plaza"):
        return entry, agent_creator
    raise HTTPException(404, "Experience entry not found")


# ── Routes ──────────────────────────────────────────

@router.get("/entries", response_model=list[EntryOut])
async def list_entries(
    view: Literal["team", "mine", "all"] = "team",
    status: str | None = None,
    tag: str | None = None,
    q: str | None = None,
    limit: int = 50,
    offset: int = 0,
    current_user: User = Depends(get_current_user),
):
    """List experience entries, scoped to the caller's tenant.

    `view`:
      - team    (default): all published entries in the tenant. The
                 "公司最新经验" feed / 团队经验 view.
      - mine    : entries I can manage (I distilled, or I created the source agent).
      - all     : whole tenant, no visibility filter (admins).
    """
    if view == "all" and not _is_admin(current_user):
        raise HTTPException(403, "Admin access is required for the all view")

    eff = _effective_tenant_id(current_user)
    order_col = desc(ExperienceEntry.last_reviewed_at) if view == "team" else desc(ExperienceEntry.updated_at)
    async with async_session() as db:
        query = select(ExperienceEntry).order_by(order_col, desc(ExperienceEntry.id))
        if eff:
            query = query.where(ExperienceEntry.tenant_id == eff)

        # legacy_plaza imports are hard-isolated — never surfaced through any view.
        query = query.where(ExperienceEntry.origin != "legacy_plaza")

        if view == "team":
            query = query.where(ExperienceEntry.status == "published")
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
            query = query.where(or_(ExperienceEntry.title.ilike(like), ExperienceEntry.body.ilike(like)))
        if tag:
            # ExperienceEntry.tags is legacy PostgreSQL JSON. Cast to JSONB so
            # membership is evaluated before offset/limit without a schema migration.
            query = query.where(cast(ExperienceEntry.tags, JSONB).contains([tag]))
        query = query.offset(offset).limit(limit)
        entries = (await db.execute(query)).scalars().all()
        return await _serialize_entries(db, entries)


def _norm(s: str | None) -> str:
    return re.sub(r"\s+", " ", (s or "").strip())


def _norm_tags(tags) -> list[str]:
    """Strip/collapse whitespace, drop blanks, dedupe (case-insensitive), keep order."""
    out, seen = [], set()
    for t in (tags or []):
        t = _norm(str(t))
        k = t.lower()
        if t and k not in seen:
            seen.add(k)
            out.append(t)
    return out


def _signature(title, body, applicability) -> tuple:
    return tuple(_norm(x) for x in (title, body, applicability))


async def _find_identical(db, eff: str | None, payload: "EntryCreate"):
    """Return an existing non-retired entry in this tenant with identical content.

    Guards against accidental duplicate sedimentation (double-click, re-opening the same
    card). Any edit to the content changes the signature, so genuine variants still pass.
    """
    sig = _signature(payload.title, payload.body, payload.applicability)
    if not any(sig[1:]):  # body + applicability both blank → don't dedupe (allow blank drafts)
        return None
    q = select(ExperienceEntry).where(ExperienceEntry.status != "retired")
    if eff:
        q = q.where(ExperienceEntry.tenant_id == eff)
    q = q.limit(500)
    for e in (await db.execute(q)).scalars().all():
        if _signature(e.title, e.body, e.applicability) == sig:
            return e
    return None


@router.post("/entries", response_model=EntryOut)
async def create_entry(payload: EntryCreate, current_user: User = Depends(get_current_user)):
    """Create a draft entry. Publishing (making it retrievable) is a separate, explicit step.

    Rejects an exact duplicate (same title + body + applicability) that already exists —
    prevents accidental repeated sedimentation while still allowing edited variants.
    """
    eff = _effective_tenant_id(current_user)
    async with async_session() as db:
        dupe = await _find_identical(db, eff, payload)
        if dupe:
            raise HTTPException(409, f"内容完全相同的经验已存在（“{dupe.title or '未命名'}”），无需重复沉淀。")
        entry = ExperienceEntry(
            tenant_id=eff,
            title=payload.title[:200],
            body=payload.body,
            applicability=payload.applicability,
            tags=_norm_tags(payload.tags),
            status="draft",
            # Legacy clients may still send visibility fields. Human Experience
            # publishing is tenant-wide now, so new entries always start canonical.
            visibility_scope="company",
            visibility_scope_id=None,
            origin="chat",
            origin_session_id=payload.origin_session_id,
            origin_agent_id=payload.origin_agent_id,
            created_by=current_user.id,
        )
        db.add(entry)
        await db.commit()
        await db.refresh(entry)
        return EntryOut.model_validate(entry)


# Seeded into an empty editor and suggested to the distiller — a default, not a schema.
BODY_TEMPLATE = "## 场景\n\n## 遇到的问题\n\n## 解决方式\n"

_DISTILL_SYSTEM = (
    "你是经验沉淀助手。基于用户选中的一段工作内容，把它抽取成一条可复用的团队经验。"
    "严格只输出一个 JSON 对象，不要任何解释或 markdown 代码块，字段如下：\n"
    '{"title": "", "body": "", "applicability": "", "tags": []}\n'
    "- body 是经验正文，markdown 格式。默认用「## 场景 / ## 遇到的问题 / ## 解决方式」三个小节；"
    "但若内容本就不是「问题—解决」型（例如一份配置说明、一条参考事实），就按内容自然组织小节，不要硬套。"
    "正文中的换行必须转义为 \\n，确保整个 JSON 合法。\n"
    "- applicability（适用条件与失效信号）必填：此经验在什么前提下成立、出现什么信号说明它已过时失效。"
    "它会脱离正文单独展示给检索方，用来判断该不该读全文，因此必须能独立读懂，写成一两句话。\n"
    "- 信息不足的字段留空字符串，不要编造；tags 给 1-3 个简短标签。"
)

_CTRL_ESCAPES = {"\n": "\\n", "\r": "\\r", "\t": "\\t"}


def _escape_raw_control_chars(s: str) -> str:
    """Escape literal newlines/tabs occurring *inside* JSON string literals.

    The markdown body is multi-line, and models sometimes emit those newlines raw
    instead of as `\\n`, which makes the object invalid JSON. Repairing beats losing
    the whole draft — the human still reviews everything before it is published.
    """
    out: list[str] = []
    in_str = esc = False
    for ch in s:
        if esc:
            out.append(ch)
            esc = False
        elif in_str and ch == "\\":
            out.append(ch)
            esc = True
        elif ch == '"':
            in_str = not in_str
            out.append(ch)
        elif in_str and ch in _CTRL_ESCAPES:
            out.append(_CTRL_ESCAPES[ch])
        else:
            out.append(ch)
    return "".join(out)


def _parse_draft_json(text: str) -> dict:
    """Extract the JSON object from the LLM reply; tolerate code fences / prose.

    No retry against the model: on failure the caller returns empty fields and the
    editor asks the human to fill them in. The human review step is the retry.
    """
    if not text:
        return {}
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        return {}
    raw = m.group(0)
    for candidate in (raw, _escape_raw_control_chars(raw)):
        try:
            data = json.loads(candidate)
            if isinstance(data, dict):
                return data
        except json.JSONDecodeError:
            continue
    return {}


class DistillResult(BaseModel):
    title: str = ""
    body: str = ""
    applicability: str = ""
    tags: list[str] = Field(default_factory=list)
    # False when the LLM produced nothing usable — the UI then shows a
    # "未能自动抽取，请手动填写" hint instead of seeding any field with raw text.
    extracted: bool = True


async def _distill_fields(db, agent, content: str) -> dict:
    """Run the LLM distillation and normalize the fields. Persists nothing.

    On LLM/parse failure the fields are left empty and `extracted=False` is
    returned, so the editor prompts the human to fill them in manually. We never
    seed a field with the raw text — a wrong auto-fill is worse than an empty one.
    """
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
                    {"role": "user", "content": content[:6000]},
                ],
                temperature=0.2,
            )
            fields = _parse_draft_json(resp["choices"][0]["message"].get("content") or "")
    except Exception as e:
        logger.warning(f"Experience distillation LLM call failed: {e}")

    tags = fields.get("tags") or []
    if not isinstance(tags, list):
        tags = []
    extracted = any((fields.get(k) or "").strip() for k, _ in REQUIRED_PARTS)
    return {
        "title": (fields.get("title") or "")[:200],
        "body": fields.get("body") or "",
        "applicability": fields.get("applicability") or "",
        "tags": [str(t)[:40] for t in tags][:5],
        "extracted": extracted,
    }


@router.post("/distill", response_model=DistillResult)
async def distill_content(payload: DraftFromContent, current_user: User = Depends(get_current_user)):
    """Distill selected chat content into title / body / applicability WITHOUT persisting.

    The human reviews/confirms in the editor; a row is created only then (via /entries).
    Keeps the human-gate: clicking 沉淀 creates no library row until the user confirms.
    """
    if not payload.content.strip():
        raise HTTPException(400, "Content cannot be empty")
    eff = _effective_tenant_id(current_user)
    async with async_session() as db:
        agent = (await db.execute(select(Agent).where(Agent.id == payload.agent_id))).scalar_one_or_none()
        if not agent or (eff and str(agent.tenant_id) != eff):
            raise HTTPException(404, "Agent not found")
        return DistillResult(**await _distill_fields(db, agent, payload.content))


@router.post("/drafts", response_model=EntryOut)
async def create_draft_from_content(payload: DraftFromContent, current_user: User = Depends(get_current_user)):
    """Distill + persist a draft in one step (kept for compatibility). Prefer /distill
    then /entries so nothing persists until the human confirms."""
    if not payload.content.strip():
        raise HTTPException(400, "Content cannot be empty")
    eff = _effective_tenant_id(current_user)
    async with async_session() as db:
        agent = (await db.execute(select(Agent).where(Agent.id == payload.agent_id))).scalar_one_or_none()
        if not agent or (eff and str(agent.tenant_id) != eff):
            raise HTTPException(404, "Agent not found")
        f = await _distill_fields(db, agent, payload.content)
        entry = ExperienceEntry(
            tenant_id=eff, title=f["title"], body=f["body"], applicability=f["applicability"],
            tags=f["tags"], status="draft", visibility_scope="company", origin="chat",
            origin_session_id=payload.session_id, origin_agent_id=payload.agent_id,
            created_by=current_user.id,
        )
        db.add(entry)
        await db.commit()
        await db.refresh(entry)
        return EntryOut.model_validate(entry)


@router.get("/entries/{entry_id}", response_model=EntryOut)
async def get_entry(entry_id: uuid.UUID, current_user: User = Depends(get_current_user)):
    async with async_session() as db:
        entry, agent_creator = await _get_entry_readable(db, entry_id, current_user)
        out = (await _serialize_entries(db, [entry]))[0]
        out.can_manage = _can_edit(current_user, entry, agent_creator)
        return out


@router.post("/entries/{entry_id}/draft", response_model=EntryOut)
async def create_revision_draft(
    entry_id: uuid.UUID,
    body: EntryUpdate,
    current_user: User = Depends(get_current_user),
):
    """Create an independent draft while keeping a published source live.

    The draft points back to the stable source entry. Deleting it only removes
    the draft; publishing it atomically updates the source and preserves the
    source id, references, and adoption history.
    """
    async with async_session() as db:
        source = await _get_entry_scoped(db, entry_id, current_user)
        agent_creator = await _agent_creator_id(db, source.origin_agent_id)
        if not _can_edit(current_user, source, agent_creator):
            raise HTTPException(403, "Not allowed to edit this entry")
        if source.status == "draft":
            raise HTTPException(409, "草稿请直接编辑，无需再创建草稿版本")

        data = body.model_dump(exclude_unset=True)
        title = data.get("title", source.title)
        content = data.get("body", source.body)
        applicability = data.get("applicability", source.applicability)
        tags = data.get("tags", source.tags)
        revision = ExperienceEntry(
            draft_of_id=source.id,
            tenant_id=source.tenant_id,
            title=(title or "")[:200],
            body=content or "",
            applicability=applicability or "",
            tags=_norm_tags(tags or []),
            status="draft",
            visibility_scope="company",
            visibility_scope_id=None,
            # Editing a legacy import is how it becomes a normal Experience
            # draft; the source keeps its stable id when this is published.
            origin="chat" if source.origin == "legacy_plaza" else source.origin,
            origin_session_id=source.origin_session_id,
            origin_agent_id=source.origin_agent_id,
            created_by=current_user.id,
        )
        db.add(revision)
        await db.commit()
        await db.refresh(revision)
        return EntryOut.model_validate(revision)


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
        visibility_was_provided = "visibility_scope" in data or "visibility_scope_id" in data
        for field, value in data.items():
            if field in {"visibility_scope", "visibility_scope_id"}:
                continue
            if field == "title" and value is not None:
                value = value[:200]
            if field == "tags" and value is not None:
                value = _norm_tags(value)
            setattr(entry, field, value)
        if visibility_was_provided or entry.status == "published":
            entry.visibility_scope = "company"
            entry.visibility_scope_id = None
        await db.commit()
        await db.refresh(entry)
        return EntryOut.model_validate(entry)


@router.post("/entries/{entry_id}/publish", response_model=EntryOut)
async def publish_entry(entry_id: uuid.UUID, current_user: User = Depends(get_current_user)):
    """Publish a draft. Enforces the P0-3 hard constraint: title + body + applicability."""
    async with async_session() as db:
        entry = await _get_entry_scoped(db, entry_id, current_user)
        # Re-publishing a retired entry is also allowed to the source agent's creator
        # (they can retire it, so they can bring it back); first-time publish stays the
        # initiator's gate.
        is_republish = entry.status == "retired"
        agent_creator = await _agent_creator_id(db, entry.origin_agent_id) if is_republish else None
        allowed = _can_publish(current_user, entry) or (
            is_republish and agent_creator is not None and agent_creator == current_user.id
        )
        if not allowed:
            raise HTTPException(403, "Not allowed to publish this entry")
        if entry.origin == "legacy_plaza":
            # History imports must be triaged into a normal draft before entering the live library.
            raise HTTPException(409, "Legacy entries must be edited into a normal draft before publishing")
        missing = [label for field, label in REQUIRED_PARTS if not (getattr(entry, field) or "").strip()]
        if missing:
            raise HTTPException(422, f"无法发布 — 以下必填项为空：{'、'.join(missing)}")

        if entry.draft_of_id:
            source = await _get_entry_scoped(db, entry.draft_of_id, current_user)
            agent_creator = await _agent_creator_id(db, source.origin_agent_id)
            if not _can_edit(current_user, source, agent_creator):
                raise HTTPException(403, "Not allowed to replace this entry")
            if source.status not in ("published", "retired"):
                raise HTTPException(409, "草稿对应的原经验已不再可更新")

            source.title = entry.title
            source.body = entry.body
            source.applicability = entry.applicability
            source.tags = _norm_tags(entry.tags)
            source.visibility_scope = "company"
            source.visibility_scope_id = None
            source.origin = entry.origin
            source.status = "published"
            source.retired_at = None
            source.reviewed_by = current_user.id
            source.last_reviewed_at = datetime.now(timezone.utc)
            await db.delete(entry)
            await db.commit()
            await db.refresh(source)
            logger.info(
                f"Experience revision {entry_id} published into source {source.id} "
                f"by {current_user.id}"
            )
            return EntryOut.model_validate(source)

        # Published Experience is tenant-wide. Normalize legacy private metadata
        # whenever an entry crosses the publication boundary.
        entry.visibility_scope = "company"
        entry.visibility_scope_id = None
        entry.status = "published"
        entry.retired_at = None  # re-publishing clears the 30-day deletion clock
        entry.reviewed_by = current_user.id
        entry.last_reviewed_at = datetime.now(timezone.utc)
        await db.commit()
        await db.refresh(entry)
        logger.info(f"Experience entry {entry_id} published by {current_user.id}")
        return EntryOut.model_validate(entry)


@router.post("/entries/{entry_id}/retire", response_model=EntryOut)
async def retire_entry(entry_id: uuid.UUID, current_user: User = Depends(get_current_user)):
    """Retire an entry so it is no longer returned by search_experience (P0-5).

    P0-7: allowed to the chat initiator, the source agent's creator, or an admin.
    Retired entries move to the "已下架" bin; if not re-published within 30 days the
    background sweep hard-deletes them.
    """
    async with async_session() as db:
        entry = await _get_entry_scoped(db, entry_id, current_user)
        agent_creator = await _agent_creator_id(db, entry.origin_agent_id)
        if not _can_retire(current_user, entry, agent_creator):
            raise HTTPException(403, "Not allowed to retire this entry")
        entry.status = "retired"
        entry.retired_at = datetime.now(timezone.utc)
        await db.commit()
        await db.refresh(entry)
        logger.info(f"Experience entry {entry_id} retired by {current_user.id}")
        return EntryOut.model_validate(entry)


@router.post("/entries/{entry_id}/review", response_model=EntryOut)
async def review_entry(entry_id: uuid.UUID, current_user: User = Depends(get_current_user)):
    """Toggle review state (P1-2): if reviewed, mark un-reviewed; else mark reviewed now."""
    async with async_session() as db:
        entry = await _get_entry_scoped(db, entry_id, current_user)
        agent_creator = await _agent_creator_id(db, entry.origin_agent_id)
        if not _can_edit(current_user, entry, agent_creator):
            raise HTTPException(403, "Not allowed to review this entry")
        if entry.last_reviewed_at is None:
            entry.last_reviewed_at = datetime.now(timezone.utc)
            entry.reviewed_by = current_user.id
        else:
            entry.last_reviewed_at = None  # toggle back to 未复核
        await db.commit()
        await db.refresh(entry)
        return EntryOut.model_validate(entry)


@router.delete("/entries/{entry_id}")
async def delete_entry(entry_id: uuid.UUID, current_user: User = Depends(get_current_user)):
    """Hard-delete an entry. Published entries must be retired first (to preserve adoption
    records); drafts and retired entries can be deleted outright."""
    async with async_session() as db:
        entry = await _get_entry_scoped(db, entry_id, current_user)
        agent_creator = await _agent_creator_id(db, entry.origin_agent_id)
        if not _can_edit(current_user, entry, agent_creator):
            raise HTTPException(403, "Not allowed to delete this entry")
        if entry.status == "published":
            raise HTTPException(409, "已发布经验请先下架再删除（以保留采纳记录）")
        await db.delete(entry)
        await db.commit()
        logger.info(f"Experience entry {entry_id} deleted by {current_user.id}")
        return {"deleted": True}


@router.get("/entries/{entry_id}/references", response_model=ReferenceStats)
async def entry_references(entry_id: uuid.UUID, current_user: User = Depends(get_current_user)):
    """Reuse stats for an entry: read vs cited counted separately (adoption uses cited only)."""
    async with async_session() as db:
        await _get_entry_readable(db, entry_id, current_user)
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
    """Header stats for the tenant-wide 公司最新经验 feed.

    total = published tenant entries; today = of those, created today;
    cited = adoption events on them; top_contributors = publishers by entry count.
    """
    eff = _effective_tenant_id(current_user)
    today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    async with async_session() as db:
        base = [ExperienceEntry.status == "published", ExperienceEntry.origin != "legacy_plaza"]
        if eff:
            base.append(ExperienceEntry.tenant_id == eff)

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
