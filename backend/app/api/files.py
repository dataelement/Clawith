"""File management API routes for agent workspaces."""

import io
import os
import uuid
import zipfile
from pathlib import Path

import aiofiles
from fastapi import APIRouter, Depends, Form, HTTPException, status
from fastapi.responses import FileResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel

from app.config import get_settings
from app.core.permissions import check_agent_access
from app.core.security import get_current_user
from app.database import get_db
from app.models.user import User
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

settings = get_settings()
router = APIRouter(prefix="/agents/{agent_id}/files", tags=["files"])


class FileInfo(BaseModel):
    name: str
    path: str
    is_dir: bool
    size: int = 0
    modified_at: str = ""


class FileContent(BaseModel):
    path: str
    content: str


class FileWrite(BaseModel):
    content: str


def _agent_base_dir(agent_id: uuid.UUID) -> Path:
    return Path(settings.AGENT_DATA_DIR) / str(agent_id)


def _safe_path(agent_id: uuid.UUID, rel_path: str) -> Path:
    """Ensure the path is within the agent's directory (no path traversal)."""
    base = _agent_base_dir(agent_id)
    full = (base / rel_path).resolve()
    if not str(full).startswith(str(base.resolve())):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Path traversal not allowed")
    return full


@router.get("/", response_model=list[FileInfo])
async def list_files(
    agent_id: uuid.UUID,
    path: str = "",
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List files and directories in an agent's file system."""
    await check_agent_access(db, current_user, agent_id)
    target = _safe_path(agent_id, path)

    if not target.exists():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Path not found")
    if not target.is_dir():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Path is not a directory")

    items = []
    base_abs = _agent_base_dir(agent_id).resolve()
    for entry in sorted(target.iterdir(), key=lambda e: (not e.is_dir(), e.name)):
        if entry.name == '.gitkeep':
            continue
        rel = str(entry.resolve().relative_to(base_abs))
        stat = entry.stat()
        items.append(FileInfo(
            name=entry.name,
            path=rel,
            is_dir=entry.is_dir(),
            size=stat.st_size if entry.is_file() else 0,
            modified_at=str(stat.st_mtime),
        ))
    return items


@router.get("/content", response_model=FileContent)
async def read_file(
    agent_id: uuid.UUID,
    path: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Read the content of a file."""
    await check_agent_access(db, current_user, agent_id)
    target = _safe_path(agent_id, path)

    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File not found")

    async with aiofiles.open(target, "r", encoding="utf-8") as f:
        content = await f.read()
    return FileContent(path=path, content=content)


@router.get("/download")
async def download_file(
    agent_id: uuid.UUID,
    path: str,
    token: str = "",
    credentials: HTTPAuthorizationCredentials | None = Depends(HTTPBearer(auto_error=False)),
    db: AsyncSession = Depends(get_db),
):
    """Download / serve a file from the agent workspace (browser-friendly).
    
    Auth via Bearer header OR `token` query parameter (for <img> tags).
    """
    from app.core.security import decode_access_token

    # Resolve JWT token from either Bearer header or query param
    jwt_token = None
    if credentials:
        jwt_token = credentials.credentials
    elif token:
        jwt_token = token

    if not jwt_token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required")

    payload = decode_access_token(jwt_token)
    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")

    result = await db.execute(select(User).where(User.id == uuid.UUID(user_id)))
    user = result.scalar_one_or_none()
    if not user or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found or inactive")

    await check_agent_access(db, user, agent_id)
    target = _safe_path(agent_id, path)
    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File not found")
    return FileResponse(path=str(target), filename=target.name)


@router.put("/content")
async def write_file(
    agent_id: uuid.UUID,
    path: str,
    data: FileWrite,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Write content to a file (create or overwrite)."""
    await check_agent_access(db, current_user, agent_id)
    target = _safe_path(agent_id, path)

    target.parent.mkdir(parents=True, exist_ok=True)
    async with aiofiles.open(target, "w", encoding="utf-8") as f:
        await f.write(data.content)

    return {"status": "ok", "path": path}


@router.delete("/content")
async def delete_file(
    agent_id: uuid.UUID,
    path: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Delete a file."""
    await check_agent_access(db, current_user, agent_id)
    target = _safe_path(agent_id, path)

    if not target.exists():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File not found")

    if target.is_dir():
        import shutil
        shutil.rmtree(target)
    else:
        target.unlink()

    return {"status": "ok", "path": path}


class ImportSkillBody(BaseModel):
    skill_id: str


@router.post("/import-skill")
async def import_skill_to_agent(
    agent_id: uuid.UUID,
    body: ImportSkillBody,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Import a global skill into this agent's skills/ workspace folder.

    Copies all files from the global skill registry into
    <agent_workspace>/skills/<folder_name>/.
    """
    await check_agent_access(db, current_user, agent_id)

    from sqlalchemy.orm import selectinload
    from app.models.skill import Skill, SkillFile

    # Load the global skill with its files
    result = await db.execute(
        select(Skill).where(Skill.id == body.skill_id).options(selectinload(Skill.files))
    )
    skill = result.scalar_one_or_none()
    if not skill:
        raise HTTPException(status_code=404, detail="Skill not found")

    if not skill.files:
        raise HTTPException(status_code=400, detail="Skill has no files")

    # Write each file into the agent's workspace
    base = _agent_base_dir(agent_id)
    skill_dir = base / "skills" / skill.folder_name
    skill_dir.mkdir(parents=True, exist_ok=True)

    written = []
    for f in skill.files:
        file_path = (skill_dir / f.path).resolve()
        # Safety check
        if not str(file_path).startswith(str(base.resolve())):
            continue
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(f.content, encoding="utf-8")
        written.append(f.path)

    return {
        "status": "ok",
        "skill_name": skill.name,
        "folder_name": skill.folder_name,
        "files_written": len(written),
        "files": written,
    }


# Separate router for file uploads (binary) since we need UploadFile
from fastapi import File as FastFile, UploadFile as UploadFileType


upload_router = APIRouter(prefix="/agents/{agent_id}/files", tags=["files"])


@upload_router.post("/upload")
async def upload_file_to_workspace(
    agent_id: uuid.UUID,
    file: UploadFileType = FastFile(...),
    path: str = "workspace/knowledge_base",
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Upload a binary file to agent workspace."""
    await check_agent_access(db, current_user, agent_id)

    # Validate path prefix
    if not path.startswith(("workspace/", "skills/")):
        raise HTTPException(status_code=400, detail="只能上传到 workspace/ 或 skills/ 目录")

    base = _agent_base_dir(agent_id)
    target_dir = (base / path).resolve()
    if not str(target_dir).startswith(str(base.resolve())):
        raise HTTPException(status_code=403, detail="Path traversal not allowed")

    target_dir.mkdir(parents=True, exist_ok=True)
    filename = file.filename or "unnamed"
    # Sanitize filename
    filename = filename.replace("/", "_").replace("\\", "_")
    save_path = target_dir / filename

    content = await file.read()
    save_path.write_bytes(content)

    # Auto-extract text from non-text files
    extracted_path = None
    from app.services.text_extractor import needs_extraction, save_extracted_text
    if needs_extraction(filename):
        txt_file = save_extracted_text(save_path, content, filename)
        if txt_file:
            base_abs = base.resolve()
            extracted_path = str(txt_file.resolve().relative_to(base_abs))

    return {
        "status": "ok",
        "path": f"{path}/{filename}",
        "filename": filename,
        "size": len(content),
        "extracted_text_path": extracted_path,
    }


MAX_ZIP_SIZE = 50 * 1024 * 1024  # 50MB compressed
MAX_ZIP_FILES = 1000
MAX_UNCOMPRESSED = 500 * 1024 * 1024  # 500MB uncompressed


def _validate_zip(content: bytes) -> tuple[zipfile.ZipFile, list[str], str]:
    """Validate and open a zip archive.

    Returns (ZipFile, file_names, root_folder).
    Raises HTTPException on validation failure.
    """
    if len(content) > MAX_ZIP_SIZE:
        raise HTTPException(status_code=400, detail=f"Zip file too large (max {MAX_ZIP_SIZE // 1024 // 1024}MB)")

    try:
        zf = zipfile.ZipFile(io.BytesIO(content))
    except zipfile.BadZipFile:
        raise HTTPException(status_code=400, detail="Invalid zip file")

    names = zf.namelist()
    total_uncompressed = sum(i.file_size for i in zf.infolist())
    if total_uncompressed > MAX_UNCOMPRESSED:
        zf.close()
        raise HTTPException(
            status_code=400,
            detail=f"Zip uncompressed size too large (max 500MB, got {total_uncompressed // 1024 // 1024}MB)",
        )
    if len(names) > MAX_ZIP_FILES:
        zf.close()
        raise HTTPException(status_code=400, detail=f"Too many files (max {MAX_ZIP_FILES})")

    parts = [n.split("/")[0] for n in names if "/" in n]
    root_folder = parts[0] if parts and all(p == parts[0] for p in parts) else ""

    return zf, names, root_folder


@upload_router.post("/preview-zip")
async def preview_zip(
    agent_id: uuid.UUID,
    file: UploadFileType = FastFile(...),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Preview zip contents before extraction."""
    await check_agent_access(db, current_user, agent_id)

    content = await file.read()
    zf, names, root_folder = _validate_zip(content)
    with zf:
        files = [n for n in names if not n.endswith("/")]
        return {
            "root_folder": root_folder,
            "files": files[:200],
            "total": len(files),
        }


import re as _re

_VALID_ROOT_NAME = _re.compile(r"^[a-zA-Z0-9_-]+$")


@upload_router.post("/extract-zip")
async def extract_zip(
    agent_id: uuid.UUID,
    file: UploadFileType = FastFile(...),
    target_path: str = Form(""),
    root_name: str = Form(""),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Extract zip into agent's skills directory."""
    await check_agent_access(db, current_user, agent_id)

    if root_name and not _VALID_ROOT_NAME.match(root_name):
        raise HTTPException(status_code=400, detail="Invalid root folder name (alphanumeric, hyphens, underscores only)")

    content = await file.read()
    zf, _names, zip_root = _validate_zip(content)

    base = _agent_base_dir(agent_id)
    skills_root = (base / "skills").resolve()

    if target_path:
        target_dir = (skills_root / target_path).resolve()
        if not str(target_dir).startswith(str(skills_root)):
            raise HTTPException(status_code=403, detail="Path traversal not allowed")
    else:
        target_dir = skills_root

    with zf:
        extracted = []
        for info in zf.infolist():
            if info.is_dir() or info.external_attr >> 28 == 0xA:
                continue
            if info.filename.startswith("/") or ".." in Path(info.filename).parts:
                continue

            rel_path = info.filename
            if root_name == "" and zip_root:
                if rel_path.startswith(zip_root + "/"):
                    rel_path = rel_path[len(zip_root) + 1:]
                else:
                    continue
            elif root_name and zip_root and rel_path.startswith(zip_root + "/"):
                rel_path = root_name + rel_path[len(zip_root):]

            if not rel_path:
                continue

            out_path = (target_dir / rel_path).resolve()
            if not str(out_path).startswith(str(skills_root)):
                continue

            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_bytes(zf.read(info.filename))
            extracted.append(str(Path(target_path) / rel_path) if target_path else rel_path)

        from app.services.skill_map import invalidate_cache
        invalidate_cache(agent_id)

        return {
            "status": "ok",
            "extracted": len(extracted),
            "files": extracted[:50],
        }


# ─── Enterprise Knowledge Base ─────────────────────────────────

enterprise_kb_router = APIRouter(prefix="/enterprise/knowledge-base", tags=["enterprise"])


def _enterprise_kb_dir(tenant_id: str) -> Path:
    return Path(settings.AGENT_DATA_DIR) / f"enterprise_info_{tenant_id}" / "knowledge_base"


def _enterprise_info_dir(tenant_id: str) -> Path:
    return Path(settings.AGENT_DATA_DIR) / f"enterprise_info_{tenant_id}"


@enterprise_kb_router.get("/files")
async def list_enterprise_kb_files(
    path: str = "",
    current_user: User = Depends(get_current_user),
):
    """List files in enterprise knowledge base (tenant-scoped)."""
    if not current_user.tenant_id:
        return []
    info_dir = _enterprise_info_dir(str(current_user.tenant_id)).resolve()
    info_dir.mkdir(parents=True, exist_ok=True)

    if path:
        target = (info_dir / path).resolve()
    else:
        target = info_dir
    if not str(target).startswith(str(info_dir)):
        raise HTTPException(status_code=403, detail="Path traversal not allowed")

    if not target.exists() or not target.is_dir():
        return []

    items = []
    for entry in sorted(target.iterdir(), key=lambda e: (not e.is_dir(), e.name)):
        if entry.name == '.gitkeep':
            continue
        rel = str(entry.resolve().relative_to(info_dir.resolve()))
        stat = entry.stat()
        items.append({
            "name": entry.name,
            "path": rel,
            "is_dir": entry.is_dir(),
            "size": stat.st_size if entry.is_file() else 0,
        })
    return items


@enterprise_kb_router.post("/upload")
async def upload_enterprise_kb_file(
    file: UploadFileType = FastFile(...),
    sub_path: str = "",
    current_user: User = Depends(get_current_user),
):
    """Upload a file to enterprise knowledge base (tenant-scoped)."""
    from app.core.security import require_role
    # Only admin can upload to enterprise KB
    if current_user.role not in ("platform_admin", "org_admin"):
        raise HTTPException(status_code=403, detail="Only admins can upload to enterprise knowledge base")
    if not current_user.tenant_id:
        raise HTTPException(status_code=400, detail="No tenant associated")

    info_dir = _enterprise_info_dir(str(current_user.tenant_id))
    target_dir = (info_dir / sub_path).resolve()
    if not str(target_dir).startswith(str(info_dir.resolve())):
        raise HTTPException(status_code=403, detail="Path traversal not allowed")

    target_dir.mkdir(parents=True, exist_ok=True)
    filename = file.filename or "unnamed"
    filename = filename.replace("/", "_").replace("\\", "_")
    save_path = target_dir / filename

    content = await file.read()
    save_path.write_bytes(content)

    # Auto-extract text from non-text files
    extracted_path = None
    from app.services.text_extractor import needs_extraction, save_extracted_text
    if needs_extraction(filename):
        txt_file = save_extracted_text(save_path, content, filename)
        if txt_file:
            extracted_path = str(txt_file.resolve().relative_to(info_dir.resolve()))

    return {
        "status": "ok",
        "path": f"{sub_path}/{filename}" if sub_path else filename,
        "filename": filename,
        "size": len(content),
        "extracted_text_path": extracted_path,
    }


@enterprise_kb_router.get("/content")
async def read_enterprise_file(
    path: str,
    current_user: User = Depends(get_current_user),
):
    """Read content of an enterprise knowledge base file (tenant-scoped)."""
    if not current_user.tenant_id:
        raise HTTPException(status_code=400, detail="No tenant associated")
    info_dir = _enterprise_info_dir(str(current_user.tenant_id))
    target = (info_dir / path).resolve()
    if not str(target).startswith(str(info_dir.resolve())):
        raise HTTPException(status_code=403, detail="Path traversal not allowed")
    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail="File not found")

    try:
        content = target.read_text(encoding="utf-8", errors="replace")
        return {"path": path, "content": content}
    except Exception:
        return {"path": path, "content": f"[二进制文件: {target.name}, {target.stat().st_size} bytes]"}


@enterprise_kb_router.put("/content")
async def write_enterprise_file(
    path: str,
    data: FileWrite,
    current_user: User = Depends(get_current_user),
):
    """Write content to an enterprise file (tenant-scoped)."""
    if current_user.role not in ("platform_admin", "org_admin"):
        raise HTTPException(status_code=403, detail="Only admins can edit enterprise knowledge base")
    if not current_user.tenant_id:
        raise HTTPException(status_code=400, detail="No tenant associated")

    info_dir = _enterprise_info_dir(str(current_user.tenant_id))
    target = (info_dir / path).resolve()
    if not str(target).startswith(str(info_dir.resolve())):
        raise HTTPException(status_code=403, detail="Path traversal not allowed")

    target.parent.mkdir(parents=True, exist_ok=True)
    async with aiofiles.open(target, "w", encoding="utf-8") as f:
        await f.write(data.content)
    return {"status": "ok", "path": path}


@enterprise_kb_router.delete("/content")
async def delete_enterprise_file(
    path: str,
    current_user: User = Depends(get_current_user),
):
    """Delete an enterprise knowledge base file (tenant-scoped)."""
    if current_user.role not in ("platform_admin", "org_admin"):
        raise HTTPException(status_code=403, detail="Only admins can delete enterprise knowledge base files")
    if not current_user.tenant_id:
        raise HTTPException(status_code=400, detail="No tenant associated")

    info_dir = _enterprise_info_dir(str(current_user.tenant_id))
    target = (info_dir / path).resolve()
    if not str(target).startswith(str(info_dir.resolve())):
        raise HTTPException(status_code=403, detail="Path traversal not allowed")
    if not target.exists():
        raise HTTPException(status_code=404, detail="File not found")

    if target.is_dir():
        import shutil
        shutil.rmtree(target)
    else:
        target.unlink()
    return {"status": "ok", "path": path}


# ─── Agent-level ClawHub / URL Skill Import ─────────────────

class ClawhubImportBody(BaseModel):
    slug: str

class UrlImportBody(BaseModel):
    url: str


@router.post("/import-from-clawhub")
async def agent_import_from_clawhub(
    agent_id: uuid.UUID,
    body: ClawhubImportBody,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Import a skill from ClawHub directly into this agent's skills/ workspace."""
    await check_agent_access(db, current_user, agent_id)

    from app.api.skills import (
        CLAWHUB_BASE, _fetch_github_directory, _parse_skill_md_frontmatter, _get_github_token,
    )
    import httpx

    slug = body.slug

    # 1. Fetch metadata from ClawHub
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(f"{CLAWHUB_BASE}/v1/skills/{slug}")
            if resp.status_code == 429:
                raise HTTPException(429, "ClawHub rate limit exceeded. Please wait and try again.")
            if resp.status_code != 200:
                raise HTTPException(502, f"ClawHub API error: {resp.status_code}")
            meta = resp.json()
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(502, f"Failed to connect to ClawHub: {e}")

    skill_info = meta.get("skill", {})
    owner_info = meta.get("owner", {})
    handle = owner_info.get("handle", "").lower()
    if not handle:
        raise HTTPException(400, "Could not determine skill owner from ClawHub metadata")

    # 2. Fetch files from GitHub
    github_path = f"skills/{handle}/{slug}"
    tenant_id = str(current_user.tenant_id) if current_user.tenant_id else None
    token = await _get_github_token(tenant_id)
    files = await _fetch_github_directory("openclaw", "skills", github_path, "main", token)

    # 3. Write to agent workspace: skills/<slug>/
    base = _agent_base_dir(agent_id)
    folder_name = slug
    skill_dir = base / "skills" / folder_name
    skill_dir.mkdir(parents=True, exist_ok=True)

    written = []
    for f in files:
        file_path = (skill_dir / f["path"]).resolve()
        if not str(file_path).startswith(str(base.resolve())):
            continue
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(f["content"], encoding="utf-8")
        written.append(f["path"])

    return {
        "status": "ok",
        "skill_name": skill_info.get("displayName", slug),
        "folder_name": folder_name,
        "files_written": len(written),
        "files": written,
    }


@router.post("/import-from-url")
async def agent_import_from_url(
    agent_id: uuid.UUID,
    body: UrlImportBody,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Import a skill from a GitHub URL directly into this agent's skills/ workspace."""
    await check_agent_access(db, current_user, agent_id)

    from app.api.skills import _parse_github_url, _fetch_github_directory, _get_github_token

    parsed = _parse_github_url(body.url)
    if not parsed:
        raise HTTPException(400, "Invalid GitHub URL")

    owner, repo, branch, path = parsed["owner"], parsed["repo"], parsed["branch"], parsed["path"]
    tenant_id = str(current_user.tenant_id) if current_user.tenant_id else None
    token = await _get_github_token(tenant_id)
    files = await _fetch_github_directory(owner, repo, path, branch, token)
    if not files:
        raise HTTPException(404, "No files found")

    # Derive folder name
    folder_name = path.rstrip("/").split("/")[-1] if path else repo

    # Write to agent workspace
    base = _agent_base_dir(agent_id)
    skill_dir = base / "skills" / folder_name
    skill_dir.mkdir(parents=True, exist_ok=True)

    written = []
    for f in files:
        file_path = (skill_dir / f["path"]).resolve()
        if not str(file_path).startswith(str(base.resolve())):
            continue
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(f["content"], encoding="utf-8")
        written.append(f["path"])

    return {
        "status": "ok",
        "folder_name": folder_name,
        "files_written": len(written),
        "files": written,
    }
