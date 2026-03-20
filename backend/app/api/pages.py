"""Public pages API — serves published HTML without authentication."""

import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core.security import get_current_user
from app.database import get_db
from app.models.published_page import PublishedPage
from app.models.user import User

settings = get_settings()

# Public router — no /api prefix, no auth
public_router = APIRouter(tags=["pages"])

# Authenticated router — under /api prefix
router = APIRouter(prefix="/pages", tags=["pages"])


def _agent_base_dir(agent_id: uuid.UUID) -> Path:
    return Path(settings.AGENT_DATA_DIR) / str(agent_id)


# ── Public render (NO auth) ────────────────────────────

@public_router.get("/p/{short_id}")
async def render_page(short_id: str, db: AsyncSession = Depends(get_db)):
    """Serve a published HTML page. No authentication required."""
    result = await db.execute(
        select(PublishedPage).where(PublishedPage.short_id == short_id)
    )
    page = result.scalar_one_or_none()
    if not page:
        raise HTTPException(status_code=404, detail="Page not found")

    # Read the HTML file from agent workspace
    file_path = _agent_base_dir(page.agent_id) / page.source_path
    if not file_path.exists() or not file_path.is_file():
        raise HTTPException(status_code=404, detail="Source file no longer exists")

    html_content = file_path.read_text(encoding="utf-8", errors="replace")

    # Increment view count
    await db.execute(
        update(PublishedPage)
        .where(PublishedPage.id == page.id)
        .values(view_count=PublishedPage.view_count + 1)
    )
    await db.commit()

    return HTMLResponse(
        content=html_content,
        headers={
            # CSP sandbox: isolates origin, prevents access to parent localStorage/cookies
            "Content-Security-Policy": "sandbox allow-scripts allow-forms allow-popups allow-modals",
            "X-Content-Type-Options": "nosniff",
        },
    )


# ── Authenticated endpoints ────────────────────────────

@router.get("/list")
async def list_pages(
    agent_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List published pages for an agent."""
    from app.core.permissions import check_agent_access
    await check_agent_access(db, current_user, agent_id)

    result = await db.execute(
        select(PublishedPage)
        .where(PublishedPage.agent_id == agent_id)
        .order_by(PublishedPage.created_at.desc())
    )
    pages = result.scalars().all()
    return [
        {
            "id": str(p.id),
            "short_id": p.short_id,
            "source_path": p.source_path,
            "title": p.title,
            "view_count": p.view_count,
            "created_at": p.created_at.isoformat() if p.created_at else None,
            "url": f"/p/{p.short_id}",
        }
        for p in pages
    ]
