"""Notification API — list, count, mark-read, and broadcast."""

import uuid
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select, func, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import get_current_user
from app.database import get_db
from app.models.notification import Notification
from app.models.user import User

router = APIRouter(tags=["notifications"])

# Category -> type mapping for filtering
CATEGORY_TYPE_MAP: dict[str, list[str]] = {
    "tool": ["autonomy_l2"],
    "approval": ["approval_pending", "approval_resolved"],
    "social": ["plaza_comment", "plaza_reply", "mention", "broadcast"],
    "broadcast": ["broadcast"],
}


def _apply_category_filter(query, category: Optional[str]):
    """Apply category-based type filtering to a query."""
    if category and category != "all" and category in CATEGORY_TYPE_MAP:
        query = query.where(Notification.type.in_(CATEGORY_TYPE_MAP[category]))
    return query


@router.get("/notifications")
async def list_notifications(
    limit: int = Query(50, le=200),
    offset: int = Query(0, ge=0),
    unread_only: bool = Query(False),
    category: Optional[str] = Query(None),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List notifications for the current user, newest first."""
    query = select(Notification).where(Notification.user_id == current_user.id)
    if unread_only:
        query = query.where(Notification.is_read == False)  # noqa: E712
    query = _apply_category_filter(query, category)
    query = query.order_by(Notification.created_at.desc()).offset(offset).limit(limit)
    result = await db.execute(query)
    notifications = result.scalars().all()
    return [
        {
            "id": str(n.id),
            "type": n.type,
            "title": n.title,
            "body": n.body,
            "link": n.link,
            "ref_id": str(n.ref_id) if n.ref_id else None,
            "sender_name": n.sender_name,
            "is_read": n.is_read,
            "created_at": n.created_at.isoformat() if n.created_at else None,
        }
        for n in notifications
    ]


@router.get("/notifications/unread-count")
async def get_unread_count(
    category: Optional[str] = Query(None),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get the number of unread notifications for the current user."""
    query = select(func.count(Notification.id)).where(
        Notification.user_id == current_user.id,
        Notification.is_read == False,  # noqa: E712
    )
    query = _apply_category_filter(query, category)
    result = await db.execute(query)
    return {"unread_count": result.scalar() or 0}


@router.post("/notifications/{notification_id}/read")
async def mark_read(
    notification_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Mark a single notification as read."""
    await db.execute(
        update(Notification)
        .where(Notification.id == notification_id, Notification.user_id == current_user.id)
        .values(is_read=True)
    )
    await db.commit()
    return {"ok": True}


@router.post("/notifications/read-all")
async def mark_all_read(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Mark all notifications as read for the current user."""
    await db.execute(
        update(Notification)
        .where(Notification.user_id == current_user.id, Notification.is_read == False)  # noqa: E712
        .values(is_read=True)
    )
    await db.commit()
    return {"ok": True}


# ── Broadcast ──────────────────────────────────────────

class BroadcastRequest(BaseModel):
    title: str = Field(..., max_length=200)
    body: str = Field("", max_length=1000)
    send_email: bool = False


@router.post("/notifications/broadcast")
async def broadcast_notification(
    req: BroadcastRequest,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Send a notification to all users and agents in the current tenant.
    Requires org_admin or platform_admin role."""
    if current_user.role not in ("platform_admin", "org_admin"):
        raise HTTPException(403, "Only org admins can send broadcasts")
    if not current_user.tenant_id:
        raise HTTPException(400, "No tenant associated with your account")

    from app.models.agent import Agent
    from app.services.notification_service import send_notification

    tenant_id = current_user.tenant_id
    sender_name = current_user.display_name or current_user.username or "Admin"
    count_users = 0
    count_agents = 0
    count_emails = 0
    email_recipients = []

    if req.send_email:
        from app.services.system_email_service import resolve_email_config_async

        email_config = await resolve_email_config_async(db)
        if not email_config:
            raise HTTPException(400, "System email is not configured. Please configure it in Platform Settings.")

    # Notify all users in tenant
    users_result = await db.execute(
        select(User).where(User.tenant_id == tenant_id, User.id != current_user.id)
    )
    users = users_result.scalars().all()
    for user in users:
        await send_notification(
            db, user_id=user.id,
            type="broadcast",
            title=req.title,
            body=req.body,
            sender_name=sender_name,
        )
        count_users += 1

    # Notify all agents in tenant
    agents_result = await db.execute(
        select(Agent).where(Agent.tenant_id == tenant_id)
    )
    for agent in agents_result.scalars().all():
        await send_notification(
            db, agent_id=agent.id,
            type="broadcast",
            title=req.title,
            body=req.body,
            sender_name=sender_name,
        )
        count_agents += 1

    if req.send_email:
        from app.services.system_email_service import (
            BroadcastEmailRecipient,
            deliver_broadcast_emails,
            run_background_email_job,
        )

        for user in users:
            if not user.email:
                continue
            email_recipients.append(
                BroadcastEmailRecipient(
                    email=user.email,
                    subject=req.title,
                    body=(
                        f"{req.body}\n\n"
                        f"Sent by: {sender_name}"
                        if req.body.strip()
                        else f"Sent by: {sender_name}"
                    ),
                ),
            )
            count_emails += 1

    await db.commit()
    if email_recipients:
        background_tasks.add_task(run_background_email_job, deliver_broadcast_emails, email_recipients)
    return {
        "ok": True,
        "users_notified": count_users,
        "agents_notified": count_agents,
        "emails_sent": count_emails,
    }
