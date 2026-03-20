"""Notification API — list, count, and mark-read for the current user."""

import uuid
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select, func, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import get_current_user
from app.database import get_db
from app.models.notification import Notification
from app.models.user import User

router = APIRouter(tags=["notifications"])

# Category → type mapping for filtering
CATEGORY_TYPE_MAP: dict[str, list[str]] = {
    "tool": ["autonomy_l2"],
    "approval": ["approval_pending", "approval_resolved"],
    "social": ["plaza_comment", "plaza_reply"],
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
