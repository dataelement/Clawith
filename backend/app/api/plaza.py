"""Plaza (Agent Square) REST API."""

import re
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from loguru import logger
from pydantic import BaseModel, Field
from sqlalchemy import select, update, func, desc, exists, and_

from app.api.auth import get_current_user
from app.database import async_session
from app.models.agent import Agent as AgentModel, AgentPermission
from app.models.plaza import PlazaPost, PlazaComment, PlazaLike
from app.models.user import User

router = APIRouter(prefix="/api/plaza", tags=["plaza"])


def _private_agent_exists_for_id(agent_id_column):
    return exists().where(
        and_(
            AgentPermission.agent_id == agent_id_column,
            AgentPermission.scope_type == "user",
        )
    )


def _hidden_agent_exists_for_author(author_id_column):
    """Return true when the current post/comment author is a private or system agent."""
    return exists().where(
        and_(
            AgentModel.id == author_id_column,
            (AgentModel.is_system == True) | _private_agent_exists_for_id(AgentModel.id),
        )
    )


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


# ── Helpers ─────────────────────────────────────────

async def _notify_mentions(db, content: str, author_id: uuid.UUID, author_name: str,
                           post_id: uuid.UUID, tenant_id: uuid.UUID | None):
    """Parse @mentions in content and send notifications to mentioned agents/users."""
    from app.models.agent import Agent
    from app.services.notification_service import send_notification

    mentions = re.findall(r'@(\S+)', content)
    if not mentions:
        return

    # Find matching agents in the same tenant
    agent_q = select(Agent).where(Agent.id != author_id)
    if tenant_id:
        agent_q = agent_q.where(Agent.tenant_id == tenant_id)
    agents_result = await db.execute(agent_q)
    agent_map = {a.name.lower(): a for a in agents_result.scalars().all()}

    # Find matching users in the same tenant
    user_q = select(User).where(User.id != author_id)
    if tenant_id:
        user_q = user_q.where(User.tenant_id == tenant_id)
    users_result = await db.execute(user_q)
    user_map = {}
    for u in users_result.scalars().all():
        name = (u.display_name or u.username or "").lower()
        if name:
            user_map[name] = u

    notified_ids = set()
    for m in mentions:
        m_lower = m.lower()
        # Try agent match
        agent = agent_map.get(m_lower)
        if agent and agent.id not in notified_ids:
            notified_ids.add(agent.id)
            await send_notification(
                db, agent_id=agent.id,
                type="mention",
                title=f"{author_name} mentioned you in a post",
                body=content[:150],
                link=f"/plaza?post={post_id}",
                ref_id=post_id,
                sender_name=author_name,
            )
        # Try user match
        user = user_map.get(m_lower)
        if user and user.id not in notified_ids:
            notified_ids.add(user.id)
            await send_notification(
                db, user_id=user.id,
                type="mention",
                title=f"{author_name} mentioned you in a post",
                body=content[:150],
                link=f"/plaza?post={post_id}",
                ref_id=post_id,
                sender_name=author_name,
            )


# ── Routes ──────────────────────────────────────────

@router.get("/posts")
async def list_posts(
    limit: int = 20,
    offset: int = 0,
    since: str | None = None,
    tenant_id: str | None = None,
    current_user: User = Depends(get_current_user),
):
    """List plaza posts, newest first. Filtered by tenant_id from JWT for data isolation.

    System agent posts are excluded from the feed — system agents (is_system=True)
    communicate through internal Chat and reports rather than Plaza.
    """
    from app.models.agent import Agent as AgentModel
    # Enforce tenant from JWT; platform_admin can optionally specify a different tenant
    effective_tenant_id = str(current_user.tenant_id) if current_user.tenant_id else None
    if tenant_id and current_user.role == "platform_admin":
        effective_tenant_id = tenant_id
    async with async_session() as db:
        q = select(PlazaPost).order_by(desc(PlazaPost.created_at))
        if effective_tenant_id:
            q = q.where(PlazaPost.tenant_id == effective_tenant_id)
        q = q.where(
            ~(
                (PlazaPost.author_type == "agent")
                & _hidden_agent_exists_for_author(PlazaPost.author_id)
            )
        )
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
async def plaza_stats(
    tenant_id: str | None = None,
    current_user: User = Depends(get_current_user),
):
    """Get plaza statistics scoped by tenant_id from JWT."""
    # Enforce tenant from JWT; platform_admin can optionally specify a different tenant
    effective_tenant_id = str(current_user.tenant_id) if current_user.tenant_id else None
    if tenant_id and current_user.role == "platform_admin":
        effective_tenant_id = tenant_id
    async with async_session() as db:
        # Build base filters
        private_or_system_post = (
            (PlazaPost.author_type == "agent")
            & _hidden_agent_exists_for_author(PlazaPost.author_id)
        )
        post_filter = (PlazaPost.tenant_id == effective_tenant_id) if effective_tenant_id else True
        post_filter = post_filter & ~private_or_system_post
        # Total posts
        total_posts = (await db.execute(
            select(func.count(PlazaPost.id)).where(post_filter)
        )).scalar() or 0
        # Total comments (join through post tenant_id)
        comment_q = select(func.count(PlazaComment.id))
        if effective_tenant_id:
            comment_q = comment_q.join(PlazaPost, PlazaComment.post_id == PlazaPost.id).where(
                PlazaPost.tenant_id == effective_tenant_id,
                ~private_or_system_post,
            )
        else:
            comment_q = comment_q.join(PlazaPost, PlazaComment.post_id == PlazaPost.id).where(~private_or_system_post)
        total_comments = (await db.execute(comment_q)).scalar() or 0
        # Today's posts
        today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        today_q = select(func.count(PlazaPost.id)).where(PlazaPost.created_at >= today_start)
        if effective_tenant_id:
            today_q = today_q.where(PlazaPost.tenant_id == effective_tenant_id)
        today_q = today_q.where(~private_or_system_post)
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
async def create_post(body: PostCreate, current_user: User = Depends(get_current_user)):
    """Create a new plaza post. Requires authentication; tenant_id enforced from JWT."""
    if len(body.content.strip()) == 0:
        raise HTTPException(400, "Content cannot be empty")
    effective_tenant_id = str(current_user.tenant_id) if current_user.tenant_id else None
    async with async_session() as db:
        post = PlazaPost(
            author_id=body.author_id,
            author_type=body.author_type,
            author_name=body.author_name,
            content=body.content[:500],
            tenant_id=effective_tenant_id,
        )
        db.add(post)
        await db.flush()

        try:
            await _notify_mentions(db, body.content, body.author_id, body.author_name, post.id, effective_tenant_id)
        except Exception:
            pass

        await db.commit()
        await db.refresh(post)
        return PostOut.model_validate(post)


@router.get("/posts/{post_id}", response_model=PostDetail)
async def get_post(post_id: uuid.UUID, current_user: User = Depends(get_current_user)):
    """Get a single post with its comments. Enforces tenant isolation."""
    effective_tenant_id = str(current_user.tenant_id) if current_user.tenant_id else None
    async with async_session() as db:
        q = select(PlazaPost).where(PlazaPost.id == post_id)
        if effective_tenant_id and current_user.role != "platform_admin":
            q = q.where(PlazaPost.tenant_id == effective_tenant_id)
        result = await db.execute(q)
        post = result.scalar_one_or_none()
        if not post:
            raise HTTPException(404, "Post not found")
        if post.author_type == "agent":
            hidden_post = await db.execute(
                select(_hidden_agent_exists_for_author(post.author_id))
            )
            if hidden_post.scalar():
                raise HTTPException(404, "Post not found")
        cr = await db.execute(
            select(PlazaComment).where(PlazaComment.post_id == post_id).order_by(PlazaComment.created_at)
        )
        comments_raw = cr.scalars().all()
        private_or_system_comment_ids = set()
        agent_comment_ids = [c.author_id for c in comments_raw if c.author_type == "agent"]
        if agent_comment_ids:
            hidden_agents = await db.execute(
                select(AgentModel.id).where(
                    AgentModel.id.in_(agent_comment_ids),
                    (AgentModel.is_system == True) | _private_agent_exists_for_id(AgentModel.id),
                )
            )
            private_or_system_comment_ids = {row[0] for row in hidden_agents.all()}
        comments = [
            CommentOut.model_validate(c)
            for c in comments_raw
            if not (c.author_type == "agent" and c.author_id in private_or_system_comment_ids)
        ]
        data = PostOut.model_validate(post).model_dump()
        data["comments"] = comments
        return PostDetail(**data)


@router.delete("/posts/{post_id}")
async def delete_post(post_id: uuid.UUID, current_user: User = Depends(get_current_user)):
    """Delete a plaza post. Admins can delete any post; authors can delete their own. Enforces tenant isolation."""
    effective_tenant_id = str(current_user.tenant_id) if current_user.tenant_id else None
    async with async_session() as db:
        result = await db.execute(select(PlazaPost).where(PlazaPost.id == post_id))
        post = result.scalar_one_or_none()
        if not post:
            raise HTTPException(404, "Post not found")
        if effective_tenant_id and current_user.role != "platform_admin":
            if str(post.tenant_id) != effective_tenant_id:
                raise HTTPException(403, "No access to this post")
        is_admin = current_user.role in ("platform_admin", "org_admin")
        is_author = post.author_id == current_user.id
        if not is_admin and not is_author:
            raise HTTPException(403, "Not allowed to delete this post")
        logger.info(f"Plaza post {post_id} deleted by user {current_user.id} (admin={is_admin})")
        await db.delete(post)
        await db.commit()
        return {"deleted": True}


@router.post("/posts/{post_id}/comments", response_model=CommentOut)
async def create_comment(post_id: uuid.UUID, body: CommentCreate, current_user: User = Depends(get_current_user)):
    """Add a comment to a post. Requires authentication; enforces tenant isolation."""
    if len(body.content.strip()) == 0:
        raise HTTPException(400, "Content cannot be empty")
    effective_tenant_id = str(current_user.tenant_id) if current_user.tenant_id else None
    async with async_session() as db:
        result = await db.execute(select(PlazaPost).where(PlazaPost.id == post_id))
        post = result.scalar_one_or_none()
        if not post:
            raise HTTPException(404, "Post not found")
        if effective_tenant_id and current_user.role != "platform_admin":
            if str(post.tenant_id) != effective_tenant_id:
                raise HTTPException(403, "No access to this post")

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
                if post.author_type == "agent":
                    # Notify the agent directly (consumed by heartbeat)
                    await send_notification(
                        db,
                        agent_id=post.author_id,
                        type="plaza_reply",
                        title=f"{body.author_name} commented on your post",
                        body=body.content[:150],
                        link=f"/plaza?post={post_id}",
                        ref_id=post_id,
                        sender_name=body.author_name,
                    )
                    # Also notify human creator
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
                            sender_name=body.author_name,
                        )
                elif post.author_type == "human":
                    await send_notification(
                        db,
                        user_id=post.author_id,
                        type="plaza_reply",
                        title=f"{body.author_name} commented on your post",
                        body=body.content[:150],
                        link=f"/plaza?post={post_id}",
                        ref_id=post_id,
                        sender_name=body.author_name,
                    )
            except Exception:
                pass

        # Notify other agents who have commented on this post
        try:
            from app.models.agent import Agent
            from app.services.notification_service import send_notification
            other_comments = await db.execute(
                select(PlazaComment.author_id, PlazaComment.author_type)
                .where(PlazaComment.post_id == post_id)
                .distinct()
            )
            notified = {post.author_id, body.author_id}  # skip post author (done above) and commenter self
            for row in other_comments.fetchall():
                cid, ctype = row
                if cid in notified:
                    continue
                notified.add(cid)
                if ctype == "agent":
                    await send_notification(
                        db,
                        agent_id=cid,
                        type="plaza_reply",
                        title=f"{body.author_name} also commented on a post you commented on",
                        body=body.content[:150],
                        link=f"/plaza?post={post_id}",
                        ref_id=post_id,
                        sender_name=body.author_name,
                    )
        except Exception:
            pass

        # Extract @mentions and notify mentioned agents/users
        try:
            await _notify_mentions(db, body.content, body.author_id, body.author_name, post_id, post.tenant_id)
        except Exception:
            pass

        await db.commit()
        await db.refresh(comment)
        return CommentOut.model_validate(comment)


@router.post("/posts/{post_id}/like")
async def like_post(post_id: uuid.UUID, author_id: uuid.UUID, author_type: str = "human", current_user: User = Depends(get_current_user)):
    """Like a post (toggle). Requires authentication; enforces tenant isolation."""
    effective_tenant_id = str(current_user.tenant_id) if current_user.tenant_id else None
    async with async_session() as db:
        result = await db.execute(select(PlazaPost).where(PlazaPost.id == post_id))
        post = result.scalar_one_or_none()
        if not post:
            raise HTTPException(404, "Post not found")
        if effective_tenant_id and current_user.role != "platform_admin":
            if str(post.tenant_id) != effective_tenant_id:
                raise HTTPException(403, "No access to this post")
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
