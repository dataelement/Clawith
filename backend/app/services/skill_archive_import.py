from __future__ import annotations

import hashlib
import io
import zipfile
from pathlib import Path

from fastapi import HTTPException

MAX_SKILL_ARCHIVE_SIZE = 50 * 1024 * 1024
MAX_SKILL_ARCHIVE_FILES = 1000
MAX_SKILL_UNCOMPRESSED = 500 * 1024 * 1024


def _normalize_member_path(member: str) -> str:
    if not member:
        raise HTTPException(status_code=400, detail="Invalid archive path")

    normalized_member = member.replace("\\", "/")
    path = Path(normalized_member)
    if normalized_member.startswith("/") or path.is_absolute() or ".." in path.parts:
        raise HTTPException(status_code=400, detail="Invalid archive path")

    return str(path).replace("\\", "/")


def _shared_root_folder(paths: list[str]) -> str:
    if not paths or any("/" not in path for path in paths):
        return ""

    roots = {path.split("/", 1)[0] for path in paths}
    return roots.pop() if len(roots) == 1 else ""


def inspect_skill_archive(data: bytes, *, target_folder: str) -> dict:
    if len(data) > MAX_SKILL_ARCHIVE_SIZE:
        raise HTTPException(status_code=400, detail="Skill archive too large")

    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise HTTPException(status_code=400, detail="Invalid skill archive") from exc

    total_uncompressed = sum(item.file_size for item in zf.infolist())
    if total_uncompressed > MAX_SKILL_UNCOMPRESSED:
        raise HTTPException(status_code=400, detail="Skill archive uncompressed size too large")

    files: dict[str, str] = {}
    with zf:
        members = [item for item in zf.infolist() if not item.is_dir()]
        if len(members) > MAX_SKILL_ARCHIVE_FILES:
            raise HTTPException(status_code=400, detail="Too many files in skill archive")

        raw_paths = [_normalize_member_path(item.filename) for item in members]
        strip_root = _shared_root_folder(raw_paths)

        for item, raw_path in zip(members, raw_paths, strict=False):
            rel_path = raw_path
            if strip_root:
                rel_path = rel_path[len(strip_root) + 1 :]
            rel_path = rel_path.strip("/")
            if not rel_path:
                continue
            files[rel_path] = zf.read(item).decode("utf-8", errors="replace")

    if "SKILL.md" not in files:
        raise HTTPException(status_code=400, detail="Uploaded folder must contain a root SKILL.md")

    digest = hashlib.sha256()
    for path in sorted(files):
        digest.update(path.encode("utf-8"))
        digest.update(b"\0")
        digest.update(files[path].encode("utf-8"))
        digest.update(b"\0")

    diff = diff_skill_manifests(files, {})
    return {
        "target_folder": target_folder,
        "files": files,
        "total_files": len(files),
        "digest": digest.hexdigest(),
        "diff": diff,
    }


def diff_skill_manifests(uploaded: dict[str, str], existing: dict[str, str]) -> dict[str, list[str]]:
    uploaded_paths = set(uploaded)
    existing_paths = set(existing)
    added = sorted(uploaded_paths - existing_paths)
    deleted = sorted(existing_paths - uploaded_paths)
    changed = sorted(path for path in uploaded_paths & existing_paths if uploaded[path] != existing[path])
    return {"added": added, "changed": changed, "deleted": deleted}
