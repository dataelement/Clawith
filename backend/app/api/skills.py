"""Skills API — global skill registry CRUD."""

import asyncio
import base64
import logging
import re

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.database import async_session
from app.models.skill import Skill, SkillFile
from app.core.security import require_role, get_current_user
from app.models.user import User

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/skills", tags=["skills"])

CLAWHUB_BASE = "https://clawhub.ai/api"
GITHUB_API = "https://api.github.com"

MAX_SKILL_SIZE = 512_000  # 500 KB total limit per skill


async def _get_tenant_setting(tenant_id: str | None, key: str) -> str:
    """Resolve a tenant setting value: tenant_settings DB > empty."""
    if tenant_id:
        try:
            from app.models.tenant_setting import TenantSetting
            import uuid as _uid
            async with async_session() as db:
                result = await db.execute(
                    select(TenantSetting).where(
                        TenantSetting.tenant_id == _uid.UUID(tenant_id),
                        TenantSetting.key == key,
                    )
                )
                setting = result.scalar_one_or_none()
                if setting and setting.value.get("token"):
                    return setting.value["token"]
        except Exception:
            pass
    return ""


async def _get_github_token(tenant_id: str | None = None) -> str:
    """Resolve GitHub token from tenant settings DB."""
    return await _get_tenant_setting(tenant_id, "github_token")


async def _get_clawhub_key(tenant_id: str | None = None) -> str:
    """Resolve ClawHub API key from tenant settings DB."""
    return await _get_tenant_setting(tenant_id, "clawhub_key")


def _clawhub_headers(api_key: str) -> dict:
    """Build request headers for ClawHub API calls."""
    if api_key:
        return {"Authorization": f"Bearer {api_key}"}
    return {}


class SkillFileIn(BaseModel):
    path: str
    content: str


class SkillCreateIn(BaseModel):
    name: str
    description: str = ""
    category: str = "custom"
    icon: str = "📋"
    folder_name: str
    files: list[SkillFileIn] = []


class ClawhubInstallIn(BaseModel):
    slug: str


class UrlImportIn(BaseModel):
    url: str


# ─── Helpers ──────────────────────────────────────────


def classify_portability(content: str) -> int:
    """Classify skill portability: 1=pure prompt, 2=CLI/API, 3=OpenClaw native."""
    openclaw_markers = [
        "bash pty:", "process action:", "Clawdbot", "exec tool",
        "openclaw.json", "imessage tool", "slack tool",
    ]
    cli_markers = [
        "requires:", "bins:", 'env:', "OPENAI_API_KEY", "GITHUB_TOKEN",
        "python3", "brew ", "pip install", "npm install", "curl ",
    ]
    lower = content.lower()
    for kw in openclaw_markers:
        if kw.lower() in lower:
            return 3
    for kw in cli_markers:
        if kw.lower() in lower:
            return 2
    return 1


def _parse_skill_md_frontmatter(content: str) -> dict:
    """Extract YAML frontmatter from SKILL.md content."""
    import yaml
    match = re.match(r"^---\s*\n(.*?)\n---", content, re.DOTALL)
    if not match:
        return {}
    try:
        return yaml.safe_load(match.group(1)) or {}
    except Exception:
        return {}


def _parse_github_url(url: str) -> dict | None:
    """Parse a GitHub URL into owner/repo/branch/path components."""
    # https://github.com/{owner}/{repo}/tree/{branch}/{path}
    m = re.match(
        r"https?://github\.com/([^/]+)/([^/]+)/tree/([^/]+)/(.*?)/?$", url
    )
    if m:
        return {"owner": m.group(1), "repo": m.group(2), "branch": m.group(3), "path": m.group(4)}
    # https://github.com/{owner}/{repo}/{path} (assume main branch)
    m = re.match(
        r"https?://github\.com/([^/]+)/([^/]+?)(?:\.git)?/?$", url
    )
    if m:
        return {"owner": m.group(1), "repo": m.group(2), "branch": "main", "path": ""}
    return None


async def _fetch_github_directory(
    owner: str, repo: str, path: str, branch: str = "main",
    token: str = "",
) -> list[dict]:
    """Recursively fetch all files from a GitHub directory via API.
    Returns [{"path": relative_path, "content": text}].
    """
    _token = token
    files: list[dict] = []
    total_size = 0
    max_depth = 3  # Prevent runaway recursion
    headers = {"Authorization": f"Bearer {_token}"} if _token else {}

    async def _recurse(dir_path: str, rel_prefix: str, depth: int = 0):
        nonlocal total_size
        if depth > max_depth:
            return
        api_url = f"{GITHUB_API}/repos/{owner}/{repo}/contents/{dir_path}?ref={branch}"
        async with httpx.AsyncClient(timeout=30, headers=headers) as client:
            resp = await client.get(api_url)
            if resp.status_code == 404:
                raise HTTPException(404, f"GitHub path not found: {dir_path}")
            if resp.status_code == 403:
                raise HTTPException(429, "GitHub API rate limit exceeded. Try again later.")
            if resp.status_code != 200:
                raise HTTPException(502, f"GitHub API error: {resp.status_code}")
            items = resp.json()

        if isinstance(items, dict):
            # Single file (not a directory)
            items = [items]

        # Early guard: if at top level, check that SKILL.md exists
        if depth == 0:
            has_skill_md = any(
                i["name"].upper() == "SKILL.MD" and i["type"] == "file"
                for i in items
            )
            dir_count = sum(1 for i in items if i["type"] == "dir")
            if not has_skill_md:
                if dir_count > 5:
                    raise HTTPException(
                        400, f"This directory contains {dir_count} subdirectories but no SKILL.md. "
                             "Please provide the URL to a specific skill directory."
                    )
                raise HTTPException(400, "No SKILL.md found at the root of this directory — not a valid skill package.")

        for item in items:
            name = item["name"]
            rel = f"{rel_prefix}{name}" if rel_prefix else name

            if item["type"] == "dir":
                await _recurse(item["path"], f"{rel}/", depth + 1)
            elif item["type"] == "file":
                size = item.get("size", 0)
                total_size += size
                if total_size > MAX_SKILL_SIZE:
                    raise HTTPException(413, f"Skill exceeds size limit ({MAX_SKILL_SIZE // 1024}KB)")
                # Download file content
                async with httpx.AsyncClient(timeout=30, headers=headers) as client:
                    dl_resp = await client.get(item["url"])
                    if dl_resp.status_code == 200:
                        data = dl_resp.json()
                        content = base64.b64decode(data.get("content", "")).decode("utf-8", errors="replace")
                        files.append({"path": rel, "content": content})

    try:
        await _recurse(path, "")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(502, f"Failed to fetch files from GitHub: {e}")
    return files


async def _save_skill_to_db(
    folder_name: str, name: str, description: str,
    category: str, icon: str, files: list[dict],
    source_url: str | None = None,
    tenant_id: str | None = None,
) -> dict:
    """Create a Skill + SkillFile records in the database."""
    import uuid as _uuid
    async with async_session() as db:
        # Check for folder_name conflict (scoped by tenant)
        conflict_q = select(Skill).where(Skill.folder_name == folder_name)
        if tenant_id:
            conflict_q = conflict_q.where(Skill.tenant_id == _uuid.UUID(tenant_id))
        else:
            conflict_q = conflict_q.where(Skill.tenant_id == None)
        existing = await db.execute(conflict_q)
        if existing.scalar_one_or_none():
            raise HTTPException(
                409, f"A skill with folder name '{folder_name}' already exists. "
                     "Delete it first or use a different name."
            )

        skill = Skill(
            name=name,
            description=description,
            category=category,
            icon=icon,
            folder_name=folder_name,
            is_builtin=False,
            tenant_id=_uuid.UUID(tenant_id) if tenant_id else None,
        )
        db.add(skill)
        await db.flush()

        for f in files:
            # PostgreSQL text columns cannot store null bytes
            content = f["content"].replace("\x00", "") if f.get("content") else ""
            db.add(SkillFile(skill_id=skill.id, path=f["path"], content=content))

        await db.commit()
        return {"id": str(skill.id), "name": skill.name, "folder_name": skill.folder_name}


# ─── ClawHub Integration ─────────────────────────────


@router.get("/clawhub/search")
async def search_clawhub(q: str, current_user: User = Depends(get_current_user)):
    """Proxy search requests to the ClawHub API."""
    tenant_id = str(current_user.tenant_id) if current_user.tenant_id else None
    api_key = await _get_clawhub_key(tenant_id)
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(f"{CLAWHUB_BASE}/search", params={"q": q}, headers=_clawhub_headers(api_key))
        if resp.status_code != 200:
            raise HTTPException(502, f"ClawHub search failed: {resp.status_code}")
        data = resp.json()
    results = data.get("results", [])
    return [
        {
            "slug": r.get("slug"),
            "displayName": r.get("displayName"),
            "summary": r.get("summary"),
            "score": r.get("score"),
        }
        for r in results
    ]


@router.get("/clawhub/detail/{slug}")
async def clawhub_detail(slug: str, current_user: User = Depends(get_current_user)):
    """Fetch full metadata for a skill from ClawHub."""
    tenant_id = str(current_user.tenant_id) if current_user.tenant_id else None
    api_key = await _get_clawhub_key(tenant_id)
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(f"{CLAWHUB_BASE}/v1/skills/{slug}", headers=_clawhub_headers(api_key))
            if resp.status_code == 404:
                raise HTTPException(404, f"Skill '{slug}' not found on ClawHub")
            if resp.status_code == 429:
                raise HTTPException(429, "ClawHub rate limit exceeded. Please wait a moment and try again.")
            if resp.status_code != 200:
                raise HTTPException(502, f"ClawHub API error: {resp.status_code}")
            return resp.json()
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(502, f"Failed to connect to ClawHub: {e}")


@router.post("/clawhub/install")
async def install_from_clawhub(body: ClawhubInstallIn, current_user: User = Depends(get_current_user)):
    """Install a skill from ClawHub into the global registry."""
    # Resolve tenant GitHub token
    tenant_id = str(current_user.tenant_id) if current_user.tenant_id else None
    token = await _get_github_token(tenant_id)
    slug = body.slug

    # 1. Fetch metadata from ClawHub (with retry for rate limits)
    api_key = await _get_clawhub_key(tenant_id)
    ch_headers = _clawhub_headers(api_key)
    meta = None
    for attempt in range(3):
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.get(f"{CLAWHUB_BASE}/v1/skills/{slug}", headers=ch_headers)
                if resp.status_code == 404:
                    raise HTTPException(404, f"Skill '{slug}' not found on ClawHub")
                if resp.status_code == 429:
                    if attempt < 2:
                        await asyncio.sleep(1 + attempt)  # 1s, 2s backoff
                        continue
                    raise HTTPException(429, "ClawHub rate limit exceeded. Please wait a moment and try again.")
                if resp.status_code != 200:
                    raise HTTPException(502, f"ClawHub API error: {resp.status_code}")
                meta = resp.json()
                break
        except HTTPException:
            raise
        except Exception as e:
            if attempt < 2:
                await asyncio.sleep(1)
                continue
            raise HTTPException(502, f"Failed to connect to ClawHub: {e}")

    skill_info = meta.get("skill", {})
    owner_info = meta.get("owner", {})
    moderation = meta.get("moderation") or {}

    handle = owner_info.get("handle", "").lower()
    if not handle:
        raise HTTPException(400, "Could not determine skill owner handle from ClawHub")

    # 2. Build result with moderation warning
    is_suspicious = moderation.get("isSuspicious", False)
    moderation_summary = moderation.get("summary", "")

    # 3. Fetch files from GitHub archive
    github_path = f"skills/{handle}/{slug}"
    try:
        files = await _fetch_github_directory("openclaw", "skills", github_path, "main", token=token)
    except HTTPException as e:
        if e.status_code == 404:
            raise HTTPException(
                404, f"Skill files not found in GitHub archive at {github_path}. "
                     "Try importing via URL instead."
            )
        raise

    if not files:
        raise HTTPException(404, "No files found in the skill directory")

    # 4. Extract name/description from SKILL.md
    skill_md = next((f for f in files if f["path"].upper() == "SKILL.MD"), None)
    if not skill_md:
        raise HTTPException(400, "No SKILL.md found — not a valid skill package")

    frontmatter = _parse_skill_md_frontmatter(skill_md["content"])
    name = frontmatter.get("name", skill_info.get("displayName", slug))
    description = frontmatter.get("description", skill_info.get("summary", ""))

    # 5. Classify portability tier
    tier = classify_portability(skill_md["content"])
    tier_labels = {1: "clawhub-tier1", 2: "clawhub-tier2", 3: "clawhub-tier3"}
    has_scripts = any("/" in f["path"] for f in files if f["path"] != "SKILL.md")

    # 6. Save to DB
    result = await _save_skill_to_db(
        folder_name=slug,
        name=name,
        description=description,
        category=tier_labels.get(tier, "clawhub"),
        icon="",
        files=files,
        source_url=f"https://clawhub.ai/skills/{slug}",
        tenant_id=tenant_id,
    )

    result["tier"] = tier
    result["is_suspicious"] = is_suspicious
    result["moderation_summary"] = moderation_summary
    result["has_scripts"] = has_scripts
    result["file_count"] = len(files)
    result["source"] = "clawhub"
    return result


@router.post("/import-from-url")
async def import_from_url(body: UrlImportIn, current_user: User = Depends(get_current_user)):
    """Import a skill from any GitHub URL into the global registry."""
    tenant_id = str(current_user.tenant_id) if current_user.tenant_id else None
    token = await _get_github_token(tenant_id)
    parsed = _parse_github_url(body.url)
    if not parsed:
        raise HTTPException(400, "Invalid GitHub URL. Expected format: https://github.com/{owner}/{repo}/tree/{branch}/{path}")

    owner, repo, branch, path = parsed["owner"], parsed["repo"], parsed["branch"], parsed["path"]

    # Fetch files
    files = await _fetch_github_directory(owner, repo, path, branch, token=token)
    if not files:
        raise HTTPException(404, "No files found at the specified path")

    # Validate SKILL.md exists
    skill_md = next((f for f in files if f["path"].upper() == "SKILL.MD"), None)
    if not skill_md:
        raise HTTPException(400, "No SKILL.md found at this URL — not a valid skill package")

    frontmatter = _parse_skill_md_frontmatter(skill_md["content"])
    name = frontmatter.get("name", path.rstrip("/").split("/")[-1] if path else repo)
    description = frontmatter.get("description", "")

    # Derive folder_name from the last path segment
    folder_name = path.rstrip("/").split("/")[-1] if path else repo

    tier = classify_portability(skill_md["content"])
    tier_labels = {1: "url-import-tier1", 2: "url-import-tier2", 3: "url-import-tier3"}

    result = await _save_skill_to_db(
        folder_name=folder_name,
        name=name,
        description=description,
        category=tier_labels.get(tier, "url-import"),
        icon="",
        files=files,
        source_url=body.url,
        tenant_id=tenant_id,
    )

    result["tier"] = tier
    result["file_count"] = len(files)
    result["source"] = "url"
    return result


@router.post("/import-from-url/preview")
async def preview_url_import(body: UrlImportIn, current_user: User = Depends(get_current_user)):
    """Preview what will be imported from a GitHub URL without saving."""
    tenant_id = str(current_user.tenant_id) if current_user.tenant_id else None
    token = await _get_github_token(tenant_id)
    parsed = _parse_github_url(body.url)
    if not parsed:
        raise HTTPException(400, "Invalid GitHub URL format")

    owner, repo, branch, path = parsed["owner"], parsed["repo"], parsed["branch"], parsed["path"]

    files = await _fetch_github_directory(owner, repo, path, branch, token=token)
    if not files:
        raise HTTPException(404, "No files found at the specified path")

    skill_md = next((f for f in files if f["path"].upper() == "SKILL.MD"), None)
    if not skill_md:
        raise HTTPException(400, "No SKILL.md found — not a valid skill package")

    frontmatter = _parse_skill_md_frontmatter(skill_md["content"])
    tier = classify_portability(skill_md["content"])

    return {
        "name": frontmatter.get("name", path.rstrip("/").split("/")[-1] if path else repo),
        "description": frontmatter.get("description", ""),
        "tier": tier,
        "files": [{"path": f["path"], "size": len(f["content"])} for f in files],
        "total_size": sum(len(f["content"]) for f in files),
        "has_scripts": any("/" in f["path"] for f in files if f["path"] != "SKILL.md"),
    }


# ─── Standard CRUD ────────────────────────────────────


@router.get("/")
async def list_skills(current_user: User = Depends(get_current_user)):
    """List global skills scoped by tenant (builtin + tenant-specific)."""
    import uuid as _uuid
    from sqlalchemy import or_ as _or
    tenant_id = str(current_user.tenant_id) if current_user.tenant_id else None
    async with async_session() as db:
        query = select(Skill).order_by(Skill.name)
        # Scope by tenant: show builtin (tenant_id is NULL) + tenant-specific skills
        if tenant_id:
            query = query.where(_or(Skill.tenant_id == None, Skill.tenant_id == _uuid.UUID(tenant_id)))
        result = await db.execute(query)
        skills = result.scalars().all()
        return [
            {
                "id": str(s.id),
                "name": s.name,
                "description": s.description,
                "category": s.category,
                "icon": s.icon,
                "folder_name": s.folder_name,
                "is_builtin": s.is_builtin,
                "is_default": s.is_default,
                "created_at": s.created_at.isoformat() if s.created_at else None,
            }
            for s in skills
        ]


@router.get("/{skill_id}")
async def get_skill(skill_id: str):
    """Get a skill with its files."""
    async with async_session() as db:
        result = await db.execute(
            select(Skill).where(Skill.id == skill_id).options(selectinload(Skill.files))
        )
        skill = result.scalar_one_or_none()
        if not skill:
            raise HTTPException(404, "Skill not found")
        return {
            "id": str(skill.id),
            "name": skill.name,
            "description": skill.description,
            "category": skill.category,
            "icon": skill.icon,
            "folder_name": skill.folder_name,
            "is_builtin": skill.is_builtin,
            "files": [
                {"path": f.path, "content": f.content}
                for f in skill.files
            ],
        }


@router.post("/")
async def create_skill(body: SkillCreateIn, _=Depends(require_role("platform_admin"))):
    """Create a custom skill."""
    async with async_session() as db:
        skill = Skill(
            name=body.name,
            description=body.description,
            category=body.category,
            icon=body.icon,
            folder_name=body.folder_name,
            is_builtin=False,
        )
        db.add(skill)
        await db.flush()

        if not body.files:
            # Auto-create a SKILL.md template
            db.add(SkillFile(
                skill_id=skill.id,
                path="SKILL.md",
                content=f"---\nname: {body.name}\ndescription: {body.description}\n---\n\n# {body.name}\n\n## Overview\n{body.description}\n",
            ))
        else:
            for f in body.files:
                db.add(SkillFile(skill_id=skill.id, path=f.path, content=f.content))

        await db.commit()
        return {"id": str(skill.id), "name": skill.name}


class SkillUpdateIn(BaseModel):
    name: str | None = None
    description: str | None = None
    category: str | None = None
    icon: str | None = None
    files: list[SkillFileIn] | None = None


@router.put("/{skill_id}")
async def update_skill(skill_id: str, body: SkillUpdateIn, _=Depends(require_role("platform_admin"))):
    """Update a skill's metadata and/or files."""
    async with async_session() as db:
        result = await db.execute(
            select(Skill).where(Skill.id == skill_id).options(selectinload(Skill.files))
        )
        skill = result.scalar_one_or_none()
        if not skill:
            raise HTTPException(404, "Skill not found")

        if body.name is not None:
            skill.name = body.name
        if body.description is not None:
            skill.description = body.description
        if body.category is not None:
            skill.category = body.category
        if body.icon is not None:
            skill.icon = body.icon

        # Replace files if provided
        if body.files is not None:
            for f in skill.files:
                await db.delete(f)
            await db.flush()
            for f in body.files:
                db.add(SkillFile(skill_id=skill.id, path=f.path, content=f.content))

        await db.commit()
        return {"id": str(skill.id), "name": skill.name}


@router.delete("/{skill_id}")
async def delete_skill(skill_id: str, _=Depends(require_role("platform_admin"))):
    """Delete a skill (not builtin)."""
    async with async_session() as db:
        result = await db.execute(select(Skill).where(Skill.id == skill_id))
        skill = result.scalar_one_or_none()
        if not skill:
            raise HTTPException(404, "Skill not found")
        if skill.is_builtin:
            raise HTTPException(400, "Cannot delete builtin skill")
        await db.delete(skill)
        await db.commit()
        return {"ok": True}


# ─── Tenant GitHub Token Settings ───────────────────────────


class SkillSettingsIn(BaseModel):
    github_token: str | None = None
    clawhub_key: str | None = None


async def _upsert_tenant_setting(tenant_id, key: str, value: str):
    """Helper to upsert a tenant setting."""
    from app.models.tenant_setting import TenantSetting
    async with async_session() as db:
        result = await db.execute(
            select(TenantSetting).where(
                TenantSetting.tenant_id == tenant_id,
                TenantSetting.key == key,
            )
        )
        existing = result.scalar_one_or_none()
        if existing:
            existing.value = {"token": value}
        else:
            db.add(TenantSetting(
                tenant_id=tenant_id,
                key=key,
                value={"token": value},
            ))
        await db.commit()


def _mask_token(token: str) -> str:
    if token and len(token) > 8:
        return f"{token[:4]}...{token[-4:]}"
    return "****" if token else ""


@router.get("/settings/token")
async def get_skill_token_status(
    current_user=Depends(require_role("platform_admin")),
):
    """Check if GitHub token and ClawHub key are configured for this tenant."""
    tenant_id = str(current_user.tenant_id) if current_user.tenant_id else None
    gh_token = await _get_github_token(tenant_id)
    ch_key = await _get_clawhub_key(tenant_id)
    return {
        "configured": bool(gh_token),
        "source": "tenant" if tenant_id else "env",
        "masked": _mask_token(gh_token),
        "clawhub_configured": bool(ch_key),
        "clawhub_masked": _mask_token(ch_key),
    }


@router.put("/settings/token")
async def set_skill_token(
    body: SkillSettingsIn,
    current_user=Depends(require_role("platform_admin")),
):
    """Save GitHub token and/or ClawHub key for this tenant."""
    if not current_user.tenant_id:
        raise HTTPException(400, "No tenant associated")

    if body.github_token is not None:
        await _upsert_tenant_setting(current_user.tenant_id, "github_token", body.github_token)
    if body.clawhub_key is not None:
        await _upsert_tenant_setting(current_user.tenant_id, "clawhub_key", body.clawhub_key)
    return {"ok": True}


# ─── Path-based browse endpoints for FileBrowser ───────────


@router.get("/browse/list")
async def browse_list(path: str = "", current_user: User = Depends(get_current_user)):
    """List skill folders (root) or files/subdirs within a skill folder."""
    import uuid as _uuid
    from sqlalchemy import or_ as _or
    tenant_id = str(current_user.tenant_id) if current_user.tenant_id else None
    async with async_session() as db:
        if not path or path == "/":
            # Root: list all skill folders (scoped by tenant)
            query = select(Skill).order_by(Skill.name)
            if tenant_id:
                query = query.where(_or(Skill.tenant_id == None, Skill.tenant_id == _uuid.UUID(tenant_id)))
            result = await db.execute(query)
            skills = result.scalars().all()
            return [
                {"name": s.folder_name, "path": s.folder_name, "is_dir": True, "size": 0}
                for s in skills
            ]

        # Inside a skill folder — resolve the skill and relative subpath
        clean = path.strip("/")
        folder = clean.split("/")[0]
        # Resolve skill folder scoped by tenant
        skill_q = select(Skill).where(Skill.folder_name == folder).options(selectinload(Skill.files))
        if tenant_id:
            skill_q = skill_q.where(_or(Skill.tenant_id == None, Skill.tenant_id == _uuid.UUID(tenant_id)))
        result = await db.execute(skill_q)
        skill = result.scalar_one_or_none()
        if not skill:
            return []

        # Calculate the relative prefix within the skill (empty = skill root)
        sub = clean[len(folder):].strip("/")  # e.g. "" or "scripts" or "scripts/sub"

        items = []
        seen_dirs: set[str] = set()
        for f in skill.files:
            if sub:
                # Only files that start with this sub prefix
                if not f.path.startswith(sub + "/"):
                    continue
                remainder = f.path[len(sub) + 1:]  # strip "scripts/" prefix
            else:
                remainder = f.path

            if "/" in remainder:
                # This file is in a subdirectory — show the directory
                dir_name = remainder.split("/")[0]
                if dir_name not in seen_dirs:
                    seen_dirs.add(dir_name)
                    dir_path = f"{folder}/{sub}/{dir_name}" if sub else f"{folder}/{dir_name}"
                    items.append({"name": dir_name, "path": dir_path, "is_dir": True, "size": 0})
            else:
                # Direct child file
                file_path = f"{folder}/{f.path}"
                items.append({"name": remainder, "path": file_path, "is_dir": False, "size": len(f.content.encode())})

        return items


@router.get("/browse/read")
async def browse_read(path: str, current_user: User = Depends(get_current_user)):
    """Read a file from a skill folder."""
    import uuid as _uuid
    from sqlalchemy import or_ as _or
    tenant_id = str(current_user.tenant_id) if current_user.tenant_id else None
    parts = path.strip("/").split("/", 1)
    if len(parts) < 2:
        raise HTTPException(400, "Path must include folder and file")
    folder, file_path = parts
    async with async_session() as db:
        skill_q = select(Skill).where(Skill.folder_name == folder).options(selectinload(Skill.files))
        if tenant_id:
            skill_q = skill_q.where(_or(Skill.tenant_id == None, Skill.tenant_id == _uuid.UUID(tenant_id)))
        result = await db.execute(skill_q)
        skill = result.scalar_one_or_none()
        if not skill:
            raise HTTPException(404, "Skill not found")
        for f in skill.files:
            if f.path == file_path:
                return {"content": f.content}
        raise HTTPException(404, "File not found")


class BrowseWriteIn(BaseModel):
    path: str
    content: str


@router.put("/browse/write")
async def browse_write(body: BrowseWriteIn, current_user: User = Depends(require_role("platform_admin"))):
    """Write a file in a skill folder. Creates the skill if the folder doesn't exist."""
    import uuid as _uuid
    from sqlalchemy import or_ as _or
    tenant_id = str(current_user.tenant_id) if current_user.tenant_id else None
    parts = body.path.strip("/").split("/", 1)
    if len(parts) < 2:
        raise HTTPException(400, "Path must include folder and file")
    folder, file_path = parts
    async with async_session() as db:
        skill_q = select(Skill).where(Skill.folder_name == folder).options(selectinload(Skill.files))
        if tenant_id:
            skill_q = skill_q.where(_or(Skill.tenant_id == None, Skill.tenant_id == _uuid.UUID(tenant_id)))
        result = await db.execute(skill_q)
        skill = result.scalar_one_or_none()
        if not skill:
            # Auto-create skill from folder name, scoped to tenant
            skill = Skill(
                name=folder.replace("-", " ").title(),
                description="",
                category="custom",
                icon="--",
                folder_name=folder,
                is_builtin=False,
                tenant_id=current_user.tenant_id,
            )
            db.add(skill)
            await db.flush()

        # Upsert file
        existing = None
        for f in skill.files:
            if f.path == file_path:
                existing = f
                break
        if existing:
            existing.content = body.content
        else:
            db.add(SkillFile(skill_id=skill.id, path=file_path, content=body.content))
        await db.commit()
        return {"ok": True}


@router.delete("/browse/delete")
async def browse_delete(path: str, current_user: User = Depends(require_role("platform_admin"))):
    """Delete a file or an entire skill folder."""
    import uuid as _uuid
    from sqlalchemy import or_ as _or
    tenant_id = str(current_user.tenant_id) if current_user.tenant_id else None
    parts = path.strip("/").split("/", 1)
    folder = parts[0]
    async with async_session() as db:
        skill_q = select(Skill).where(Skill.folder_name == folder).options(selectinload(Skill.files))
        if tenant_id:
            skill_q = skill_q.where(_or(Skill.tenant_id == None, Skill.tenant_id == _uuid.UUID(tenant_id)))
        result = await db.execute(skill_q)
        skill = result.scalar_one_or_none()
        if not skill:
            raise HTTPException(404, "Skill not found")
        if skill.is_builtin and len(parts) == 1:
            raise HTTPException(400, "Cannot delete builtin skill")

        if len(parts) == 1:
            # Delete entire skill
            await db.delete(skill)
        else:
            # Delete specific file
            file_path = parts[1]
            for f in skill.files:
                if f.path == file_path:
                    await db.delete(f)
                    break
        await db.commit()
        return {"ok": True}
