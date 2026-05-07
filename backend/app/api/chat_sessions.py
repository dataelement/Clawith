"""Chat session management API endpoints."""

import uuid
from datetime import datetime, timezone as tz
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import cast, select, func, or_, and_, String
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.permissions import check_agent_access
from app.core.security import get_current_user
from app.database import get_db
from app.models.audit import ChatMessage
from app.models.chat_session import ChatSession
from app.models.agent import Agent
from app.models.project import Project, ProjectAgent, ProjectChatVisibility
from app.models.user import User

router = APIRouter(prefix="/api/agents", tags=["chat-sessions"])


def _can_view_all_agent_chat_sessions(user: User, agent: Agent) -> bool:
    """Admins and the agent creator may list/view/delete other users' chat sessions."""
    return (
        user.role in ("platform_admin", "org_admin", "agent_admin")
        or str(agent.creator_id) == str(user.id)
    )


class SessionOut(BaseModel):
    id: str
    agent_id: str
    user_id: str
    username: Optional[str] = None      # display_name ?? username
    source_channel: str = "web"         # web / feishu / discord / slack / agent
    title: str
    created_at: str
    last_message_at: Optional[str] = None
    message_count: int = 0
    unread_count: int = 0
    is_primary: bool = False
    # Agent-to-agent session fields
    peer_agent_id: Optional[str] = None
    peer_agent_name: Optional[str] = None
    participant_type: str = "user"       # 'user' | 'agent'
    # Group chat session fields
    is_group: bool = False
    group_name: Optional[str] = None
    # Project binding — present only when this session was created inside a project
    project_id: Optional[str] = None
    project_name: Optional[str] = None
    # Whether the requesting user owns this session. Sidebar / chat input use this
    # to decide whether to enable composing. Defaults to True (caller's own list).
    owned_by_me: bool = True

    class Config:
        from_attributes = True


class CreateSessionIn(BaseModel):
    title: Optional[str] = None
    # Project binding — if set, the session inherits the project's BRIEF and workspace
    project_id: Optional[uuid.UUID] = None


class PatchSessionIn(BaseModel):
    title: str


@router.get("/{agent_id}/sessions")
async def list_sessions(
    agent_id: uuid.UUID,
    scope: str = Query("mine", description="'mine' or 'all'"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List chat sessions for an agent. scope=all for org/platform admins and agent_admin."""
    # Verify agent exists
    agent_result = await db.execute(select(Agent).where(Agent.id == agent_id))
    agent = agent_result.scalar_one_or_none()
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    await check_agent_access(db, current_user, agent_id)

    if scope == "all":
        if not _can_view_all_agent_chat_sessions(current_user, agent):
            raise HTTPException(status_code=403, detail="Not authorized to view all sessions")

        # Fetch all sessions (including agent-to-agent where this agent is peer)
        result = await db.execute(
            select(ChatSession)
            .where(
                (ChatSession.agent_id == agent_id)
                | ((ChatSession.peer_agent_id == agent_id) & (ChatSession.source_channel == "agent"))
            )
            # Order by activity time, treating freshly created (no messages yet)
            # sessions as having "activity = created_at" so they appear at the top
            # right after creation rather than getting pushed below older convos
            # with stale messages.
            .order_by(func.coalesce(ChatSession.last_message_at, ChatSession.created_at).desc())
        )
        sessions = result.scalars().all()
        out = []

        # --- BULK FETCH: message counts, user names, agent names in 3 queries total ---
        session_ids = [str(s.id) for s in sessions]
        session_uuid_ids = [s.id for s in sessions]

        message_counts: dict[str, int] = {}
        unread_counts: dict[str, int] = {}
        if session_ids:
            count_res = await db.execute(
                select(ChatMessage.conversation_id, func.count(ChatMessage.id))
                .where(ChatMessage.conversation_id.in_(session_ids))
                .group_by(ChatMessage.conversation_id)
            )
            for row in count_res.all():
                message_counts[row[0]] = row[1]

            unread_res = await db.execute(
                select(ChatSession.id, func.count(ChatMessage.id))
                .join(ChatMessage, ChatMessage.conversation_id == cast(ChatSession.id, String))
                .where(
                    ChatSession.id.in_(session_uuid_ids),
                    ChatSession.user_id == current_user.id,
                    ChatSession.source_channel.notin_(["agent", "trigger"]),
                    ChatSession.is_group == False,
                    ChatMessage.role.in_(["assistant", "system", "tool_call"]),
                    ChatMessage.created_at > func.coalesce(
                        ChatSession.last_read_at_by_user,
                        datetime(1970, 1, 1, tzinfo=tz.utc),
                    ),
                )
                .group_by(ChatSession.id)
            )
            for row in unread_res.all():
                unread_counts[str(row[0])] = int(row[1] or 0)

        # Collect IDs to resolve in bulk
        from app.models.user import Identity
        user_ids = list({s.user_id for s in sessions
                         if not s.is_group and s.source_channel != "agent" and s.user_id})
        user_names: dict[str, str] = {}
        if user_ids:
            user_r = await db.execute(
                select(User.id, func.coalesce(User.display_name, Identity.username))
                .join(Identity, User.identity_id == Identity.id)
                .where(User.id.in_(user_ids))
            )
            for row in user_r.all():
                user_names[str(row[0])] = row[1] or "Unknown"

        agent_ids_to_fetch: set = set()
        for s in sessions:
            if s.source_channel == "agent" and s.peer_agent_id:
                agent_ids_to_fetch.add(s.agent_id)
                agent_ids_to_fetch.add(s.peer_agent_id)
        agent_names: dict[str, str] = {}
        if agent_ids_to_fetch:
            agent_r = await db.execute(
                select(Agent.id, Agent.name).where(Agent.id.in_(list(agent_ids_to_fetch)))
            )
            for row in agent_r.all():
                agent_names[str(row[0])] = row[1] or "Agent"

        project_ids_to_fetch = list({s.project_id for s in sessions if s.project_id})
        project_names: dict[str, str] = {}
        if project_ids_to_fetch:
            pr = await db.execute(
                select(Project.id, Project.name).where(Project.id.in_(project_ids_to_fetch))
            )
            for row in pr.all():
                project_names[str(row[0])] = row[1]

        for session in sessions:
            count = message_counts.get(str(session.id), 0)
            # Same rationale as scope=mine: empty project sessions are legitimate.
            if count == 0 and session.project_id is None:
                continue

            display = None
            peer_agent_id = None
            peer_agent_name = None
            participant_type = "user"

            if session.source_channel == "agent" and session.peer_agent_id:
                participant_type = "agent"
                peer_agent_id = str(session.peer_agent_id)
                a1_name = agent_names.get(str(session.agent_id), "Agent")
                a2_name = agent_names.get(str(session.peer_agent_id), "Agent")
                peer_agent_name = a2_name
                display = f"Agent {a1_name} - {a2_name}"
            elif session.is_group:
                display = session.group_name or session.title or "Group Chat"
            else:
                display = user_names.get(str(session.user_id), "Unknown")

            out.append(SessionOut(
                id=str(session.id),
                agent_id=str(session.agent_id),
                user_id=str(session.user_id),
                username=display,
                source_channel=session.source_channel,
                title=session.title,
                created_at=session.created_at.isoformat(),
                last_message_at=session.last_message_at.isoformat() if session.last_message_at else None,
                message_count=count,
                unread_count=unread_counts.get(str(session.id), 0),
                is_primary=bool(session.is_primary),
                peer_agent_id=peer_agent_id,
                peer_agent_name=peer_agent_name,
                participant_type="group" if session.is_group else participant_type,
                is_group=session.is_group,
                group_name=session.group_name,
                project_id=str(session.project_id) if session.project_id else None,
                project_name=project_names.get(str(session.project_id)) if session.project_id else None,
                owned_by_me=str(session.user_id) == str(current_user.id),
            ))
        return out

    else:  # scope == "mine"
        result = await db.execute(
            select(ChatSession)
            .where(
                ChatSession.agent_id == agent_id,
                ChatSession.user_id == current_user.id,
                ChatSession.is_group == False,  # Group sessions are not "mine"
                # Exclude agent-to-agent and reflection sessions, BUT keep trigger
                # sessions that are bound to a project — those are user-configured
                # scheduled tasks running inside a project workspace and should
                # remain reachable from the sidebar (otherwise users see them in
                # ProjectDetail.Chats but cannot open them).
                or_(
                    ChatSession.source_channel.notin_(["agent", "trigger"]),
                    and_(
                        ChatSession.source_channel == "trigger",
                        ChatSession.project_id.isnot(None),
                    ),
                ),
            )
            # Order by activity time, treating freshly created (no messages yet)
            # sessions as having "activity = created_at" so they appear at the top
            # right after creation rather than getting pushed below older convos
            # with stale messages.
            .order_by(func.coalesce(ChatSession.last_message_at, ChatSession.created_at).desc())
        )
        sessions = result.scalars().all()
        out = []

        # --- BULK FETCH: count total messages and unread messages in two compact queries ---
        session_ids = [str(s.id) for s in sessions]
        session_uuid_ids = [s.id for s in sessions]

        total_counts: dict[str, int] = {}
        unread_counts: dict[str, int] = {}
        if session_ids:
            counts_res = await db.execute(
                select(
                    ChatMessage.conversation_id,
                    func.count(ChatMessage.id)
                ).where(
                    ChatMessage.conversation_id.in_(session_ids),
                    ChatMessage.agent_id == agent_id
                ).group_by(ChatMessage.conversation_id)
            )
            for row in counts_res.all():
                total_counts[row[0]] = int(row[1] or 0)

            unread_res = await db.execute(
                select(ChatSession.id, func.count(ChatMessage.id))
                .join(ChatMessage, ChatMessage.conversation_id == cast(ChatSession.id, String))
                .where(
                    ChatSession.id.in_(session_uuid_ids),
                    ChatMessage.role.in_(["assistant", "system", "tool_call"]),
                    ChatMessage.created_at > func.coalesce(
                        ChatSession.last_read_at_by_user,
                        datetime(1970, 1, 1, tzinfo=tz.utc),
                    ),
                )
                .group_by(ChatSession.id)
            )
            for row in unread_res.all():
                unread_counts[str(row[0])] = int(row[1] or 0)

        project_ids_to_fetch = list({s.project_id for s in sessions if s.project_id})
        project_names: dict[str, str] = {}
        if project_ids_to_fetch:
            pr = await db.execute(
                select(Project.id, Project.name).where(Project.id.in_(project_ids_to_fetch))
            )
            for row in pr.all():
                project_names[str(row[0])] = row[1]

        for session in sessions:
            # Hide truly empty / orphan sessions. Onboarding sessions have zero
            # user messages (the agent greets first) but do have assistant
            # turns, so count ALL messages here — not just user ones.
            count = total_counts.get(str(session.id), 0)
            if count == 0:
                continue
            out.append(SessionOut(
                id=str(session.id),
                agent_id=str(session.agent_id),
                user_id=str(session.user_id),
                source_channel=session.source_channel,
                title=session.title,
                created_at=session.created_at.isoformat(),
                last_message_at=session.last_message_at.isoformat() if session.last_message_at else None,
                message_count=count,
                unread_count=unread_counts.get(str(session.id), 0),
                is_primary=bool(session.is_primary),
                project_id=str(session.project_id) if session.project_id else None,
                project_name=project_names.get(str(session.project_id)) if session.project_id else None,
                owned_by_me=True,  # scope=mine guarantees user_id == current_user.id
            ))
        return out


@router.post("/{agent_id}/sessions", status_code=201)
async def create_session(
    agent_id: uuid.UUID,
    body: CreateSessionIn = CreateSessionIn(),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Create a new chat session for the current user.

    If `project_id` is given, the session is bound to that project and the
    agent must already be in `project_agents`; the project must not be archived.
    """
    await check_agent_access(db, current_user, agent_id)

    project_id: uuid.UUID | None = None
    project_obj = None
    if body.project_id is not None:
        # Lazy imports to avoid a circular dependency between chat_sessions
        # and the project feature (api/projects imports chat_session models).
        from app.models.project import Project, ProjectAgent, ProjectScopeType
        project_obj = (await db.execute(select(Project).where(Project.id == body.project_id))).scalar_one_or_none()
        if not project_obj:
            raise HTTPException(status_code=404, detail="Project not found")
        # Same-tenant fence
        if current_user.role != "platform_admin":
            if project_obj.scope_type != ProjectScopeType.TENANT.value or str(project_obj.scope_id) != str(current_user.tenant_id):
                raise HTTPException(status_code=403, detail="No access to this project")
        if project_obj.archived_at is not None:
            raise HTTPException(status_code=409, detail="Project is archived")
        # Agent must be assigned to the project
        in_project = (await db.execute(
            select(ProjectAgent).where(
                ProjectAgent.project_id == body.project_id,
                ProjectAgent.agent_id == agent_id,
            )
        )).scalar_one_or_none()
        if not in_project:
            raise HTTPException(status_code=403, detail="Agent is not assigned to this project")
        project_id = body.project_id

    now = datetime.now(tz.utc)
    new_id = uuid.uuid4()
    # Title: project sessions prefix with "{project_name}：" so the agent's
    # session sidebar and cross-project activity views can tell at a glance
    # which project a chat belongs to.
    base_title = body.title or now.strftime("%m-%d %H:%M")
    if project_obj is not None:
        title = f"{project_obj.name}：{base_title}"
    else:
        title = body.title or f"Session {now.strftime('%m-%d %H:%M')}"
    session = ChatSession(
        id=new_id,
        agent_id=agent_id,
        user_id=current_user.id,
        title=title,
        source_channel="web",
        is_primary=False,
        created_at=now,
        project_id=project_id,
    )
    db.add(session)
    await db.commit()
    await db.refresh(session)
    return SessionOut(
        id=str(session.id),
        agent_id=str(session.agent_id),
        user_id=str(session.user_id),
        source_channel=session.source_channel,
        title=session.title,
        created_at=session.created_at.isoformat(),
        last_message_at=None,
        message_count=0,
        unread_count=0,
        is_primary=False,
        participant_type="user",
        is_group=False,
        project_id=str(session.project_id) if session.project_id else None,
    )


@router.patch("/{agent_id}/sessions/{session_id}")
async def rename_session(
    agent_id: uuid.UUID,
    session_id: uuid.UUID,
    body: PatchSessionIn,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Rename a session. Owner, agent creator, or admin may rename others' sessions."""
    agent, _ = await check_agent_access(db, current_user, agent_id)
    result = await db.execute(
        select(ChatSession).where(ChatSession.id == session_id, ChatSession.agent_id == agent_id)
    )
    session = result.scalar_one_or_none()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    if str(session.user_id) != str(current_user.id) and not _can_view_all_agent_chat_sessions(current_user, agent):
        raise HTTPException(status_code=403, detail="Not authorized")

    session.title = body.title
    await db.commit()
    return {"id": str(session.id), "title": session.title}


@router.delete("/{agent_id}/sessions/{session_id}", status_code=204)
async def delete_session(
    agent_id: uuid.UUID,
    session_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Delete a chat session and its messages. Owner, agent creator, or admin may delete others' sessions."""
    agent, _ = await check_agent_access(db, current_user, agent_id)
    result = await db.execute(
        select(ChatSession).where(ChatSession.id == session_id, ChatSession.agent_id == agent_id)
    )
    session = result.scalar_one_or_none()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    if str(session.user_id) != str(current_user.id) and not _can_view_all_agent_chat_sessions(current_user, agent):
        raise HTTPException(status_code=403, detail="Not authorized")

    # Delete associated messages first
    from sqlalchemy import delete as sql_delete
    await db.execute(sql_delete(ChatMessage).where(ChatMessage.conversation_id == str(session_id)))
    await db.delete(session)
    await db.commit()
    return None


@router.get("/{agent_id}/sessions/{session_id}/messages")
async def get_session_messages(
    agent_id: uuid.UUID,
    session_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get chat messages for a specific session."""
    agent, _ = await check_agent_access(db, current_user, agent_id)
    # Allow looking up sessions where agent_id OR peer_agent_id matches
    result = await db.execute(
        select(ChatSession).where(
            ChatSession.id == session_id,
            (ChatSession.agent_id == agent_id) | (ChatSession.peer_agent_id == agent_id),
        )
    )
    session = result.scalar_one_or_none()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    # Permission: session owner, agent creator, or admin.
    if str(session.user_id) != str(current_user.id) and not _can_view_all_agent_chat_sessions(current_user, agent):
        raise HTTPException(status_code=403, detail="Not authorized to view this session")

    # Query messages by conversation_id only (agent-to-agent uses session_agent_id)
    # Query the latest 500 messages (subquery in DESC, then reverse for display order)
    from sqlalchemy import desc
    latest_subq = (
        select(ChatMessage.id)
        .where(ChatMessage.conversation_id == str(session_id))
        .order_by(desc(ChatMessage.created_at))
        .limit(500)
        .subquery()
    )
    msgs_result = await db.execute(
        select(ChatMessage)
        .where(ChatMessage.id.in_(select(latest_subq.c.id)))
        .order_by(ChatMessage.created_at.asc())
    )
    messages = msgs_result.scalars().all()

    # Reading your own first-party/channel session should clear its unread state.
    if str(session.user_id) == str(current_user.id) and not session.is_group and session.source_channel not in ("agent", "trigger"):
        session.last_read_at_by_user = datetime.now(tz.utc)
        await db.commit()

    # Resolve sender names for agent sessions
    sender_cache: dict = {}
    if session.source_channel == "agent":
        from app.models.participant import Participant
        for m in messages:
            if m.participant_id and str(m.participant_id) not in sender_cache:
                p_r = await db.execute(select(Participant.display_name).where(Participant.id == m.participant_id))
                sender_cache[str(m.participant_id)] = p_r.scalar_one_or_none() or "Unknown"

    out = []
    for m in messages:
        sender_name = sender_cache.get(str(m.participant_id)) if m.participant_id else None

        if m.role == "tool_call":
            import json
            entry: dict = {"role": m.role, "content": m.content, "created_at": m.created_at.isoformat() if m.created_at else None}
            try:
                data = json.loads(m.content)
                entry["content"] = ""
                entry["toolName"] = data.get("name", "")
                entry["toolArgs"] = data.get("args")
                entry["toolStatus"] = data.get("status", "done")
                entry["toolResult"] = data.get("result", "")
                entry["toolThinking"] = data.get("reasoning_content", "")
            except Exception:
                pass
            if sender_name:
                entry["sender_name"] = sender_name
            out.append(entry)
            continue

        # For agent sessions, parse inline tool_code blocks from assistant messages
        if session.source_channel == "agent" and m.role == "assistant" and "```tool_code" in (m.content or ""):
            parts = _split_inline_tools(m.content)
            for part in parts:
                if sender_name:
                    part["sender_name"] = sender_name
                if m.participant_id:
                    part["participant_id"] = str(m.participant_id)
                out.append(part)
        else:
            entry = {"role": m.role, "content": m.content, "created_at": m.created_at.isoformat() if m.created_at else None}
            if hasattr(m, 'thinking') and m.thinking:
                entry["thinking"] = m.thinking
            if sender_name:
                entry["sender_name"] = sender_name
            if m.participant_id:
                entry["participant_id"] = str(m.participant_id)
            out.append(entry)

    return out


import re

def _split_inline_tools(content: str) -> list[dict]:
    """Parse assistant content containing inline ```tool_code blocks.

    Splits into alternating text segments and tool_call entries.
    Format: ```tool_code\ntool_name\n``` ```json\n{args}\n```
    """
    # Pattern: ```tool_code\n<name>\n``` optionally followed by ```json\n<args>\n```
    pattern = re.compile(
        r'```tool_code\s*\n\s*(\w+)\s*\n```'        # tool name
        r'(?:\s*```json\s*\n(.*?)\n```)?',            # optional JSON args
        re.DOTALL
    )

    parts: list[dict] = []
    last_end = 0

    for match in pattern.finditer(content):
        # Text before this tool call
        text_before = content[last_end:match.start()].strip()
        if text_before:
            parts.append({"role": "assistant", "content": text_before})

        tool_name = match.group(1)
        args_str = match.group(2)
        tool_args = None
        if args_str:
            try:
                import json
                tool_args = json.loads(args_str.strip())
            except Exception:
                tool_args = {"raw": args_str.strip()}

        parts.append({
            "role": "tool_call",
            "content": "",
            "toolName": tool_name,
            "toolArgs": tool_args,
            "toolStatus": "done",
            "toolResult": "",
        })
        last_end = match.end()

    # Trailing text after last tool
    trailing = content[last_end:].strip()
    if trailing:
        parts.append({"role": "assistant", "content": trailing})

    # If no matches found, return the whole content as-is
    if not parts:
        parts.append({"role": "assistant", "content": content})

    return parts


# ─── Single session detail endpoint ──────────────────────────────────────
#
# A separate router because /api/agents/{agent_id}/sessions/... is awkward
# when the caller knows only the session_id (e.g. deep-linking from a
# Project chat list, or fetching metadata for the chat header pill).

chat_session_router = APIRouter(prefix="/api/chat-sessions", tags=["chat-sessions"])


async def _can_view_session(
    session: ChatSession,
    current_user: User,
    db: AsyncSession,
) -> bool:
    """Whether current_user may read this session.

    Enforces compass red line 1 (tenant isolation) plus project visibility:
    - Agent must be in the same tenant as the user.
    - User must be the session owner, an admin, or a same-tenant member of a
      project where chat_visibility=shared (M5/H1 in the Project plan).
    """
    agent_row = (await db.execute(
        select(Agent.tenant_id).where(Agent.id == session.agent_id)
    )).first()
    if not agent_row or agent_row[0] != current_user.tenant_id:
        return False

    if str(session.user_id) == str(current_user.id):
        return True

    if current_user.role in ("platform_admin", "org_admin", "agent_admin"):
        return True

    if session.project_id is not None:
        project = (await db.execute(
            select(Project).where(Project.id == session.project_id)
        )).scalar_one_or_none()
        if (
            project
            and project.chat_visibility == ProjectChatVisibility.SHARED.value
            and project.scope_type == "tenant"
            and str(project.scope_id) == str(current_user.tenant_id)
        ):
            return True

    return False


@chat_session_router.get("/{session_id}", response_model=SessionOut)
async def get_chat_session(
    session_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Return single session metadata for chat header / deep-link rendering."""
    session = (await db.execute(
        select(ChatSession).where(ChatSession.id == session_id)
    )).scalar_one_or_none()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    if not await _can_view_session(session, current_user, db):
        raise HTTPException(status_code=403, detail="Not authorized to view this session")

    project_name: Optional[str] = None
    if session.project_id is not None:
        project_name = (await db.execute(
            select(Project.name).where(Project.id == session.project_id)
        )).scalar_one_or_none()

    msg_count = (await db.execute(
        select(func.count(ChatMessage.id)).where(ChatMessage.conversation_id == str(session_id))
    )).scalar() or 0

    return SessionOut(
        id=str(session.id),
        agent_id=str(session.agent_id),
        user_id=str(session.user_id),
        source_channel=session.source_channel,
        title=session.title,
        created_at=session.created_at.isoformat(),
        last_message_at=session.last_message_at.isoformat() if session.last_message_at else None,
        message_count=int(msg_count),
        is_group=session.is_group,
        group_name=session.group_name,
        project_id=str(session.project_id) if session.project_id else None,
        project_name=project_name,
        owned_by_me=str(session.user_id) == str(current_user.id),
    )
