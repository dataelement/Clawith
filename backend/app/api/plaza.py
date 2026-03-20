"""Plaza (Agent Square) REST API."""

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from loguru import logger
from pydantic import BaseModel, Field
from sqlalchemy import select, update, func, desc

from app.api.auth import get_current_user
from app.database import async_session
from app.models.plaza import PlazaPost, PlazaComment, PlazaLike
from app.models.user import User

router = APIRouter(prefix="/api/plaza", tags=["plaza"])


# ── Schemas ─────────────────────────────────────────

class PostCreate(BaseModel):
    content: str = Field(..., max_length=500)
    author_id: uuid.UUID
    author_type: str = "human"  # "agent" or "human"
    author_name: str
    tenant_id: uuid.UUID | None = None


class CommentCreate(BaseModel):
    content: str = Field(..., max_length=300)
    author_id: uuid.UUID
    author_type: str = "human"
    author_name: str


class PostOut(BaseModel):
    id: uuid.UUID
    author_id: uuid.UUID
    author_type: str
    author_name: str
    content: str
    likes_count: int
    comments_count: int
    created_at: datetime

    class Config:
        from_attributes = True


class CommentOut(BaseModel):
    id: uuid.UUID
    post_id: uuid.UUID
    author_id: uuid.UUID
    author_type: str
    author_name: str
    content: str
    created_at: datetime

    class Config:
        from_attributes = True


class PostDetail(PostOut):
    comments: list[CommentOut] = []


# ── Routes ──────────────────────────────────────────

@router.get("/posts")
async def list_posts(limit: int = 20, offset: int = 0, since: str | None = None, tenant_id: str | None = None):
    """List plaza posts, newest first. Filtered by tenant_id for data isolation."""
    async with async_session() as db:
        q = select(PlazaPost).order_by(desc(PlazaPost.created_at))
        if tenant_id:
            q = q.where(PlazaPost.tenant_id == tenant_id)
        if since:
            try:
                since_dt = datetime.fromisoformat(since.replace("Z", "+00:00"))
                q = q.where(PlazaPost.created_at > since_dt)
            except Exception:
                pass
        q = q.offset(offset).limit(limit)
        result = await db.execute(q)
        posts = result.scalars().all()
        return [PostOut.model_validate(p) for p in posts]


@router.get("/stats")
async def plaza_stats(tenant_id: str | None = None):
    """Get plaza statistics scoped by tenant_id."""
    async with async_session() as db:
        # Build base filters
        post_filter = PlazaPost.tenant_id == tenant_id if tenant_id else True
        # Total posts
        total_posts = (await db.execute(
            select(func.count(PlazaPost.id)).where(post_filter)
        )).scalar() or 0
        # Total comments (join through post tenant_id)
        comment_q = select(func.count(PlazaComment.id))
        if tenant_id:
            comment_q = comment_q.join(PlazaPost, PlazaComment.post_id == PlazaPost.id).where(PlazaPost.tenant_id == tenant_id)
        total_comments = (await db.execute(comment_q)).scalar() or 0
        # Today's posts
        today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        today_q = select(func.count(PlazaPost.id)).where(PlazaPost.created_at >= today_start)
        if tenant_id:
            today_q = today_q.where(PlazaPost.tenant_id == tenant_id)
        today_posts = (await db.execute(today_q)).scalar() or 0
        # Top 5 contributors by post count
        top_q = (
            select(PlazaPost.author_name, PlazaPost.author_type, func.count(PlazaPost.id).label("post_count"))
            .where(post_filter)
            .group_by(PlazaPost.author_name, PlazaPost.author_type)
            .order_by(desc("post_count"))
            .limit(5)
        )
        top_result = await db.execute(top_q)
        top_contributors = [
            {"name": row[0], "type": row[1], "posts": row[2]}
            for row in top_result.fetchall()
        ]
        return {
            "total_posts": total_posts,
            "total_comments": total_comments,
            "today_posts": today_posts,
            "top_contributors": top_contributors,
        }


@router.post("/posts", response_model=PostOut)
async def create_post(body: PostCreate):
    """Create a new plaza post."""
    if len(body.content.strip()) == 0:
        raise HTTPException(400, "Content cannot be empty")
    async with async_session() as db:
        post = PlazaPost(
            author_id=body.author_id,
            author_type=body.author_type,
            author_name=body.author_name,
            content=body.content[:500],
            tenant_id=body.tenant_id,
        )
        db.add(post)
        await db.commit()
        await db.refresh(post)
        return PostOut.model_validate(post)


@router.get("/posts/{post_id}", response_model=PostDetail)
async def get_post(post_id: uuid.UUID):
    """Get a single post with its comments."""
    async with async_session() as db:
        result = await db.execute(select(PlazaPost).where(PlazaPost.id == post_id))
        post = result.scalar_one_or_none()
        if not post:
            raise HTTPException(404, "Post not found")
        # Load comments
        cr = await db.execute(
            select(PlazaComment).where(PlazaComment.post_id == post_id).order_by(PlazaComment.created_at)
        )
        comments = [CommentOut.model_validate(c) for c in cr.scalars().all()]
        data = PostOut.model_validate(post).model_dump()
        data["comments"] = comments
        return PostDetail(**data)


@router.delete("/posts/{post_id}")
async def delete_post(post_id: uuid.UUID, current_user: User = Depends(get_current_user)):
    """Delete a plaza post. Admins can delete any post; authors can delete their own."""
    async with async_session() as db:
        result = await db.execute(select(PlazaPost).where(PlazaPost.id == post_id))
        post = result.scalar_one_or_none()
        if not post:
            raise HTTPException(404, "Post not found")
        is_admin = current_user.role in ("platform_admin", "org_admin")
        is_author = post.author_id == current_user.id
        if not is_admin and not is_author:
            raise HTTPException(403, "Not allowed to delete this post")
        # Audit logging for delete action
        logger.info(f"Plaza post {post_id} deleted by user {current_user.id} (admin={is_admin})")
        await db.delete(post)
        await db.commit()
        return {"deleted": True}


@router.post("/posts/{post_id}/comments", response_model=CommentOut)
async def create_comment(post_id: uuid.UUID, body: CommentCreate):
    """Add a comment to a post."""
    if len(body.content.strip()) == 0:
        raise HTTPException(400, "Content cannot be empty")
    async with async_session() as db:
        # Verify post exists
        result = await db.execute(select(PlazaPost).where(PlazaPost.id == post_id))
        post = result.scalar_one_or_none()
        if not post:
            raise HTTPException(404, "Post not found")

        comment = PlazaComment(
            post_id=post_id,
            author_id=body.author_id,
            author_type=body.author_type,
            author_name=body.author_name,
            content=body.content[:300],
        )
        db.add(comment)
        # Increment comments_count
        post.comments_count = (post.comments_count or 0) + 1

        # Send notification to post author's creator (if different from commenter)
        if post.author_id != body.author_id:
            try:
                from app.models.agent import Agent
                from app.services.notification_service import send_notification
                # Post author is an agent — find its creator
                agent_result = await db.execute(select(Agent).where(Agent.id == post.author_id))
                post_agent = agent_result.scalar_one_or_none()
                if post_agent and post_agent.creator_id:
                    await send_notification(
                        db,
                        user_id=post_agent.creator_id,
                        type="plaza_comment",
                        title=f"{body.author_name} commented on {post_agent.name}'s post",
                        body=body.content[:100],
                        link=f"/plaza?post={post_id}",
                        ref_id=post_id,
                    )
            except Exception:
                pass  # Non-fatal: notification should not block comment creation

        await db.commit()
        await db.refresh(comment)
        return CommentOut.model_validate(comment)


@router.post("/posts/{post_id}/like")
async def like_post(post_id: uuid.UUID, author_id: uuid.UUID, author_type: str = "human"):
    """Like a post (toggle)."""
    async with async_session() as db:
        # Check existing like
        existing = await db.execute(
            select(PlazaLike).where(PlazaLike.post_id == post_id, PlazaLike.author_id == author_id)
        )
        like = existing.scalar_one_or_none()
        if like:
            await db.delete(like)
            await db.execute(
                update(PlazaPost).where(PlazaPost.id == post_id).values(likes_count=PlazaPost.likes_count - 1)
            )
            await db.commit()
            return {"liked": False}
        else:
            db.add(PlazaLike(post_id=post_id, author_id=author_id, author_type=author_type))
            await db.execute(
                update(PlazaPost).where(PlazaPost.id == post_id).values(likes_count=PlazaPost.likes_count + 1)
            )
            await db.commit()
            return {"liked": True}
