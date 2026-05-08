"""Project feature API — organises multiple agents around one shared goal.

Routes live under `/api/projects`. A small reverse router also exposes
`/api/agents/{agent_id}/projects` for the AgentDetail "Projects" tab.

MVP permission model:
- View / edit / add-agent / upload-file / edit-brief: any user in the project's
  tenant scope (scope_type='tenant' and scope_id == user.tenant_id)
- Archive: creator + tenant admin only
- Cross-tenant access: always denied (except platform_admin, by convention)
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone as _tz
from typing import Literal, Optional

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    Response,
    UploadFile,
    status,
)
from fastapi.responses import StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel
from sqlalchemy import and_, delete as _sql_delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import get_current_user
from app.database import get_db
from app.models.agent import Agent
from app.models.audit import ChatMessage
from app.models.chat_session import ChatSession
from app.models.project import (
    Project,
    ProjectAgent,
    ProjectChatVisibility,
    ProjectFile,
    ProjectFileCreatedByType,
    ProjectScopeType,
)
from app.models.project_task import (
    ProjectTask,
    ProjectTaskCreatedByType,
    ProjectTaskFile,
    ProjectTaskStatus,
)
from app.models.trigger import AgentTrigger
from app.models.user import User
from app.schemas.project import (
    ProjectAgentAdd,
    ProjectAgentOut,
    ProjectAgentSummary,
    ProjectBriefOut,
    ProjectBriefUpdate,
    ProjectChatSessionOut,
    ProjectCreate,
    ProjectFileConflict,
    ProjectFileContent,
    ProjectFileMove,
    ProjectFileOut,
    ProjectFileWrite,
    ProjectListItem,
    ProjectOut,
    ProjectScheduledTaskCreate,
    ProjectScheduledTaskOut,
    ProjectScheduledTaskUpdate,
    ProjectUpdate,
    ScheduledTaskFrequency,
)
from app.schemas.project_task import (
    ProjectTaskCreate,
    ProjectTaskDetail,
    ProjectTaskFileLinkIn,
    ProjectTaskFileLinkRef,
    ProjectTaskOut,
    ProjectTaskUpdate,
)
from app.services.project_workspace import (
    BRIEF_FILENAME,
    ConflictMode,
    project_workspace_service,
)


router = APIRouter(prefix="/api/projects", tags=["projects"])
agent_projects_router = APIRouter(prefix="/api/agents", tags=["projects"])

logger = logging.getLogger(__name__)


# ── permission helpers ──────────────────────────────────────────────────

def _is_tenant_admin(user: User) -> bool:
    return user.role in ("platform_admin", "org_admin")


def _can_view_project(user: User, project: Project) -> bool:
    if user.role == "platform_admin":
        return True
    # MVP: scope_type is always 'tenant' in storage, and scope_id == tenant_id
    if project.scope_type == ProjectScopeType.TENANT.value:
        return str(project.scope_id) == str(user.tenant_id)
    # DEPARTMENT / USER scopes reserved for future work
    return False


def _can_edit_project(user: User, project: Project) -> bool:
    # H3-b: all scope members can write. Archive gate is separate.
    return _can_view_project(user, project)


def _can_archive_project(user: User, project: Project) -> bool:
    if user.role == "platform_admin":
        return True
    if not _can_view_project(user, project):
        return False
    return str(project.created_by) == str(user.id) or _is_tenant_admin(user)


async def _load_project(db: AsyncSession, project_id: uuid.UUID) -> Project:
    project = (await db.execute(select(Project).where(Project.id == project_id))).scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return project


async def _require_view(
    db: AsyncSession, project_id: uuid.UUID, user: User
) -> Project:
    project = await _load_project(db, project_id)
    if not _can_view_project(user, project):
        raise HTTPException(status_code=403, detail="No access to this project")
    return project


async def _require_edit(
    db: AsyncSession, project_id: uuid.UUID, user: User, *, allow_archived: bool = False
) -> Project:
    project = await _require_view(db, project_id, user)
    if not _can_edit_project(user, project):
        raise HTTPException(status_code=403, detail="No edit access to this project")
    if project.archived_at is not None and not allow_archived:
        raise HTTPException(status_code=409, detail="Project is archived")
    return project


# ── enrichment ──────────────────────────────────────────────────────────

async def _enrich(db: AsyncSession, project: Project) -> ProjectListItem:
    """Attach agent summaries + counts to a Project for list/detail responses."""
    # Agents
    ag_rows = (await db.execute(
        select(Agent.id, Agent.name, Agent.avatar_url)
        .join(ProjectAgent, ProjectAgent.agent_id == Agent.id)
        .where(ProjectAgent.project_id == project.id)
        .order_by(ProjectAgent.added_at)
    )).all()
    agents = [
        ProjectAgentSummary(agent_id=row.id, name=row.name, avatar_url=row.avatar_url)
        for row in ag_rows
    ]
    # Counts
    agent_count = len(agents)
    file_count = (await db.execute(
        select(func.count(ProjectFile.id)).where(ProjectFile.project_id == project.id)
    )).scalar_one() or 0
    session_count = (await db.execute(
        select(func.count(ChatSession.id)).where(ChatSession.project_id == project.id)
    )).scalar_one() or 0
    last_message_at = (await db.execute(
        select(func.max(ChatSession.last_message_at)).where(ChatSession.project_id == project.id)
    )).scalar_one()

    return ProjectListItem(
        id=project.id,
        name=project.name,
        description=project.description,
        scope_type=project.scope_type,
        scope_id=project.scope_id,
        chat_visibility=project.chat_visibility,
        archived_at=project.archived_at,
        created_by=project.created_by,
        created_at=project.created_at,
        updated_at=project.updated_at,
        agent_count=agent_count,
        file_count=file_count,
        session_count=session_count,
        last_message_at=last_message_at,
        agents=agents,
    )


# ── CRUD ────────────────────────────────────────────────────────────────

@router.get("", response_model=list[ProjectListItem])
async def list_projects(
    q: str | None = Query(default=None, description="Substring match on name (case-insensitive)"),
    archived: bool = Query(default=False, description="Include archived projects"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List projects the current user can see (MVP = same tenant)."""
    # MVP: only tenant-scoped projects exist, and only same-tenant ones are visible
    conditions = [
        Project.scope_type == ProjectScopeType.TENANT.value,
        Project.scope_id == current_user.tenant_id,
    ]
    if not archived:
        conditions.append(Project.archived_at.is_(None))
    if q:
        conditions.append(Project.name.ilike(f"%{q}%"))

    projects = (await db.execute(
        select(Project).where(and_(*conditions)).order_by(Project.updated_at.desc())
    )).scalars().all()

    return [await _enrich(db, p) for p in projects]


@router.post("", response_model=ProjectOut, status_code=status.HTTP_201_CREATED)
async def create_project(
    body: ProjectCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Create a new tenant-scoped project and seed its workspace + BRIEF.md."""
    if not current_user.tenant_id:
        raise HTTPException(status_code=400, detail="User is not in a tenant")

    # Name uniqueness inside scope
    existing = (await db.execute(
        select(Project.id).where(
            Project.scope_type == ProjectScopeType.TENANT.value,
            Project.scope_id == current_user.tenant_id,
            Project.name == body.name,
            Project.archived_at.is_(None),
        )
    )).first()
    if existing:
        raise HTTPException(status_code=409, detail="A project with this name already exists")

    project = Project(
        name=body.name,
        description=body.description,
        created_by=current_user.id,
        scope_type=ProjectScopeType.TENANT.value,
        scope_id=current_user.tenant_id,
        chat_visibility=ProjectChatVisibility.SHARED.value,
    )
    db.add(project)
    await db.flush()  # populate project.id

    # Seed filesystem workspace + BRIEF template
    project_workspace_service.ensure_initialized(project.id, project.name)

    await db.commit()
    await db.refresh(project)
    return await _enrich(db, project)


@router.get("/{project_id}", response_model=ProjectOut)
async def get_project(
    project_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    project = await _require_view(db, project_id, current_user)
    return await _enrich(db, project)


@router.patch("/{project_id}", response_model=ProjectOut)
async def update_project(
    project_id: uuid.UUID,
    body: ProjectUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    project = await _require_edit(db, project_id, current_user)
    if body.name is not None and body.name != project.name:
        # Name uniqueness check
        dup = (await db.execute(
            select(Project.id).where(
                Project.scope_type == project.scope_type,
                Project.scope_id == project.scope_id,
                Project.name == body.name,
                Project.id != project.id,
            )
        )).first()
        if dup:
            raise HTTPException(status_code=409, detail="A project with this name already exists")
        project.name = body.name
    if body.description is not None:
        project.description = body.description
    if body.chat_visibility is not None:
        project.chat_visibility = body.chat_visibility
    await db.commit()
    await db.refresh(project)
    return await _enrich(db, project)


@router.post("/{project_id}/archive", response_model=ProjectOut)
async def archive_project(
    project_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    project = await _require_view(db, project_id, current_user)
    if not _can_archive_project(current_user, project):
        raise HTTPException(status_code=403, detail="Only creator or tenant admin can archive")
    if project.archived_at is None:
        project.archived_at = datetime.now(_tz.utc)
        await db.commit()
        await db.refresh(project)
    return await _enrich(db, project)


@router.post("/{project_id}/unarchive", response_model=ProjectOut)
async def unarchive_project(
    project_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    project = await _require_view(db, project_id, current_user)
    if not _can_archive_project(current_user, project):
        raise HTTPException(status_code=403, detail="Only creator or tenant admin can unarchive")
    if project.archived_at is not None:
        project.archived_at = None
        await db.commit()
        await db.refresh(project)
    return await _enrich(db, project)


# ── Agent membership ────────────────────────────────────────────────────

@router.get("/{project_id}/agents", response_model=list[ProjectAgentOut])
async def list_project_agents(
    project_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await _require_view(db, project_id, current_user)
    rows = (await db.execute(
        select(ProjectAgent, Agent.name, Agent.avatar_url)
        .join(Agent, Agent.id == ProjectAgent.agent_id)
        .where(ProjectAgent.project_id == project_id)
        .order_by(ProjectAgent.added_at)
    )).all()
    return [
        ProjectAgentOut(
            project_id=pa.project_id,
            agent_id=pa.agent_id,
            agent_name=name,
            avatar_url=avatar_url,
            added_by=pa.added_by,
            added_at=pa.added_at,
        )
        for (pa, name, avatar_url) in rows
    ]


@router.post(
    "/{project_id}/agents",
    response_model=ProjectAgentOut,
    status_code=status.HTTP_201_CREATED,
)
async def add_project_agent(
    project_id: uuid.UUID,
    body: ProjectAgentAdd,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    project = await _require_edit(db, project_id, current_user)
    # Agent must exist and be in the same tenant as the project
    agent = (await db.execute(select(Agent).where(Agent.id == body.agent_id))).scalar_one_or_none()
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    if agent.tenant_id != project.scope_id:
        raise HTTPException(status_code=403, detail="Agent is not in this tenant")
    # Idempotent re-add
    existing = (await db.execute(
        select(ProjectAgent).where(
            ProjectAgent.project_id == project_id,
            ProjectAgent.agent_id == body.agent_id,
        )
    )).scalar_one_or_none()
    if existing:
        return ProjectAgentOut(
            project_id=existing.project_id,
            agent_id=existing.agent_id,
            agent_name=agent.name,
            avatar_url=agent.avatar_url,
            added_by=existing.added_by,
            added_at=existing.added_at,
        )
    link = ProjectAgent(project_id=project_id, agent_id=body.agent_id, added_by=current_user.id)
    db.add(link)
    await db.commit()
    await db.refresh(link)
    return ProjectAgentOut(
        project_id=link.project_id,
        agent_id=link.agent_id,
        agent_name=agent.name,
        avatar_url=agent.avatar_url,
        added_by=link.added_by,
        added_at=link.added_at,
    )


@router.delete("/{project_id}/agents/{agent_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_project_agent(
    project_id: uuid.UUID,
    agent_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Remove an agent from a project. Existing chat sessions retain their project_id (history preserved)."""
    await _require_edit(db, project_id, current_user)
    link = (await db.execute(
        select(ProjectAgent).where(
            ProjectAgent.project_id == project_id,
            ProjectAgent.agent_id == agent_id,
        )
    )).scalar_one_or_none()
    if link:
        await db.delete(link)
        await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ── Files ───────────────────────────────────────────────────────────────

_MAX_UPLOAD_BYTES = 50 * 1024 * 1024  # 50 MB


def _ph_to_out(
    ph,
    record: ProjectFile | None,
    project_id: uuid.UUID,
) -> ProjectFileOut:
    """Build a ProjectFileOut from a PhysicalFile entry + optional DB row.

    `updated_at` is sourced from filesystem mtime when available — that's the
    truth for "last modified". The DB column tracks DB-row updates (renames,
    metadata edits) which can lag agent writes.
    """
    fs_updated = (
        datetime.fromisoformat(ph.mtime_iso) if getattr(ph, "mtime_iso", "") else datetime.now(_tz.utc)
    )
    if ph.is_dir:
        # Directories never have a DB row — they're filesystem-only.
        return ProjectFileOut(
            id=uuid.uuid5(uuid.NAMESPACE_URL, f"dir:{project_id}:{ph.physical_path}"),
            project_id=project_id,
            filename=ph.filename,
            path=ph.physical_path,
            is_dir=True,
            size_bytes=0,
            mime_type="",
            created_by_type="agent",  # neutral default for synthetic dir entries
            created_by=uuid.UUID(int=0),
            created_at=fs_updated,
            updated_at=fs_updated,
        )
    if record is None:
        return ProjectFileOut(
            id=uuid.uuid5(uuid.NAMESPACE_URL, f"synthetic:{project_id}:{ph.physical_path}"),
            project_id=project_id,
            filename=ph.filename,
            path=ph.physical_path,
            is_dir=False,
            size_bytes=ph.size_bytes,
            mime_type=ph.mime_type,
            created_by_type="agent",
            created_by=uuid.UUID(int=0),
            created_at=fs_updated,
            updated_at=fs_updated,
        )
    return ProjectFileOut(
        id=record.id,
        project_id=record.project_id,
        filename=record.filename,
        path=record.physical_path,
        is_dir=False,
        size_bytes=ph.size_bytes,
        mime_type=record.mime_type or ph.mime_type,
        created_by_type=record.created_by_type,  # type: ignore[arg-type]
        created_by=record.created_by,
        created_at=record.created_at,
        updated_at=fs_updated,  # filesystem mtime — overrides DB for freshness
    )


@router.get("/{project_id}/files", response_model=list[ProjectFileOut])
async def list_project_files(
    project_id: uuid.UUID,
    path: str = Query(default=""),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List files and directories under `path` (default: workspace root).

    Single-level only — to navigate into a subdir, the caller passes its path.
    Reconciliation: any file at this level without a matching DB row (agent
    writes that bypassed the upload API) gets a row inserted on demand.
    """
    await _require_view(db, project_id, current_user)
    try:
        physical = project_workspace_service.list_physical_files(project_id, sub_path=path)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    file_paths = [ph.physical_path for ph in physical if not ph.is_dir]
    db_files: list[ProjectFile] = []
    if file_paths:
        db_files = (await db.execute(
            select(ProjectFile).where(
                ProjectFile.project_id == project_id,
                ProjectFile.physical_path.in_(file_paths),
            )
        )).scalars().all()
    by_path = {pf.physical_path: pf for pf in db_files}

    inserted_any = False
    for ph in physical:
        if ph.is_dir or ph.physical_path in by_path:
            continue
        record = ProjectFile(
            project_id=project_id,
            filename=ph.filename,
            physical_path=ph.physical_path,
            size_bytes=ph.size_bytes,
            mime_type=ph.mime_type,
            created_by_type=ProjectFileCreatedByType.AGENT.value,
            created_by=uuid.UUID(int=0),
        )
        db.add(record)
        by_path[ph.physical_path] = record
        inserted_any = True
    if inserted_any:
        try:
            await db.commit()
        except Exception:  # pragma: no cover — race with another reconciler
            await db.rollback()
            db_files = (await db.execute(
                select(ProjectFile).where(
                    ProjectFile.project_id == project_id,
                    ProjectFile.physical_path.in_(file_paths),
                )
            )).scalars().all()
            by_path = {pf.physical_path: pf for pf in db_files}
        else:
            for pf in by_path.values():
                if pf.id is None:
                    await db.refresh(pf)

    # ── Phase 4 polish: linked-task badges ──────────────────────────────
    # One batch query for ProjectTaskFile rows whose project_file_id is in
    # the current listing's DB rows. We aggregate counts + up to 3 task titles.
    file_id_to_record = {pf.id: pf for pf in by_path.values() if pf.id is not None}
    file_id_to_links: dict[uuid.UUID, list[str]] = {}
    if file_id_to_record:
        link_rows = (await db.execute(
            select(ProjectTaskFile.project_file_id, ProjectTask.title)
            .join(ProjectTask, ProjectTask.id == ProjectTaskFile.project_task_id)
            .where(ProjectTaskFile.project_file_id.in_(file_id_to_record.keys()))
            .order_by(ProjectTask.created_at.desc())
        )).all()
        for fid, title in link_rows:
            file_id_to_links.setdefault(fid, []).append(title)

    out: list[ProjectFileOut] = []
    for ph in physical:
        rec = by_path.get(ph.physical_path)
        item = _ph_to_out(ph, rec, project_id)
        if rec and rec.id in file_id_to_links:
            titles = file_id_to_links[rec.id]
            item.linked_task_count = len(titles)
            item.linked_task_titles = titles[:3]
        out.append(item)
    return out


@router.post("/{project_id}/files", status_code=status.HTTP_201_CREATED)
async def upload_project_file(
    project_id: uuid.UUID,
    file: UploadFile = File(...),
    path: str = Query(default=""),
    conflict: Optional[Literal["replace", "keep_both", "abort"]] = Query(default=None),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Upload a file into `path` (default: workspace root).

    Conflict handling is scoped to the target subdirectory — same name in
    different folders is fine. On 409 the response carries a suggested
    alt name based on what `keep_both` would produce in that subdir.
    """
    await _require_edit(db, project_id, current_user)

    if not file.filename:
        raise HTTPException(status_code=400, detail="No filename")
    sub_path = (path or "").strip().lstrip("/")
    if sub_path == "" and file.filename == BRIEF_FILENAME:
        raise HTTPException(status_code=400, detail=f"'{BRIEF_FILENAME}' is reserved; use the brief endpoints.")

    content = await file.read()
    if len(content) > _MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="File exceeds 50MB limit")

    try:
        result = project_workspace_service.save_upload(
            project_id, file.filename, content, conflict_mode=conflict, sub_path=sub_path,
        )
    except FileExistsError:
        existing_rel = f"{sub_path}/{file.filename}" if sub_path else file.filename
        existing_row = (await db.execute(
            select(ProjectFile).where(
                ProjectFile.project_id == project_id,
                ProjectFile.physical_path == existing_rel,
            )
        )).scalar_one_or_none()
        existing_out = None
        if existing_row:
            existing_out = ProjectFileOut(
                id=existing_row.id,
                project_id=existing_row.project_id,
                filename=existing_row.filename,
                path=existing_row.physical_path,
                is_dir=False,
                size_bytes=existing_row.size_bytes,
                mime_type=existing_row.mime_type,
                created_by_type=existing_row.created_by_type,  # type: ignore[arg-type]
                created_by=existing_row.created_by,
                created_at=existing_row.created_at,
                updated_at=existing_row.updated_at,
            )
        payload = ProjectFileConflict(
            existing=existing_out or ProjectFileOut(
                id=uuid.UUID(int=0),
                project_id=project_id,
                filename=file.filename,
                path=existing_rel,
                is_dir=False,
                size_bytes=0,
                mime_type="",
                created_by_type="agent",
                created_by=uuid.UUID(int=0),
                created_at=datetime.now(_tz.utc),
                updated_at=datetime.now(_tz.utc),
            ),
            suggested_alt_name=project_workspace_service.suggest_alt_name(
                project_id, file.filename, sub_path=sub_path
            ),
        )
        raise HTTPException(status_code=409, detail=payload.model_dump(mode="json"))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    # Upsert DB metadata keyed by physical_path
    existing = (await db.execute(
        select(ProjectFile).where(
            ProjectFile.project_id == project_id,
            ProjectFile.physical_path == result.physical_path,
        )
    )).scalar_one_or_none()
    if existing:
        existing.filename = result.filename
        existing.size_bytes = result.size_bytes
        existing.mime_type = file.content_type or existing.mime_type
        existing.updated_at = datetime.now(_tz.utc)
        existing.created_by_type = ProjectFileCreatedByType.USER.value
        existing.created_by = current_user.id
        record = existing
    else:
        record = ProjectFile(
            project_id=project_id,
            filename=result.filename,
            physical_path=result.physical_path,
            size_bytes=result.size_bytes,
            mime_type=file.content_type or "",
            created_by_type=ProjectFileCreatedByType.USER.value,
            created_by=current_user.id,
        )
        db.add(record)
    await db.commit()
    await db.refresh(record)

    return ProjectFileOut(
        id=record.id,
        project_id=record.project_id,
        filename=record.filename,
        path=record.physical_path,
        is_dir=False,
        size_bytes=record.size_bytes,
        mime_type=record.mime_type,
        created_by_type=record.created_by_type,  # type: ignore[arg-type]
        created_by=record.created_by,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


# IMPORTANT: static-path routes (/files/content, /files/download) must be
# registered BEFORE the parametric /files/{file_id} route below so FastAPI
# matches them first.

@router.get("/{project_id}/files/content", response_model=ProjectFileContent)
async def read_project_file_content(
    project_id: uuid.UUID,
    path: str = Query(..., min_length=1),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Return text content of a file under `path`. Used by the inline editor / preview."""
    await _require_view(db, project_id, current_user)
    try:
        text = project_workspace_service.read_text(project_id, path)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="File not found")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return ProjectFileContent(path=path, content=text)


@router.put("/{project_id}/files/content", response_model=ProjectFileOut)
async def write_project_file_content(
    project_id: uuid.UUID,
    body: ProjectFileWrite,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Write text content (used by FileBrowser for new file / edit / new folder via .gitkeep).

    Auto-creates parent directories. BRIEF.md is rejected — use the brief endpoints.
    Upserts a ProjectFile DB row keyed by physical_path.
    """
    await _require_edit(db, project_id, current_user)
    try:
        result = project_workspace_service.write_text(project_id, body.path, body.content)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"Filesystem error: {e}")

    # Hidden files (e.g. .gitkeep) are not user-visible; skip DB row.
    if result.filename.startswith("."):
        return ProjectFileOut(
            id=uuid.uuid5(uuid.NAMESPACE_URL, f"hidden:{project_id}:{result.physical_path}"),
            project_id=project_id,
            filename=result.filename,
            path=result.physical_path,
            is_dir=False,
            size_bytes=result.size_bytes,
            mime_type="",
            created_by_type="user",
            created_by=current_user.id,
            created_at=datetime.now(_tz.utc),
            updated_at=datetime.now(_tz.utc),
        )

    existing = (await db.execute(
        select(ProjectFile).where(
            ProjectFile.project_id == project_id,
            ProjectFile.physical_path == result.physical_path,
        )
    )).scalar_one_or_none()
    if existing:
        existing.size_bytes = result.size_bytes
        existing.updated_at = datetime.now(_tz.utc)
        record = existing
    else:
        record = ProjectFile(
            project_id=project_id,
            filename=result.filename,
            physical_path=result.physical_path,
            size_bytes=result.size_bytes,
            mime_type="text/plain",
            created_by_type=ProjectFileCreatedByType.USER.value,
            created_by=current_user.id,
        )
        db.add(record)
    await db.commit()
    await db.refresh(record)

    return ProjectFileOut(
        id=record.id,
        project_id=record.project_id,
        filename=record.filename,
        path=record.physical_path,
        is_dir=False,
        size_bytes=record.size_bytes,
        mime_type=record.mime_type,
        created_by_type=record.created_by_type,  # type: ignore[arg-type]
        created_by=record.created_by,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


@router.get("/{project_id}/files/download")
async def download_project_file_by_path(
    project_id: uuid.UUID,
    path: str = Query(..., min_length=1),
    token: str = Query(default=""),
    credentials: HTTPAuthorizationCredentials | None = Depends(HTTPBearer(auto_error=False)),
    db: AsyncSession = Depends(get_db),
):
    """Download / serve a project file by its relative path (used by FileBrowser).

    Auth via Bearer header OR `?token=` query parameter — the latter is for
    `<img>` / `<iframe>` use cases that cannot send custom headers.
    Mirrors the pattern used by `/api/agents/{aid}/files/download`.
    Content-Disposition is `inline` so the browser previews PDFs/images in-frame.
    """
    from app.core.security import decode_access_token

    jwt_token: str | None = None
    if credentials:
        jwt_token = credentials.credentials
    elif token:
        jwt_token = token
    if not jwt_token:
        raise HTTPException(status_code=401, detail="Not authenticated")

    payload = decode_access_token(jwt_token)
    user_id = payload.get("sub") if payload else None
    if not user_id:
        raise HTTPException(status_code=401, detail="Invalid token")
    user = (await db.execute(select(User).where(User.id == uuid.UUID(user_id)))).scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=401, detail="User not found")

    await _require_view(db, project_id, user)
    try:
        data = project_workspace_service.read_file_bytes(project_id, path)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="File not found")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    record = (await db.execute(
        select(ProjectFile).where(
            ProjectFile.project_id == project_id,
            ProjectFile.physical_path == path,
        )
    )).scalar_one_or_none()
    filename = record.filename if record else path.split("/")[-1]
    media_type = (record.mime_type if record else "") or "application/octet-stream"

    from io import BytesIO
    return StreamingResponse(
        BytesIO(data),
        media_type=media_type,
        headers={"Content-Disposition": f'inline; filename="{filename}"'},
    )


@router.get("/{project_id}/files/recent", response_model=list[ProjectFileOut])
async def list_recent_project_files(
    project_id: uuid.UUID,
    hours: int = Query(default=168, ge=1, le=720),
    limit: int = Query(default=20, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Recently-modified files (recursive). Powers the Overview "recent activity"
    section. Sorted newest-first, capped at `limit`. Excludes BRIEF.md and hidden dirs.
    """
    await _require_view(db, project_id, current_user)
    physical = project_workspace_service.list_recent_files(project_id, hours=hours, limit=limit)

    # Look up DB rows (by physical_path) to get created_by_type, real id.
    paths = [ph.physical_path for ph in physical]
    by_path: dict[str, ProjectFile] = {}
    if paths:
        db_files = (await db.execute(
            select(ProjectFile).where(
                ProjectFile.project_id == project_id,
                ProjectFile.physical_path.in_(paths),
            )
        )).scalars().all()
        by_path = {pf.physical_path: pf for pf in db_files}

    out: list[ProjectFileOut] = []
    for ph in physical:
        out.append(_ph_to_out(ph, by_path.get(ph.physical_path), project_id))
    return out


@router.post("/{project_id}/files/cleanup-empty", status_code=status.HTTP_200_OK)
async def cleanup_empty_folders(
    project_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Remove all empty subdirectories in the project workspace.

    "Empty" = contains no user-visible files (only hidden entries like `.gitkeep`).
    Returns `{removed: ["foo", "bar/baz", ...]}`. DB rows are not touched —
    empty dirs have none.
    """
    await _require_edit(db, project_id, current_user)
    removed = project_workspace_service.prune_empty_dirs(project_id)
    if removed:
        logger.info(
            "[cleanup-empty] project=%s user=%s removed=%d paths=%s",
            project_id, current_user.id, len(removed), removed,
        )
    return {"removed": removed}


@router.post("/{project_id}/files/move", status_code=status.HTTP_200_OK)
async def move_project_file(
    project_id: uuid.UUID,
    body: ProjectFileMove,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Rename or relocate a file/directory within the project workspace.

    Used by FileBrowser drag-to-folder. The frontend computes the new full
    path (e.g. dragging "draft.md" onto folder "posts" → dst_path="posts/draft.md").
    Refuses overwrites — caller should remove the destination first or pick
    a different name.
    """
    await _require_edit(db, project_id, current_user)
    try:
        new_path = project_workspace_service.move(project_id, body.src_path, body.dst_path)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Source not found")
    except FileExistsError:
        raise HTTPException(status_code=409, detail="Destination already exists")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    # Update DB rows. Two cases: src was a file (one row) or a directory (many rows).
    src = body.src_path.strip("/")
    dst = body.dst_path.strip("/")
    # Single-file move:
    file_row = (await db.execute(
        select(ProjectFile).where(
            ProjectFile.project_id == project_id,
            ProjectFile.physical_path == src,
        )
    )).scalar_one_or_none()
    if file_row is not None:
        file_row.physical_path = dst
        file_row.filename = dst.split("/")[-1]
        file_row.updated_at = datetime.now(_tz.utc)
    # Directory move: rewrite physical_path for all rows under the prefix.
    rows_under = (await db.execute(
        select(ProjectFile).where(
            ProjectFile.project_id == project_id,
            ProjectFile.physical_path.like(
                src.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "/%",
                escape="\\",
            ),
        )
    )).scalars().all()
    for r in rows_under:
        # Replace the leading src/ with dst/
        rest = r.physical_path[len(src) + 1:]
        r.physical_path = f"{dst}/{rest}"
        r.updated_at = datetime.now(_tz.utc)

    if file_row is not None or rows_under:
        await db.commit()

    return {"src_path": src, "dst_path": new_path}


@router.delete("/{project_id}/files", status_code=status.HTTP_204_NO_CONTENT)
async def delete_project_file_by_path(
    project_id: uuid.UUID,
    path: str = Query(..., min_length=1),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Delete a file or directory at `path`.

    For directories, recursively removes everything underneath (including any
    DB rows for files inside). Empty parent directories are not auto-cleaned.
    """
    await _require_edit(db, project_id, current_user)
    try:
        full = project_workspace_service._safe_path(project_id, path)  # pyright: ignore[reportPrivateUsage]
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    if not full.exists():
        raise HTTPException(status_code=404, detail="Path not found")

    try:
        if full.is_dir():
            project_workspace_service.delete_directory(project_id, path)
            # Sweep DB rows for any files that were under this directory.
            normalized = path.strip("/")
            prefix = f"{normalized}/"
            await db.execute(
                _sql_delete(ProjectFile).where(
                    ProjectFile.project_id == project_id,
                    or_(
                        ProjectFile.physical_path == normalized,
                        ProjectFile.physical_path.like(prefix.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%", escape="\\"),
                    ),
                )
            )
            await db.commit()
        else:
            project_workspace_service.delete_file(project_id, path)
            record = (await db.execute(
                select(ProjectFile).where(
                    ProjectFile.project_id == project_id,
                    ProjectFile.physical_path == path,
                )
            )).scalar_one_or_none()
            if record:
                await db.delete(record)
                await db.commit()
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ── Legacy by-id routes (kept for Task↔file linking) ──────────────────────

@router.get("/{project_id}/files/{file_id}")
async def download_project_file(
    project_id: uuid.UUID,
    file_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await _require_view(db, project_id, current_user)
    record = (await db.execute(
        select(ProjectFile).where(
            ProjectFile.id == file_id,
            ProjectFile.project_id == project_id,
        )
    )).scalar_one_or_none()
    if not record:
        raise HTTPException(status_code=404, detail="File not found")
    try:
        data = project_workspace_service.read_file_bytes(project_id, record.physical_path)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="File missing on disk")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    from io import BytesIO
    return StreamingResponse(
        BytesIO(data),
        media_type=record.mime_type or "application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{record.filename}"'},
    )


@router.delete("/{project_id}/files/{file_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_project_file(
    project_id: uuid.UUID,
    file_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await _require_edit(db, project_id, current_user)
    record = (await db.execute(
        select(ProjectFile).where(
            ProjectFile.id == file_id,
            ProjectFile.project_id == project_id,
        )
    )).scalar_one_or_none()
    if not record:
        raise HTTPException(status_code=404, detail="File not found")
    try:
        project_workspace_service.delete_file(project_id, record.physical_path)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    await db.delete(record)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ── Brief ───────────────────────────────────────────────────────────────

@router.get("/{project_id}/brief", response_model=ProjectBriefOut)
async def get_project_brief(
    project_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    project = await _require_view(db, project_id, current_user)
    # Lazy self-heal: if BRIEF.md is missing (e.g. the project's workspace
    # directory was lost in a volume-mount change, a disk wipe, or a manual
    # delete), re-seed the default template instead of returning empty content.
    project_workspace_service.ensure_initialized(project.id, project.name)
    content = project_workspace_service.read_brief(project_id)
    return ProjectBriefOut(content=content)


@router.put("/{project_id}/brief", response_model=ProjectBriefOut)
async def put_project_brief(
    project_id: uuid.UUID,
    body: ProjectBriefUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await _require_edit(db, project_id, current_user)
    project_workspace_service.write_brief(
        project_id,
        body.content,
        actor_type="user",
        actor_id=str(current_user.id),
    )
    return ProjectBriefOut(content=body.content)


@router.get("/{project_id}/brief/history")
async def list_brief_history(
    project_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List past BRIEF.md snapshots for undo / audit."""
    await _require_view(db, project_id, current_user)
    return project_workspace_service.list_brief_history(project_id)


@router.get("/{project_id}/brief/history/{filename}")
async def get_brief_snapshot(
    project_id: uuid.UUID,
    filename: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await _require_view(db, project_id, current_user)
    try:
        content = project_workspace_service.read_brief_snapshot(project_id, filename)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Snapshot not found")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"filename": filename, "content": content}


@router.post("/{project_id}/brief/history/{filename}/restore", response_model=ProjectBriefOut)
async def restore_brief_snapshot(
    project_id: uuid.UUID,
    filename: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Restore a snapshot as the current BRIEF.md. The current content is itself
    snapshotted first so restore is reversible."""
    await _require_edit(db, project_id, current_user)
    try:
        content = project_workspace_service.read_brief_snapshot(project_id, filename)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Snapshot not found")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    project_workspace_service.write_brief(
        project_id,
        content,
        actor_type="user",
        actor_id=str(current_user.id),
    )
    return ProjectBriefOut(content=content)


# ── Chat sessions bound to this project ─────────────────────────────────

@router.get("/{project_id}/chat-sessions", response_model=list[ProjectChatSessionOut])
async def list_project_chat_sessions(
    project_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List sessions attached to this project.

    Visibility rules (MVP):
    - chat_visibility=shared → scope members see all sessions; the owned_by_me
      flag tells the frontend whether to enable the input box
    - chat_visibility=private → each user sees only their own sessions
    """
    project = await _require_view(db, project_id, current_user)

    conds = [ChatSession.project_id == project_id]
    if project.chat_visibility == ProjectChatVisibility.PRIVATE.value:
        conds.append(ChatSession.user_id == current_user.id)

    rows = (await db.execute(
        select(ChatSession, Agent.name, User.display_name)
        .join(Agent, Agent.id == ChatSession.agent_id)
        .outerjoin(User, User.id == ChatSession.user_id)
        .where(and_(*conds))
        .order_by(func.coalesce(ChatSession.last_message_at, ChatSession.created_at).desc())
    )).all()

    # Message counts: ChatMessage.conversation_id stores the session UUID as a string
    session_id_strs = [str(r[0].id) for r in rows]
    msg_counts: dict[str, int] = {}
    if session_id_strs:
        count_rows = (await db.execute(
            select(ChatMessage.conversation_id, func.count(ChatMessage.id))
            .where(ChatMessage.conversation_id.in_(session_id_strs))
            .group_by(ChatMessage.conversation_id)
        )).all()
        msg_counts = {sid: n for sid, n in count_rows}

    return [
        ProjectChatSessionOut(
            id=s.id,
            agent_id=s.agent_id,
            agent_name=agent_name,
            user_id=s.user_id,
            user_display_name=display_name,
            title=s.title,
            created_at=s.created_at,
            last_message_at=s.last_message_at,
            message_count=msg_counts.get(str(s.id), 0),
            owned_by_me=str(s.user_id) == str(current_user.id),
        )
        for (s, agent_name, display_name) in rows
    ]


# ── Scheduled tasks ─────────────────────────────────────────────────────
#
# Implementation note: this is NOT a new table. Each "scheduled task" is an
# entry in `agent_triggers` with:
#   type       = "cron"
#   focus_ref  = "project:{project_id}"            ← binds the task to a project
#   config     = {"expr": <cron expression>}        ← derived from frequency enum
#   reason     = <user-written prompt>              ← what the agent should do
#
# When the trigger fires, trigger_daemon creates a ChatSession. We hook it
# there to set session.project_id so the run appears in the project Chats tab
# and build_project_context_block injects the BRIEF automatically.

_FOCUS_PREFIX = "project:"


def _build_cron(frequency: str, hour: int) -> str:
    """Compose a cron expression from a frequency preset + hour-of-day (0-23).

    `hour` is ignored for `hourly` (which fires every hour at minute 0)."""
    if frequency == "hourly":
        return "0 * * * *"
    if frequency == "daily":
        return f"0 {hour} * * *"
    if frequency == "weekdays":
        return f"0 {hour} * * 1-5"
    if frequency == "weekly":
        return f"0 {hour} * * 1"
    raise ValueError(f"Unknown frequency: {frequency}")


def _cron_to_frequency_and_hour(expr: str) -> tuple[ScheduledTaskFrequency, int]:
    """Inverse of `_build_cron`. Falls back to ('daily', 9) on parse failure."""
    parts = (expr or "").split()
    if len(parts) != 5:
        return ("daily", 9)
    _, hour_token, _, _, dow = parts
    if hour_token == "*":
        return ("hourly", 9)
    try:
        hour = int(hour_token)
    except ValueError:
        return ("daily", 9)
    if dow == "1-5":
        return ("weekdays", hour)
    if dow == "1":
        return ("weekly", hour)
    return ("daily", hour)


def _focus_ref_for_project(project_id: uuid.UUID) -> str:
    return f"{_FOCUS_PREFIX}{project_id}"


async def _load_scheduled_task_or_404(
    db: AsyncSession, project_id: uuid.UUID, task_id: uuid.UUID
) -> AgentTrigger:
    trig = (await db.execute(
        select(AgentTrigger).where(AgentTrigger.id == task_id)
    )).scalar_one_or_none()
    if trig is None or trig.focus_ref != _focus_ref_for_project(project_id):
        raise HTTPException(status_code=404, detail="Scheduled task not found")
    return trig


async def _compute_next_fire_at(trig: AgentTrigger) -> datetime | None:
    """Compute the next scheduled fire time in UTC, or None if disabled / unparseable.

    Uses the agent's timezone (via `get_agent_timezone`) so the result agrees with
    what the trigger_daemon will actually do at fire time."""
    if not trig.is_enabled:
        return None
    expr = (trig.config or {}).get("expr", "")
    if not expr:
        return None
    try:
        from zoneinfo import ZoneInfo
        from croniter import croniter
        from app.services.timezone_utils import get_agent_timezone

        tz_name = await get_agent_timezone(trig.agent_id)
        try:
            tz = ZoneInfo(tz_name)
        except Exception:
            tz = ZoneInfo("UTC")
        base = trig.last_fired_at or trig.created_at
        if base.tzinfo is None:
            base = base.replace(tzinfo=_tz.utc)
        local_base = base.astimezone(tz)
        cron = croniter(expr, local_base)
        local_next = cron.get_next(datetime)
        return local_next.astimezone(_tz.utc)
    except Exception:
        return None


async def _serialize_scheduled_task(db: AsyncSession, trig: AgentTrigger, project_id: uuid.UUID) -> ProjectScheduledTaskOut:
    agent_row = (await db.execute(
        select(Agent.name, Agent.avatar_url).where(Agent.id == trig.agent_id)
    )).first()
    agent_name = agent_row[0] if agent_row else "Unknown"
    agent_avatar_url = agent_row[1] if agent_row else None
    expr = (trig.config or {}).get("expr", "")
    frequency, hour = _cron_to_frequency_and_hour(expr)
    next_fire_at = await _compute_next_fire_at(trig)
    return ProjectScheduledTaskOut(
        id=trig.id,
        project_id=project_id,
        agent_id=trig.agent_id,
        agent_name=agent_name,
        agent_avatar_url=agent_avatar_url,
        name=trig.name,
        prompt=trig.reason or "",
        frequency=frequency,
        hour=hour,
        is_enabled=trig.is_enabled,
        last_fired_at=trig.last_fired_at,
        next_fire_at=next_fire_at,
        fire_count=trig.fire_count,
        cron_expr=expr,
        created_at=trig.created_at,
    )


@router.get("/{project_id}/scheduled-tasks", response_model=list[ProjectScheduledTaskOut])
async def list_scheduled_tasks(
    project_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await _require_view(db, project_id, current_user)
    rows = (await db.execute(
        select(AgentTrigger)
        .where(AgentTrigger.focus_ref == _focus_ref_for_project(project_id))
        .order_by(AgentTrigger.created_at.desc())
    )).scalars().all()
    return [await _serialize_scheduled_task(db, t, project_id) for t in rows]


@router.post(
    "/{project_id}/scheduled-tasks",
    response_model=ProjectScheduledTaskOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_scheduled_task(
    project_id: uuid.UUID,
    body: ProjectScheduledTaskCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await _require_edit(db, project_id, current_user)

    # Agent must be in this project
    in_project = (await db.execute(
        select(ProjectAgent).where(
            ProjectAgent.project_id == project_id,
            ProjectAgent.agent_id == body.agent_id,
        )
    )).scalar_one_or_none()
    if not in_project:
        raise HTTPException(status_code=403, detail="Agent is not in this project")

    # Name uniqueness check (agent_triggers has UNIQUE(agent_id, name))
    dup = (await db.execute(
        select(AgentTrigger.id).where(
            AgentTrigger.agent_id == body.agent_id,
            AgentTrigger.name == body.name,
        )
    )).first()
    if dup:
        raise HTTPException(status_code=409, detail="This agent already has a trigger with that name")

    trig = AgentTrigger(
        agent_id=body.agent_id,
        name=body.name,
        type="cron",
        config={"expr": _build_cron(body.frequency, body.hour)},
        reason=body.prompt,
        focus_ref=_focus_ref_for_project(project_id),
        is_enabled=body.is_enabled,
    )
    db.add(trig)
    await db.commit()
    await db.refresh(trig)
    return await _serialize_scheduled_task(db, trig, project_id)


@router.patch(
    "/{project_id}/scheduled-tasks/{task_id}",
    response_model=ProjectScheduledTaskOut,
)
async def update_scheduled_task(
    project_id: uuid.UUID,
    task_id: uuid.UUID,
    body: ProjectScheduledTaskUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await _require_edit(db, project_id, current_user)
    trig = await _load_scheduled_task_or_404(db, project_id, task_id)

    if body.name is not None and body.name != trig.name:
        dup = (await db.execute(
            select(AgentTrigger.id).where(
                AgentTrigger.agent_id == trig.agent_id,
                AgentTrigger.name == body.name,
                AgentTrigger.id != trig.id,
            )
        )).first()
        if dup:
            raise HTTPException(status_code=409, detail="Another trigger with that name exists")
        trig.name = body.name
    if body.prompt is not None:
        trig.reason = body.prompt
    if body.frequency is not None or body.hour is not None:
        current_freq, current_hour = _cron_to_frequency_and_hour(
            (trig.config or {}).get("expr", "")
        )
        new_freq = body.frequency if body.frequency is not None else current_freq
        new_hour = body.hour if body.hour is not None else current_hour
        trig.config = {"expr": _build_cron(new_freq, new_hour)}
    if body.is_enabled is not None:
        trig.is_enabled = body.is_enabled
    await db.commit()
    await db.refresh(trig)
    return await _serialize_scheduled_task(db, trig, project_id)


@router.delete(
    "/{project_id}/scheduled-tasks/{task_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_scheduled_task(
    project_id: uuid.UUID,
    task_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await _require_edit(db, project_id, current_user)
    trig = await _load_scheduled_task_or_404(db, project_id, task_id)
    await db.delete(trig)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


async def _run_scheduled_task_in_background(trigger_id: uuid.UUID) -> None:
    """Re-fetch the trigger in a fresh DB session and fire it now.

    Reuses the same code path as the cron daemon — `_invoke_agent_for_triggers`
    creates the ChatSession (with project_id and source_channel='trigger') and
    drives the LLM. We call `_mark_trigger_fired` afterwards so fire_count and
    last_fired_at stay consistent with cron-fired runs."""
    from app.services.trigger_daemon import (
        _invoke_agent_for_triggers,
        _mark_trigger_fired,
    )
    from app.database import async_session

    async with async_session() as db:
        trig = (await db.execute(
            select(AgentTrigger).where(AgentTrigger.id == trigger_id)
        )).scalar_one_or_none()
        if not trig:
            return
        agent_id = trig.agent_id
        # Detach so we can use the row across sessions
        db.expunge(trig)
    await _invoke_agent_for_triggers(agent_id, [trig])
    await _mark_trigger_fired(trigger_id, datetime.now(_tz.utc))


@router.post(
    "/{project_id}/scheduled-tasks/{task_id}/run",
    status_code=status.HTTP_202_ACCEPTED,
)
async def run_scheduled_task_now(
    project_id: uuid.UUID,
    task_id: uuid.UUID,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Fire a scheduled task immediately, out-of-band from the cron schedule.

    Goes through the same code path as a cron-fired run — the LLM call is
    queued as a background task so the HTTP request returns instantly; the
    resulting ChatSession will surface in the project's Chats tab once the
    LLM finishes."""
    await _require_edit(db, project_id, current_user)
    trig = await _load_scheduled_task_or_404(db, project_id, task_id)
    background_tasks.add_task(_run_scheduled_task_in_background, trig.id)
    return {"status": "queued"}


# ── Project tasks (deliverables) ────────────────────────────────────────
#
# A ProjectTask is a per-project to-do / deliverable. Distinct from the
# agent-scoped `tasks` table (which is for supervision / agent reminders).
# Agents in a project chat can read & mutate these via the four
# `*_project_task*` tools — see backend/app/services/agent_tools.py.

_TASK_VALID_STATUSES = {"todo", "doing", "done", "blocked"}


async def _load_task_or_404(
    db: AsyncSession, project_id: uuid.UUID, task_id: uuid.UUID
) -> ProjectTask:
    task = (await db.execute(
        select(ProjectTask).where(
            ProjectTask.id == task_id,
            ProjectTask.project_id == project_id,
        )
    )).scalar_one_or_none()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return task


async def _serialize_task(
    db: AsyncSession,
    task: ProjectTask,
    *,
    detail: bool = False,
) -> ProjectTaskOut | ProjectTaskDetail:
    # Joined info: agent name, user display name, file count
    agent_name: str | None = None
    agent_avatar: str | None = None
    if task.assigned_agent_id is not None:
        ag = (await db.execute(
            select(Agent.name, Agent.avatar_url).where(Agent.id == task.assigned_agent_id)
        )).first()
        if ag:
            agent_name, agent_avatar = ag[0], ag[1]

    user_display: str | None = None
    if task.assigned_user_id is not None:
        u = (await db.execute(
            select(User.display_name).where(User.id == task.assigned_user_id)
        )).first()
        if u:
            user_display = u[0]

    file_count = (await db.execute(
        select(func.count(ProjectTaskFile.project_file_id))
        .where(ProjectTaskFile.project_task_id == task.id)
    )).scalar() or 0

    base_kwargs = dict(
        id=task.id,
        project_id=task.project_id,
        title=task.title,
        description=task.description,
        status=task.status,
        assigned_agent_id=task.assigned_agent_id,
        assigned_agent_name=agent_name,
        assigned_agent_avatar_url=agent_avatar,
        assigned_user_id=task.assigned_user_id,
        assigned_user_display_name=user_display,
        due_date=task.due_date,
        created_by=task.created_by,
        created_by_type=task.created_by_type,
        created_at=task.created_at,
        updated_at=task.updated_at,
        completed_at=task.completed_at,
        linked_file_count=int(file_count),
    )

    if not detail:
        return ProjectTaskOut(**base_kwargs)

    # Detail: also fetch each linked file's metadata
    rows = (await db.execute(
        select(
            ProjectTaskFile.project_file_id,
            ProjectTaskFile.linked_at,
            ProjectTaskFile.linked_by_type,
            ProjectFile.filename,
            ProjectFile.mime_type,
            ProjectFile.size_bytes,
        )
        .join(ProjectFile, ProjectFile.id == ProjectTaskFile.project_file_id)
        .where(ProjectTaskFile.project_task_id == task.id)
        .order_by(ProjectTaskFile.linked_at.desc())
    )).all()
    linked_files = [
        ProjectTaskFileLinkRef(
            file_id=r[0],
            filename=r[3],
            mime_type=r[4],
            size_bytes=r[5],
            linked_at=r[1],
            linked_by_type=r[2],
        )
        for r in rows
    ]
    return ProjectTaskDetail(**base_kwargs, linked_files=linked_files)


@router.get("/{project_id}/tasks", response_model=list[ProjectTaskOut])
async def list_project_tasks(
    project_id: uuid.UUID,
    status_filter: str | None = Query(default=None, alias="status"),
    assigned_agent_id: uuid.UUID | None = Query(default=None),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await _require_view(db, project_id, current_user)

    conds = [ProjectTask.project_id == project_id]
    if status_filter and status_filter != "all":
        if status_filter not in _TASK_VALID_STATUSES:
            raise HTTPException(status_code=400, detail=f"Invalid status: {status_filter}")
        conds.append(ProjectTask.status == status_filter)
    if assigned_agent_id is not None:
        conds.append(ProjectTask.assigned_agent_id == assigned_agent_id)

    tasks = (await db.execute(
        select(ProjectTask)
        .where(and_(*conds))
        # Active first (todo/doing/blocked), done last; within each by created_at desc
        .order_by(
            (ProjectTask.status == ProjectTaskStatus.DONE.value).asc(),
            ProjectTask.created_at.desc(),
        )
    )).scalars().all()
    return [await _serialize_task(db, t) for t in tasks]


@router.post("/{project_id}/tasks", response_model=ProjectTaskDetail, status_code=201)
async def create_project_task_endpoint(
    project_id: uuid.UUID,
    body: ProjectTaskCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    project = await _require_edit(db, project_id, current_user)

    # If assigning to an agent, verify it's actually a member of the project
    if body.assigned_agent_id is not None:
        member = (await db.execute(
            select(ProjectAgent).where(
                ProjectAgent.project_id == project.id,
                ProjectAgent.agent_id == body.assigned_agent_id,
            )
        )).scalar_one_or_none()
        if not member:
            raise HTTPException(
                status_code=400,
                detail="Cannot assign to an agent that is not in this project",
            )

    completed_at = datetime.now(_tz.utc) if body.status == ProjectTaskStatus.DONE.value else None

    task = ProjectTask(
        project_id=project.id,
        title=body.title,
        description=body.description,
        status=body.status,
        assigned_agent_id=body.assigned_agent_id,
        assigned_user_id=body.assigned_user_id,
        due_date=body.due_date,
        created_by=current_user.id,
        created_by_type=ProjectTaskCreatedByType.USER.value,
        completed_at=completed_at,
    )
    db.add(task)
    await db.commit()
    await db.refresh(task)
    return await _serialize_task(db, task, detail=True)


@router.get("/{project_id}/tasks/{task_id}", response_model=ProjectTaskDetail)
async def get_project_task(
    project_id: uuid.UUID,
    task_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await _require_view(db, project_id, current_user)
    task = await _load_task_or_404(db, project_id, task_id)
    return await _serialize_task(db, task, detail=True)


@router.patch("/{project_id}/tasks/{task_id}", response_model=ProjectTaskDetail)
async def update_project_task_endpoint(
    project_id: uuid.UUID,
    task_id: uuid.UUID,
    body: ProjectTaskUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    project = await _require_edit(db, project_id, current_user)
    task = await _load_task_or_404(db, project_id, task_id)

    if body.title is not None:
        task.title = body.title
    if body.description is not None:
        task.description = body.description

    if body.status is not None:
        if body.status not in _TASK_VALID_STATUSES:
            raise HTTPException(status_code=400, detail=f"Invalid status: {body.status}")
        was_done = task.status == ProjectTaskStatus.DONE.value
        is_done = body.status == ProjectTaskStatus.DONE.value
        task.status = body.status
        if is_done and not was_done:
            task.completed_at = datetime.now(_tz.utc)
        elif was_done and not is_done:
            task.completed_at = None

    if body.clear_assignee:
        task.assigned_agent_id = None
        task.assigned_user_id = None
    else:
        if body.assigned_agent_id is not None:
            member = (await db.execute(
                select(ProjectAgent).where(
                    ProjectAgent.project_id == project.id,
                    ProjectAgent.agent_id == body.assigned_agent_id,
                )
            )).scalar_one_or_none()
            if not member:
                raise HTTPException(
                    status_code=400,
                    detail="Cannot assign to an agent that is not in this project",
                )
            task.assigned_agent_id = body.assigned_agent_id
            task.assigned_user_id = None
        if body.assigned_user_id is not None:
            task.assigned_user_id = body.assigned_user_id
            task.assigned_agent_id = None

    if body.clear_due_date:
        task.due_date = None
    elif body.due_date is not None:
        task.due_date = body.due_date

    await db.commit()
    await db.refresh(task)
    return await _serialize_task(db, task, detail=True)


@router.delete("/{project_id}/tasks/{task_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_project_task(
    project_id: uuid.UUID,
    task_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await _require_edit(db, project_id, current_user)
    task = await _load_task_or_404(db, project_id, task_id)
    await db.delete(task)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{project_id}/tasks/{task_id}/files", response_model=ProjectTaskDetail)
async def link_file_to_project_task(
    project_id: uuid.UUID,
    task_id: uuid.UUID,
    body: ProjectTaskFileLinkIn,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await _require_edit(db, project_id, current_user)
    task = await _load_task_or_404(db, project_id, task_id)

    file = (await db.execute(
        select(ProjectFile).where(
            ProjectFile.id == body.file_id,
            ProjectFile.project_id == project_id,
        )
    )).scalar_one_or_none()
    if not file:
        raise HTTPException(status_code=404, detail="File not found in this project")

    existing = (await db.execute(
        select(ProjectTaskFile).where(
            ProjectTaskFile.project_task_id == task.id,
            ProjectTaskFile.project_file_id == file.id,
        )
    )).scalar_one_or_none()
    if not existing:
        link = ProjectTaskFile(
            project_task_id=task.id,
            project_file_id=file.id,
            linked_by_type=ProjectTaskCreatedByType.USER.value,
            linked_by=current_user.id,
        )
        db.add(link)
        await db.commit()

    return await _serialize_task(db, task, detail=True)


@router.delete(
    "/{project_id}/tasks/{task_id}/files/{file_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def unlink_file_from_project_task(
    project_id: uuid.UUID,
    task_id: uuid.UUID,
    file_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await _require_edit(db, project_id, current_user)
    task = await _load_task_or_404(db, project_id, task_id)

    link = (await db.execute(
        select(ProjectTaskFile).where(
            ProjectTaskFile.project_task_id == task.id,
            ProjectTaskFile.project_file_id == file_id,
        )
    )).scalar_one_or_none()
    if link:
        await db.delete(link)
        await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ── Reverse: agent → projects ───────────────────────────────────────────

@agent_projects_router.get("/{agent_id}/projects", response_model=list[ProjectListItem])
async def list_projects_for_agent(
    agent_id: uuid.UUID,
    archived: bool = Query(default=False),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List every project this agent participates in, filtered to caller's visibility."""
    agent = (await db.execute(select(Agent).where(Agent.id == agent_id))).scalar_one_or_none()
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    # Tenant fence — consistent with check_agent_access
    if current_user.role != "platform_admin" and agent.tenant_id != current_user.tenant_id:
        raise HTTPException(status_code=403, detail="No access to this agent")

    conds = [ProjectAgent.agent_id == agent_id]
    if not archived:
        conds.append(Project.archived_at.is_(None))

    projects = (await db.execute(
        select(Project)
        .join(ProjectAgent, ProjectAgent.project_id == Project.id)
        .where(and_(*conds))
        .order_by(Project.updated_at.desc())
    )).scalars().all()

    # Filter out cross-tenant projects the caller can't see
    return [await _enrich(db, p) for p in projects if _can_view_project(current_user, p)]
