"""CRM REST API — contacts, deals, activities with batch operations."""

import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select, or_, delete as sql_delete, func
from sqlalchemy.orm import selectinload

from app.database import async_session
from app.api.auth import get_current_user
from app.models.user import User
from app.models.crm import CRMContact, CRMDeal, CRMActivity

router = APIRouter(prefix="/crm", tags=["crm"])

VALID_STAGES = ("lead", "contacted", "qualified", "proposal", "negotiation", "won", "lost")


class ContactCreate(BaseModel):
    name: str
    company: str | None = None
    email: str | None = None
    phone: str | None = None
    country: str | None = None
    industry: str | None = None
    source: str | None = None
    tags: list[str] | None = None
    notes: str | None = None

class ContactUpdate(BaseModel):
    name: str | None = None
    company: str | None = None
    email: str | None = None
    phone: str | None = None
    country: str | None = None
    industry: str | None = None
    source: str | None = None
    tags: list[str] | None = None
    notes: str | None = None

class DealCreate(BaseModel):
    contact_id: uuid.UUID
    title: str
    stage: str = "lead"
    value: float | None = None
    currency: str = "USD"
    notes: str | None = None

class DealUpdate(BaseModel):
    title: str | None = None
    stage: str | None = None
    value: float | None = None
    currency: str | None = None
    notes: str | None = None

class ActivityCreate(BaseModel):
    contact_id: uuid.UUID
    type: str
    summary: str

class BatchStageUpdate(BaseModel):
    deal_ids: list[uuid.UUID]
    stage: str

class BatchIds(BaseModel):
    ids: list[uuid.UUID]


def _contact_dict(c: CRMContact) -> dict:
    d = {
        "id": str(c.id), "name": c.name, "company": c.company,
        "email": c.email, "phone": c.phone, "country": c.country,
        "industry": c.industry, "source": c.source, "tags": c.tags or [],
        "notes": c.notes,
        "created_at": c.created_at.isoformat() if c.created_at else None,
    }
    if hasattr(c, "deals") and c.deals is not None:
        d["deals"] = [{"id": str(dl.id), "title": dl.title, "stage": dl.stage,
                        "value": float(dl.value) if dl.value else None, "currency": dl.currency}
                       for dl in c.deals]
    else:
        d["deals"] = []
    return d

def _deal_dict(d: CRMDeal) -> dict:
    return {
        "id": str(d.id), "contact_id": str(d.contact_id), "title": d.title,
        "stage": d.stage, "value": float(d.value) if d.value else None,
        "currency": d.currency, "notes": d.notes,
        "created_at": d.created_at.isoformat() if d.created_at else None,
        "contact_name": d.contact.name if d.contact else None,
        "contact_company": d.contact.company if d.contact else None,
        "contact_email": d.contact.email if d.contact else None,
    }


# ── Contacts ──

@router.get("/contacts")
async def list_contacts(search: str = "", country: str = "", source: str = "",
                        page: int = 1, size: int = 100,
                        current_user: User = Depends(get_current_user)):
    async with async_session() as db:
        q = select(CRMContact).where(CRMContact.tenant_id == current_user.tenant_id
                                     ).options(selectinload(CRMContact.deals))
        if search:
            q = q.where(or_(CRMContact.name.ilike(f"%{search}%"),
                            CRMContact.company.ilike(f"%{search}%"),
                            CRMContact.email.ilike(f"%{search}%")))
        if country:
            q = q.where(CRMContact.country.ilike(f"%{country}%"))
        if source:
            q = q.where(CRMContact.source == source)
        total = (await db.execute(select(func.count()).select_from(q.subquery()))).scalar() or 0
        q = q.order_by(CRMContact.created_at.desc()).offset((page - 1) * size).limit(size)
        contacts = (await db.execute(q)).scalars().all()
        return {"items": [_contact_dict(c) for c in contacts], "total": total, "page": page}


@router.get("/contacts/{contact_id}")
async def get_contact(contact_id: uuid.UUID, current_user: User = Depends(get_current_user)):
    async with async_session() as db:
        c = (await db.execute(
            select(CRMContact).where(CRMContact.id == contact_id, CRMContact.tenant_id == current_user.tenant_id
                                     ).options(selectinload(CRMContact.deals), selectinload(CRMContact.activities))
        )).scalar_one_or_none()
        if not c:
            raise HTTPException(404, "Contact not found")
        d = _contact_dict(c)
        d["activities"] = [{"id": str(a.id), "type": a.type, "summary": a.summary,
                            "created_at": a.created_at.isoformat() if a.created_at else None}
                           for a in sorted(c.activities, key=lambda x: x.created_at, reverse=True)[:50]]
        return d


@router.post("/contacts", status_code=201)
async def create_contact(body: ContactCreate, current_user: User = Depends(get_current_user)):
    async with async_session() as db:
        c = CRMContact(tenant_id=current_user.tenant_id, **body.model_dump(exclude_none=True))
        db.add(c)
        await db.commit()
        await db.refresh(c)
        return {"id": str(c.id), "name": c.name, "company": c.company, "email": c.email,
                "phone": c.phone, "country": c.country, "industry": c.industry,
                "source": c.source, "tags": c.tags or [], "deals": [],
                "created_at": c.created_at.isoformat() if c.created_at else None}


@router.patch("/contacts/{contact_id}")
async def update_contact(contact_id: uuid.UUID, body: ContactUpdate, current_user: User = Depends(get_current_user)):
    async with async_session() as db:
        c = (await db.execute(select(CRMContact).where(
            CRMContact.id == contact_id, CRMContact.tenant_id == current_user.tenant_id))).scalar_one_or_none()
        if not c:
            raise HTTPException(404, "Contact not found")
        for k, v in body.model_dump(exclude_none=True).items():
            setattr(c, k, v)
        await db.commit()
        await db.refresh(c)
        return {"id": str(c.id), "name": c.name, "company": c.company, "email": c.email,
                "phone": c.phone, "country": c.country, "industry": c.industry,
                "source": c.source, "tags": c.tags or [], "deals": [],
                "created_at": c.created_at.isoformat() if c.created_at else None}


@router.delete("/contacts/{contact_id}")
async def delete_contact(contact_id: uuid.UUID, current_user: User = Depends(get_current_user)):
    async with async_session() as db:
        c = (await db.execute(select(CRMContact).where(
            CRMContact.id == contact_id, CRMContact.tenant_id == current_user.tenant_id))).scalar_one_or_none()
        if not c:
            raise HTTPException(404, "Contact not found")
        await db.execute(sql_delete(CRMActivity).where(CRMActivity.contact_id == contact_id))
        await db.execute(sql_delete(CRMDeal).where(CRMDeal.contact_id == contact_id))
        await db.execute(sql_delete(CRMContact).where(CRMContact.id == contact_id))
        await db.commit()
    return {"message": "deleted"}


@router.post("/contacts/batch-delete")
async def batch_delete_contacts(body: BatchIds, current_user: User = Depends(get_current_user)):
    async with async_session() as db:
        for cid in body.ids:
            await db.execute(sql_delete(CRMActivity).where(CRMActivity.contact_id == cid, CRMActivity.tenant_id == current_user.tenant_id))
            await db.execute(sql_delete(CRMDeal).where(CRMDeal.contact_id == cid, CRMDeal.tenant_id == current_user.tenant_id))
            await db.execute(sql_delete(CRMContact).where(CRMContact.id == cid, CRMContact.tenant_id == current_user.tenant_id))
        await db.commit()
    return {"deleted": len(body.ids)}


# ── Deals ──

@router.get("/deals")
async def list_deals(stage: str = "", current_user: User = Depends(get_current_user)):
    async with async_session() as db:
        q = select(CRMDeal).where(CRMDeal.tenant_id == current_user.tenant_id).options(selectinload(CRMDeal.contact))
        if stage:
            q = q.where(CRMDeal.stage == stage)
        deals = (await db.execute(q.order_by(CRMDeal.created_at.desc()).limit(500))).scalars().all()
        return [_deal_dict(d) for d in deals]


@router.post("/deals", status_code=201)
async def create_deal(body: DealCreate, current_user: User = Depends(get_current_user)):
    if body.stage not in VALID_STAGES:
        raise HTTPException(400, f"Invalid stage: {body.stage}")
    async with async_session() as db:
        contact = (await db.execute(select(CRMContact).where(
            CRMContact.id == body.contact_id, CRMContact.tenant_id == current_user.tenant_id))).scalar_one_or_none()
        if not contact:
            raise HTTPException(404, "Contact not found")
        d = CRMDeal(tenant_id=current_user.tenant_id, **body.model_dump())
        db.add(d)
        db.add(CRMActivity(tenant_id=current_user.tenant_id, contact_id=body.contact_id,
                           type="deal_update", summary=f"Created deal: {body.title}"))
        await db.commit()
        await db.refresh(d)
        return {"id": str(d.id), "title": d.title, "stage": d.stage,
                "value": float(d.value) if d.value else None,
                "contact_name": contact.name, "contact_company": contact.company, "contact_email": contact.email}


@router.patch("/deals/{deal_id}")
async def update_deal(deal_id: uuid.UUID, body: DealUpdate, current_user: User = Depends(get_current_user)):
    if body.stage and body.stage not in VALID_STAGES:
        raise HTTPException(400, f"Invalid stage: {body.stage}")
    async with async_session() as db:
        d = (await db.execute(select(CRMDeal).where(
            CRMDeal.id == deal_id, CRMDeal.tenant_id == current_user.tenant_id))).scalar_one_or_none()
        if not d:
            raise HTTPException(404, "Deal not found")
        old_stage = d.stage
        for k, v in body.model_dump(exclude_none=True).items():
            setattr(d, k, v)
        if body.stage and body.stage != old_stage:
            db.add(CRMActivity(tenant_id=current_user.tenant_id, contact_id=d.contact_id,
                               type="deal_update", summary=f"Stage: {old_stage} -> {body.stage}"))
        await db.commit()
        return {"id": str(d.id), "title": d.title, "stage": d.stage}


@router.delete("/deals/{deal_id}")
async def delete_deal(deal_id: uuid.UUID, current_user: User = Depends(get_current_user)):
    async with async_session() as db:
        d = (await db.execute(select(CRMDeal).where(
            CRMDeal.id == deal_id, CRMDeal.tenant_id == current_user.tenant_id))).scalar_one_or_none()
        if not d:
            raise HTTPException(404, "Deal not found")
        await db.execute(sql_delete(CRMDeal).where(CRMDeal.id == deal_id))
        await db.commit()
    return {"message": "deleted"}


@router.post("/deals/batch-stage")
async def batch_update_stage(body: BatchStageUpdate, current_user: User = Depends(get_current_user)):
    if body.stage not in VALID_STAGES:
        raise HTTPException(400, f"Invalid stage")
    async with async_session() as db:
        n = 0
        for did in body.deal_ids:
            d = (await db.execute(select(CRMDeal).where(
                CRMDeal.id == did, CRMDeal.tenant_id == current_user.tenant_id))).scalar_one_or_none()
            if d and d.stage != body.stage:
                old = d.stage
                d.stage = body.stage
                db.add(CRMActivity(tenant_id=current_user.tenant_id, contact_id=d.contact_id,
                                   type="deal_update", summary=f"Batch: {old} -> {body.stage}"))
                n += 1
        await db.commit()
    return {"updated": n}


@router.post("/deals/batch-delete")
async def batch_delete_deals(body: BatchIds, current_user: User = Depends(get_current_user)):
    async with async_session() as db:
        for did in body.ids:
            await db.execute(sql_delete(CRMDeal).where(CRMDeal.id == did, CRMDeal.tenant_id == current_user.tenant_id))
        await db.commit()
    return {"deleted": len(body.ids)}


# ── Activities ──

@router.get("/activities/{contact_id}")
async def list_activities(contact_id: uuid.UUID, current_user: User = Depends(get_current_user)):
    async with async_session() as db:
        acts = (await db.execute(
            select(CRMActivity).where(CRMActivity.contact_id == contact_id, CRMActivity.tenant_id == current_user.tenant_id
                                      ).order_by(CRMActivity.created_at.desc()).limit(100))).scalars().all()
        return [{"id": str(a.id), "type": a.type, "summary": a.summary,
                 "created_at": a.created_at.isoformat() if a.created_at else None} for a in acts]


@router.post("/activities", status_code=201)
async def create_activity(body: ActivityCreate, current_user: User = Depends(get_current_user)):
    async with async_session() as db:
        cr = (await db.execute(select(CRMContact).where(
            CRMContact.id == body.contact_id, CRMContact.tenant_id == current_user.tenant_id))).scalar_one_or_none()
        if not cr:
            raise HTTPException(404, "Contact not found")
        a = CRMActivity(tenant_id=current_user.tenant_id, **body.model_dump())
        db.add(a)
        await db.commit()
        await db.refresh(a)
        return {"id": str(a.id), "type": a.type, "summary": a.summary}


# ── Stats ──

@router.get("/stats")
async def crm_stats(current_user: User = Depends(get_current_user)):
    async with async_session() as db:
        tid = current_user.tenant_id
        contacts = (await db.execute(select(func.count()).select_from(CRMContact).where(CRMContact.tenant_id == tid))).scalar() or 0
        deals_total = (await db.execute(select(func.count()).select_from(CRMDeal).where(CRMDeal.tenant_id == tid))).scalar() or 0
        pipeline = {}
        for s in VALID_STAGES:
            row = (await db.execute(select(func.count(), func.coalesce(func.sum(CRMDeal.value), 0)).where(
                CRMDeal.tenant_id == tid, CRMDeal.stage == s))).one()
            pipeline[s] = {"count": row[0], "value": float(row[1])}
        return {"contacts": contacts, "deals": deals_total, "pipeline": pipeline}
