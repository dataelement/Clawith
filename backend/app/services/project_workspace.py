"""Filesystem service for Project workspaces.

Each project owns a directory at PROJECT_WORKSPACE_DIR/{project_id}/ containing:
- BRIEF.md  — the project's brief, injected into agent system prompts
- Any files uploaded by users or written by agents at work

This service is strictly filesystem I/O. Database updates to `project_files`
are performed by the API layer so that auth + DB updates stay atomic.
"""

from __future__ import annotations

import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from app.config import get_settings


BRIEF_FILENAME = "BRIEF.md"
"""Reserved filename for the project brief. Filtered out of generic file listings."""

BRIEF_HISTORY_DIRNAME = ".brief-history"
"""Subdirectory under each project workspace that keeps point-in-time snapshots
of BRIEF.md, one per save. Enables undo / audit of brief changes.
"""

BRIEF_HISTORY_INDEX = "INDEX.jsonl"
"""One JSONL row per snapshot. Fields: ts, actor_type, actor_id, bytes, filename."""

_MAX_BRIEF_HISTORY_ENTRIES = 100
"""Keep the newest N snapshots per project; older ones are pruned on save."""

ConflictMode = Literal["replace", "keep_both", "abort"]


@dataclass
class PhysicalFile:
    """An entry on disk inside the project workspace (file or directory).

    The name kept for backward compatibility — entries can now be directories
    too (`is_dir=True`), in which case `size_bytes`/`mime_type` are zero/empty.
    """

    filename: str
    physical_path: str       # path relative to project workspace root
    size_bytes: int
    mime_type: str
    is_dir: bool = False
    # Filesystem mtime as ISO 8601 string. Authoritative timestamp for "when
    # was this file last changed" — DB updated_at is the row's last write time
    # (set on reconciliation / upload metadata changes), not the file content's.
    mtime_iso: str = ""


@dataclass
class SaveResult:
    """Outcome of save_upload."""

    filename: str            # may differ from requested filename when conflict=keep_both
    physical_path: str
    size_bytes: int
    replaced_existing: bool  # True when conflict=replace overwrote a prior file


_DEFAULT_BRIEF_TEMPLATE = """\
# {name}

> 这是项目说明文件 BRIEF.md。所有以 `>` 开头的引用块都是填写指引，写完后可以整段删除。

## 目标 / Goal

> 这个项目最终要交付什么？尽量具体、可验收。
> 例如：在两周内产出 10 张适合 Instagram 发布的品牌海报，含英文/中文双语版本。



## 背景 / Context

> 为什么做、谁在用、已有的相关决定 / 参考资料。
> 例如：10 月进入美国市场，品牌 tagline 是 "Build with clarity"，视觉风格参考 Linear 官网。



## 限制条件 / Constraints

> 不能越界的红线：截止时间、合规、预算、风格约束等。
> 例如：
> - 截止日期：2026-05-15
> - 不能使用 AI 生成的人脸
> - 文案必须经英文母语审校
> - 素材必须包含 1080×1080 和 1920×1080 两种尺寸
"""


# Rough char budget for a token at the project BRIEF level. English / Chinese
# average out to ~3.5 chars per token for markdown; we round to 3.5 and keep
# a small safety buffer. This keeps us from importing a full tokenizer for a
# non-critical truncation.
_CHARS_PER_TOKEN_ESTIMATE = 3.5


def _guess_mime(filename: str) -> str:
    """Very small MIME lookup — the browser will correct it on download anyway."""
    ext = filename.lower().rsplit(".", 1)
    if len(ext) != 2:
        return ""
    suffix = ext[1]
    return {
        "pdf": "application/pdf",
        "png": "image/png",
        "jpg": "image/jpeg", "jpeg": "image/jpeg",
        "gif": "image/gif", "webp": "image/webp", "svg": "image/svg+xml",
        "md": "text/markdown", "txt": "text/plain",
        "json": "application/json", "csv": "text/csv",
        "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    }.get(suffix, f"application/{suffix}")


def _mtime_iso(st_mtime: float) -> str:
    """Format a stat() st_mtime float as a UTC ISO-8601 string."""
    from datetime import datetime, timezone
    return datetime.fromtimestamp(st_mtime, tz=timezone.utc).isoformat()


def _next_unique_name(root: Path, requested: str) -> str:
    """Return a filename like 'brand (1).pdf' that does not yet exist on disk."""
    if not (root / requested).exists():
        return requested
    stem, sep, ext = requested.rpartition(".")
    if sep == "":
        # No extension
        stem, ext = requested, ""
        sep = ""
    i = 1
    while True:
        candidate = f"{stem} ({i}){sep}{ext}"
        if not (root / candidate).exists():
            return candidate
        i += 1


class ProjectWorkspaceService:
    """Stateless helper wrapping filesystem operations under PROJECT_WORKSPACE_DIR."""

    def __init__(self) -> None:
        self._root = Path(get_settings().PROJECT_WORKSPACE_DIR)

    # ── paths ────────────────────────────────────────────────────────────
    def get_root(self, project_id: uuid.UUID) -> Path:
        """Return the root directory for a project workspace (does not create)."""
        return self._root / str(project_id)

    # ── lifecycle ────────────────────────────────────────────────────────
    def ensure_initialized(self, project_id: uuid.UUID, project_name: str) -> None:
        """Create the workspace directory and seed BRIEF.md if absent. Idempotent."""
        root = self.get_root(project_id)
        root.mkdir(parents=True, exist_ok=True)
        brief = root / BRIEF_FILENAME
        if not brief.exists():
            brief.write_text(
                _DEFAULT_BRIEF_TEMPLATE.format(name=project_name),
                encoding="utf-8",
            )

    def archive_workspace(self, project_id: uuid.UUID) -> None:
        """MVP archive is soft (no filesystem mutation). The API enforces read-only."""
        return None

    def destroy_workspace(self, project_id: uuid.UUID) -> None:
        """Delete the entire project workspace directory. Currently unused (MVP has no hard delete)."""
        root = self.get_root(project_id)
        if root.exists():
            shutil.rmtree(root)

    # ── Agent ↔ Project bridge ───────────────────────────────────────────
    def ensure_agent_project_symlink(
        self, agent_id: uuid.UUID, project_id: uuid.UUID
    ) -> tuple[Path, str]:
        """Create (idempotently) a symlink at the agent's workspace root pointing
        at this project's shared workspace.

        Layout:
          {AGENT_DATA_DIR}/{agent_id}/.project/{project_id}  →  {PROJECT_WORKSPACE_DIR}/{project_id}

        This matches the path the agent actually uses (`.project/{project_id}/...`)
        because the agent's filesystem-tool root (`ws`) is `{AGENT_DATA_DIR}/{agent_id}/`,
        not the `workspace/` subdirectory. Earlier versions placed the symlink under
        `workspace/.project/{pid}` which the agent never reached — agents writing to
        `.project/<pid>/foo.md` ended up creating a fake real directory at
        `{AGENT_DATA_DIR}/{agent_id}/.project/<pid>/`, so project chat writes
        accumulated as private copies instead of going to the shared workspace.

        This function self-heals that condition:
        - Removes the legacy stale symlink at `workspace/.project/{pid}` if present.
        - If the target location holds a *real* directory (legacy fake), migrates
          its contents into the shared project workspace (collision-safe with a
          `-from-<agent_short>` suffix), then replaces it with a symlink.
        - If the symlink already points at the right target, returns immediately.

        Returns (absolute_symlink_path, relative_path_from_agent_workspace).
        """
        import logging

        log = logging.getLogger(__name__)

        agent_data_dir = Path(get_settings().AGENT_DATA_DIR)
        agent_root = agent_data_dir / str(agent_id)
        link_path = agent_root / ".project" / str(project_id)
        rel_path = f".project/{project_id}"
        target = self.get_root(project_id)

        target.mkdir(parents=True, exist_ok=True)
        link_path.parent.mkdir(parents=True, exist_ok=True)

        # 1. Sweep the legacy stale symlink at workspace/.project/<pid> (it was
        #    never actually used by the agent, but it's noise on disk).
        legacy_link = agent_root / "workspace" / ".project" / str(project_id)
        if legacy_link.is_symlink():
            try:
                legacy_link.unlink()
            except OSError as e:
                log.warning(f"[project-workspace] could not remove legacy symlink {legacy_link}: {e}")
        # Clean up empty parent legacy dir
        legacy_parent = agent_root / "workspace" / ".project"
        if legacy_parent.exists() and not any(legacy_parent.iterdir()):
            try:
                legacy_parent.rmdir()
            except OSError:
                pass

        # 2. If link_path is already a symlink and points at the right target, done.
        if link_path.is_symlink():
            try:
                resolved = link_path.resolve(strict=False)
                if resolved == target.resolve(strict=False):
                    return link_path, rel_path
            except OSError:
                pass
            # Wrong target → drop and re-create
            try:
                link_path.unlink()
            except OSError as e:
                log.warning(f"[project-workspace] could not unlink stale symlink {link_path}: {e}")

        # 3. If link_path is a *real* directory (legacy fake from before this fix),
        #    migrate its contents into the shared workspace then remove it.
        if link_path.exists() and not link_path.is_symlink() and link_path.is_dir():
            try:
                self._migrate_legacy_project_dir(link_path, target, agent_id)
                # After migration the dir should be empty; rmdir it.
                # (It might still contain files we couldn't move — leave & log.)
                if not any(link_path.iterdir()):
                    link_path.rmdir()
                else:
                    log.warning(
                        f"[project-workspace] legacy dir {link_path} not empty after migration; "
                        f"leaving in place (agent's project view may be stale until cleared)"
                    )
                    return link_path, rel_path
            except Exception as e:  # pragma: no cover — defensive
                log.warning(f"[project-workspace] legacy migration failed for {link_path}: {e}")
                return link_path, rel_path

        # 4. Create the symlink.
        try:
            link_path.symlink_to(target, target_is_directory=True)
        except OSError as e:
            log.warning(
                f"[project-workspace] failed to create symlink {link_path} -> {target}: {e}"
            )

        return link_path, rel_path

    def _migrate_legacy_project_dir(
        self, legacy_dir: Path, shared_root: Path, agent_id: uuid.UUID,
    ) -> None:
        """Move all files from a legacy fake `.project/<pid>/` real directory in
        an agent's workspace into the actual shared project workspace.

        On filename conflict (another agent's copy or a user upload of the same
        name already exists), append `-from-<agent_short>` and retry; numeric
        suffix if even that collides. Sub-directories are preserved (moved as a
        unit).

        Idempotent: an already-migrated dir is empty and this is a no-op.
        """
        import logging

        log = logging.getLogger(__name__)
        agent_short = str(agent_id)[:8]

        for src in list(legacy_dir.iterdir()):
            dst = shared_root / src.name
            if not dst.exists():
                try:
                    shutil.move(str(src), str(dst))
                except OSError as e:
                    log.warning(f"[project-workspace] migrate move {src} -> {dst} failed: {e}")
                continue
            # Conflict — derive a non-clashing name
            if src.is_dir():
                stem, ext = src.name, ""
            else:
                if "." in src.name and not src.name.startswith("."):
                    stem, _, ext = src.name.rpartition(".")
                    ext = "." + ext
                else:
                    stem, ext = src.name, ""
            candidate = shared_root / f"{stem}-from-{agent_short}{ext}"
            n = 1
            while candidate.exists():
                n += 1
                candidate = shared_root / f"{stem}-from-{agent_short}-{n}{ext}"
            try:
                shutil.move(str(src), str(candidate))
                log.info(
                    f"[project-workspace] migrated {src.name} -> {candidate.name} "
                    f"(name conflict resolved with -from-{agent_short} suffix)"
                )
            except OSError as e:
                log.warning(f"[project-workspace] migrate move {src} -> {candidate} failed: {e}")

    # ── brief ────────────────────────────────────────────────────────────
    def read_brief(self, project_id: uuid.UUID) -> str:
        path = self.get_root(project_id) / BRIEF_FILENAME
        if not path.exists():
            return ""
        return path.read_text(encoding="utf-8", errors="replace")

    def write_brief(
        self,
        project_id: uuid.UUID,
        content: str,
        *,
        actor_type: str = "user",
        actor_id: str | None = None,
    ) -> None:
        """Write BRIEF.md, snapshotting the previous content first so edits can be undone.

        Snapshots go to .brief-history/{iso-ts}.md plus one JSONL row in INDEX.jsonl.
        """
        root = self.get_root(project_id)
        root.mkdir(parents=True, exist_ok=True)
        brief_path = root / BRIEF_FILENAME

        # 1. Snapshot the current content before overwriting (skip if identical or empty)
        if brief_path.exists():
            try:
                prev = brief_path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                prev = ""
            if prev and prev != content:
                self._snapshot_brief(project_id, prev, actor_type=actor_type, actor_id=actor_id)

        # 2. Overwrite
        brief_path.write_text(content, encoding="utf-8")

    # ── Brief history ────────────────────────────────────────────────────
    def _history_dir(self, project_id: uuid.UUID) -> Path:
        return self.get_root(project_id) / BRIEF_HISTORY_DIRNAME

    def _snapshot_brief(
        self, project_id: uuid.UUID, content: str, *, actor_type: str, actor_id: str | None
    ) -> None:
        """Save a snapshot of the previous brief content to .brief-history/.

        Filename uses a URL-safe ISO-8601 UTC timestamp plus a short random suffix
        to guarantee uniqueness if two saves happen in the same millisecond.
        """
        import json
        import secrets
        from datetime import datetime, timezone

        hist = self._history_dir(project_id)
        hist.mkdir(parents=True, exist_ok=True)

        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%f") + "Z"
        suffix = secrets.token_hex(3)
        filename = f"{ts}-{suffix}.md"
        (hist / filename).write_text(content, encoding="utf-8")

        # Append index row (atomic-ish; acceptable for single-writer)
        index_path = hist / BRIEF_HISTORY_INDEX
        row = {
            "ts": ts,
            "actor_type": actor_type,
            "actor_id": actor_id,
            "bytes": len(content.encode("utf-8")),
            "filename": filename,
        }
        with index_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

        # Prune if we've exceeded the retention cap
        self._prune_brief_history(project_id)

    def _prune_brief_history(self, project_id: uuid.UUID) -> None:
        hist = self._history_dir(project_id)
        if not hist.exists():
            return
        entries = sorted(
            (p for p in hist.glob("*.md") if p.is_file()),
            key=lambda p: p.name,
        )
        excess = len(entries) - _MAX_BRIEF_HISTORY_ENTRIES
        if excess <= 0:
            return
        for old in entries[:excess]:
            try:
                old.unlink()
            except OSError:
                pass

    def list_brief_history(self, project_id: uuid.UUID) -> list[dict]:
        """Return newest-first list of snapshots: {ts, actor_type, actor_id, bytes, filename}."""
        import json

        hist = self._history_dir(project_id)
        index_path = hist / BRIEF_HISTORY_INDEX
        rows: list[dict] = []
        if index_path.exists():
            try:
                for line in index_path.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rows.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
            except OSError:
                rows = []
        # Filter to snapshots whose files still exist on disk
        rows = [r for r in rows if (hist / r.get("filename", "")).exists()]
        rows.sort(key=lambda r: r.get("ts", ""), reverse=True)
        return rows

    def read_brief_snapshot(self, project_id: uuid.UUID, filename: str) -> str:
        """Return the content of a specific snapshot. Path-traversal safe."""
        if "/" in filename or ".." in filename or not filename.endswith(".md"):
            raise ValueError("Invalid snapshot filename")
        path = self._history_dir(project_id) / filename
        if not path.is_file():
            raise FileNotFoundError(filename)
        return path.read_text(encoding="utf-8", errors="replace")

    def truncate_brief_for_prompt(
        self, brief_md: str, max_tokens: int = 2000
    ) -> tuple[str, bool]:
        """Return (maybe-truncated text, was_truncated)."""
        char_budget = int(max_tokens * _CHARS_PER_TOKEN_ESTIMATE)
        if len(brief_md) <= char_budget:
            return brief_md, False
        return brief_md[:char_budget], True

    # ── files ────────────────────────────────────────────────────────────
    def _safe_path(self, project_id: uuid.UUID, rel_path: str) -> Path:
        """Resolve a relative path inside the project workspace, rejecting traversal.

        Accepts forward-slash separated paths. Empty string = workspace root.
        Raises ValueError if the path escapes the workspace root.
        """
        rel_path = (rel_path or "").strip().lstrip("/")
        root = self.get_root(project_id)
        # Use parent .resolve() chain since rel target may not exist yet (write paths)
        try:
            base = root.resolve()
        except OSError:
            base = root
        candidate = (root / rel_path)
        try:
            full = candidate.resolve(strict=False)
        except OSError:
            full = candidate
        if not (str(full) == str(base) or str(full).startswith(str(base) + ("/" if not str(base).endswith("/") else ""))):
            # Fall back to part comparison for cross-platform safety
            try:
                full.relative_to(base)
            except (ValueError, OSError):
                raise ValueError(f"Invalid path (escapes workspace): {rel_path}")
        return full

    def list_physical_files(
        self, project_id: uuid.UUID, sub_path: str = ""
    ) -> list[PhysicalFile]:
        """Enumerate files and directories under sub_path (default: workspace root).

        - Single-level only (no recursion); a UI navigates one folder at a time.
        - Excludes BRIEF.md (surfaced separately) only at the root level.
        - Excludes hidden entries (names starting with ".") at every level.
        - Each entry's `physical_path` is relative to the workspace root, using "/".
        """
        root = self.get_root(project_id)
        if not root.exists():
            return []

        target = self._safe_path(project_id, sub_path)
        if not target.exists() or not target.is_dir():
            return []

        is_root = (target.resolve() == root.resolve())
        entries: list[PhysicalFile] = []
        # Sort: directories first, then files, both alphabetically — matches FileBrowser expectations.
        for entry in sorted(target.iterdir(), key=lambda e: (not e.is_dir(), e.name.lower())):
            if entry.name.startswith("."):
                continue
            if is_root and entry.name == BRIEF_FILENAME:
                continue
            try:
                rel = entry.resolve().relative_to(root.resolve()).as_posix()
            except (ValueError, OSError):
                continue
            if entry.is_dir():
                stat = entry.stat()
                entries.append(PhysicalFile(
                    filename=entry.name,
                    physical_path=rel,
                    size_bytes=0,
                    mime_type="",
                    is_dir=True,
                    mtime_iso=_mtime_iso(stat.st_mtime),
                ))
            elif entry.is_file():
                stat = entry.stat()
                entries.append(PhysicalFile(
                    filename=entry.name,
                    physical_path=rel,
                    size_bytes=stat.st_size,
                    mime_type=_guess_mime(entry.name),
                    mtime_iso=_mtime_iso(stat.st_mtime),
                ))
        return entries

    def save_upload(
        self,
        project_id: uuid.UUID,
        filename: str,
        content: bytes,
        conflict_mode: ConflictMode | None = None,
        sub_path: str = "",
    ) -> SaveResult:
        """Write bytes into the project workspace under sub_path/filename.

        - sub_path is a forward-slash relative path (default: workspace root).
        - Filename must not contain "/" — pass sub_path separately.
        - Conflict check is scoped to sub_path (same name in different dirs is OK).
        - Raises FileExistsError when conflict_mode is None and the filename is taken
          at this sub_path; caller is expected to prompt the user and retry.
        """
        if "/" in filename or "\\" in filename or filename in ("", ".", ".."):
            raise ValueError(f"Invalid filename: {filename!r}")
        sub_path = (sub_path or "").strip().lstrip("/")
        # BRIEF.md is reserved at root only — under a sub_path the name is fine.
        if sub_path == "" and filename == BRIEF_FILENAME:
            raise ValueError("BRIEF.md is reserved; use the brief endpoints to edit it.")

        dir_path = self._safe_path(project_id, sub_path)
        dir_path.mkdir(parents=True, exist_ok=True)

        target = dir_path / filename
        # _safe_path on the full rel path keeps the assertion that target is inside the root.
        rel_full = f"{sub_path}/{filename}" if sub_path else filename
        self._safe_path(project_id, rel_full)

        replaced = False
        if target.exists():
            if conflict_mode is None or conflict_mode == "abort":
                raise FileExistsError(filename)
            if conflict_mode == "replace":
                replaced = True
            elif conflict_mode == "keep_both":
                filename = _next_unique_name(dir_path, filename)
                target = dir_path / filename
                rel_full = f"{sub_path}/{filename}" if sub_path else filename
            else:
                raise ValueError(f"Unknown conflict_mode: {conflict_mode}")

        target.write_bytes(content)
        return SaveResult(
            filename=filename,
            physical_path=rel_full,
            size_bytes=len(content),
            replaced_existing=replaced,
        )

    def delete_file(self, project_id: uuid.UUID, physical_path: str) -> None:
        """Remove a file from disk. No-op if missing. Blocks BRIEF.md deletion.

        physical_path is relative to the workspace root, may contain "/" for nested files.
        Empty directories left behind by deletion are NOT auto-cleaned (matches FS semantics).
        """
        if physical_path == BRIEF_FILENAME:
            raise ValueError("BRIEF.md cannot be deleted.")
        path = self._safe_path(project_id, physical_path)
        if path.exists() and path.is_file():
            path.unlink()

    def move(
        self, project_id: uuid.UUID, src_path: str, dst_path: str
    ) -> str:
        """Rename or relocate a file/directory inside the project workspace.

        - src_path / dst_path are workspace-relative, "/"-separated.
        - Both are validated for path-traversal.
        - dst_path is the FULL new path (not a destination directory). To move
          a file `foo.md` into `posts/`, pass dst_path="posts/foo.md".
        - Auto-creates dst's parent directory if needed.
        - Refuses moving BRIEF.md and refuses overwrites (raises FileExistsError).
        - Refuses moving a directory into its own descendant (would be circular).

        Returns the resolved dst_path (same as input, sanitized).
        """
        src_path = (src_path or "").strip().lstrip("/")
        dst_path = (dst_path or "").strip().lstrip("/")
        if not src_path or not dst_path:
            raise ValueError("src_path and dst_path are required")
        if src_path == dst_path:
            return src_path
        if src_path == BRIEF_FILENAME or dst_path == BRIEF_FILENAME:
            raise ValueError("BRIEF.md cannot be moved.")
        if dst_path.endswith("/" + BRIEF_FILENAME):
            raise ValueError("Cannot use BRIEF.md as a destination filename.")

        src_full = self._safe_path(project_id, src_path)
        dst_full = self._safe_path(project_id, dst_path)

        if not src_full.exists():
            raise FileNotFoundError(src_path)
        if dst_full.exists():
            raise FileExistsError(dst_path)

        # Refuse moving a directory into its own descendant subtree.
        if src_full.is_dir():
            try:
                src_resolved = src_full.resolve(strict=False)
                dst_resolved = dst_full.resolve(strict=False)
                if dst_resolved.is_relative_to(src_resolved):
                    raise ValueError("Cannot move a directory into itself.")
            except OSError:
                pass

        dst_full.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src_full), str(dst_full))
        # Touch the destination so list_physical_files / list_recent_changes
        # surfaces the rename/relocation. Without this the file keeps its
        # original mtime (shutil.move uses os.rename on same-FS, preserving
        # mtime) and "Modified" pill / "recent changes" miss the activity.
        try:
            import os as _os
            _os.utime(dst_full, None)
        except OSError:
            pass
        return dst_path

    def list_recent_files(
        self, project_id: uuid.UUID, hours: int = 168, limit: int = 20,
    ) -> list[PhysicalFile]:
        """Recursive walk returning files modified within the last N hours,
        newest first, capped at `limit`. Powers the Overview "Recent activity"
        section. Excludes BRIEF.md (it has its own history surface) and any
        entries inside hidden directories (.brief-history, etc).
        """
        import time as _time
        root = self.get_root(project_id)
        if not root.exists():
            return []
        cutoff = _time.time() - hours * 3600
        results: list[tuple[float, PhysicalFile]] = []
        for entry in root.rglob("*"):
            if not entry.is_file():
                continue
            try:
                rel_parts = entry.relative_to(root).parts
            except ValueError:
                continue
            if any(p.startswith(".") for p in rel_parts):
                continue
            if rel_parts == (BRIEF_FILENAME,):
                continue
            try:
                stat = entry.stat()
            except OSError:
                continue
            if stat.st_mtime < cutoff:
                continue
            rel = "/".join(rel_parts)
            results.append((stat.st_mtime, PhysicalFile(
                filename=entry.name,
                physical_path=rel,
                size_bytes=stat.st_size,
                mime_type=_guess_mime(entry.name),
                mtime_iso=_mtime_iso(stat.st_mtime),
            )))
        results.sort(key=lambda t: t[0], reverse=True)
        return [pf for _, pf in results[:limit]]

    def find_empty_dirs(self, project_id: uuid.UUID) -> list[str]:
        """Return relative paths of all subdirectories that contain no
        user-visible files (empty, or only `.gitkeep` / hidden entries).

        Workspace root and `.brief-history/` are never returned. Order: deepest first
        so a caller can rmdir them in sequence without the parent disappearing first.
        """
        root = self.get_root(project_id)
        if not root.exists():
            return []
        empties: list[str] = []
        for dir_path in sorted(
            (p for p in root.rglob("*") if p.is_dir()),
            key=lambda p: -len(p.parts),
        ):
            try:
                rel = dir_path.relative_to(root).as_posix()
            except ValueError:
                continue
            if rel.startswith(BRIEF_HISTORY_DIRNAME):
                continue
            visible = [c for c in dir_path.iterdir() if not c.name.startswith(".")]
            if not visible:
                empties.append(rel)
        return empties

    def prune_empty_dirs(self, project_id: uuid.UUID) -> list[str]:
        """Remove all empty subdirectories (those with no user-visible files).

        Returns the list of paths removed. Iterates because pruning a leaf may
        make its parent empty — deepest-first ordering inside `find_empty_dirs`
        means most cleanup happens in the first pass; the loop catches cascades.
        Bounded to MAX_PASSES iterations as a safety net against an empty list
        that won't shrink (e.g. read-only filesystem swallowing every rmtree).
        """
        MAX_PASSES = 8  # in practice, ≤ 2 passes; extra headroom for rare cascades
        removed: list[str] = []
        for _ in range(MAX_PASSES):
            empties = self.find_empty_dirs(project_id)
            if not empties:
                break
            removed_before = len(removed)
            for rel in empties:
                try:
                    full = self._safe_path(project_id, rel)
                    shutil.rmtree(full)
                    removed.append(rel)
                except (ValueError, OSError):
                    continue
            # Termination guard: if nothing removed this pass, the remaining
            # empties are stuck (permission errors, read-only fs) — stop.
            if len(removed) == removed_before:
                break
        return removed

    def delete_directory(self, project_id: uuid.UUID, rel_path: str) -> None:
        """Remove an entire subdirectory and its contents. Refuses workspace root."""
        rel_path = (rel_path or "").strip().lstrip("/")
        if rel_path in ("", "."):
            raise ValueError("Cannot delete project workspace root")
        path = self._safe_path(project_id, rel_path)
        if path.exists() and path.is_dir():
            shutil.rmtree(path)

    def suggest_alt_name(
        self, project_id: uuid.UUID, filename: str, sub_path: str = ""
    ) -> str:
        """Return what filename would be used under conflict=keep_both at sub_path."""
        sub_path = (sub_path or "").strip().lstrip("/")
        dir_path = self._safe_path(project_id, sub_path)
        if not dir_path.exists():
            return filename
        return _next_unique_name(dir_path, filename)

    def read_file_bytes(self, project_id: uuid.UUID, physical_path: str) -> bytes:
        """Read raw bytes for download. Raises FileNotFoundError if missing."""
        path = self._safe_path(project_id, physical_path)
        return path.read_bytes()

    def read_text(self, project_id: uuid.UUID, rel_path: str) -> str:
        """Read a text file as UTF-8. Returns a placeholder string for binary files."""
        path = self._safe_path(project_id, rel_path)
        if not path.exists() or not path.is_file():
            raise FileNotFoundError(rel_path)
        try:
            return path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            return f"[Binary file: {path.name}, {path.stat().st_size} bytes]"

    def write_text(
        self, project_id: uuid.UUID, rel_path: str, content: str
    ) -> SaveResult:
        """Write a UTF-8 text file at rel_path. Auto-creates parent directories.

        Used by the FileBrowser "new file" / "edit existing" / "new folder
        (writes a .gitkeep)" flows. Reject BRIEF.md — use write_brief instead.
        """
        rel_path = (rel_path or "").strip().lstrip("/")
        if not rel_path:
            raise ValueError("Empty path")
        if rel_path == BRIEF_FILENAME or rel_path.endswith("/" + BRIEF_FILENAME):
            raise ValueError("BRIEF.md is reserved; use the brief endpoints to edit it.")
        path = self._safe_path(project_id, rel_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        encoded = content.encode("utf-8")
        path.write_bytes(encoded)
        return SaveResult(
            filename=path.name,
            physical_path=rel_path,
            size_bytes=len(encoded),
            replaced_existing=False,
        )


project_workspace_service = ProjectWorkspaceService()


# ── System-prompt integration ────────────────────────────────────────────

async def build_project_context_block(session_id: str | uuid.UUID) -> str:
    """Return a markdown block describing the project, to prepend to an agent's
    system prompt. Empty string if the session is not bound to a project.

    Also lazily provisions the agent ↔ project workspace symlink so that the
    agent's existing filesystem tools (read_file / write_file / list_files) can
    reach shared project files via a `.project/{project_id}/` relative path.
    """
    from app.database import async_session
    from app.models.chat_session import ChatSession
    from app.models.project import Project
    from sqlalchemy import select as _select, func

    if not session_id:
        return ""

    try:
        session_uuid = uuid.UUID(str(session_id))
    except (ValueError, AttributeError):
        return ""

    async with async_session() as db:
        session = (await db.execute(
            _select(ChatSession).where(ChatSession.id == session_uuid)
        )).scalar_one_or_none()
        if session is None or session.project_id is None:
            return ""
        project = (await db.execute(
            _select(Project).where(Project.id == session.project_id)
        )).scalar_one_or_none()
        if project is None:
            return ""
        agent_id = session.agent_id

    # Self-heal: make sure the project workspace and BRIEF.md exist before
    # linking / reading. Idempotent — only seeds when missing.
    project_workspace_service.ensure_initialized(project.id, project.name)
    # Provision the symlink so file tools can reach project files by path.
    _link_path, rel_path = project_workspace_service.ensure_agent_project_symlink(
        agent_id, project.id
    )

    brief_raw = project_workspace_service.read_brief(project.id)
    brief_text, truncated = project_workspace_service.truncate_brief_for_prompt(brief_raw, max_tokens=2000)
    files = project_workspace_service.list_physical_files(project.id)

    parts: list[str] = [f"## Active Project: {project.name}"]
    if project.description:
        parts.append(project.description)
    parts.append("")
    parts.append("### Project Brief")
    parts.append(brief_text.strip() or "_(BRIEF.md is empty — ask the user for project details.)_")
    if truncated:
        parts.append("")
        parts.append(f"_Project brief truncated for length; the full file is at `{rel_path}/BRIEF.md`._")

    parts.append("")
    parts.append("### Shared project workspace")
    parts.append(
        f"You are working inside project **{project.name}**. The project's shared "
        f"workspace is available to you at the relative path `{rel_path}/`. Use your "
        f"existing filesystem tools to operate there:"
    )
    parts.append(f"- `read_file(\"{rel_path}/BRIEF.md\")` — read or update the project brief")
    parts.append(f"- `list_files(\"{rel_path}/\")` — list shared files")
    parts.append(f"- `write_file(\"{rel_path}/<name>\", ...)` — produce a file that the team can see in the Files tab")
    parts.append("")
    parts.append(
        "Files you write under that path go to the **team-shared** workspace, not your "
        "private `workspace/`. Treat them as team artefacts — other members see them in "
        "the project's Files tab. Your own `workspace/` is still yours for scratch work."
    )

    if files:
        parts.append("")
        parts.append("Currently in the shared workspace:")
        for f in files[:30]:
            if f.is_dir:
                parts.append(f"- `{f.filename}/` (directory)")
            else:
                size_kb = max(1, f.size_bytes // 1024)
                parts.append(f"- `{f.filename}` ({size_kb} KB)")
        if len(files) > 30:
            parts.append(f"- ...and {len(files) - 30} more")

    # ── Active project tasks (Phase 3 deliverables) ──────────────────────
    # Only inject the active subset to keep prompt budget under control. Agents
    # can call `list_project_tasks` for the full picture if needed.
    try:
        from app.models.project_task import ProjectTask
        from app.models.agent import Agent as _Agent
        async with async_session() as db:
            active_rows = (await db.execute(
                _select(ProjectTask, _Agent.name)
                .outerjoin(_Agent, _Agent.id == ProjectTask.assigned_agent_id)
                .where(
                    ProjectTask.project_id == project.id,
                    ProjectTask.status != "done",
                )
                .order_by(ProjectTask.created_at.desc())
                .limit(10)
            )).all()
            total = (await db.execute(
                _select(func.count(ProjectTask.id)).where(ProjectTask.project_id == project.id)
            )).scalar() or 0
        if active_rows:
            shown = len(active_rows)
            parts.append("")
            parts.append(f"### Active Tasks ({shown} of {total} total)")
            for task, agent_name in active_rows:
                bits = [f"[{task.status}]", task.title]
                if agent_name:
                    bits.append(f"(@{agent_name})")
                if task.due_date:
                    bits.append(f"due {task.due_date.strftime('%Y-%m-%d')}")
                parts.append(f"- {' '.join(bits)}  (id: {task.id})")
            parts.append("")
            parts.append(
                "You can run `list_project_tasks` for the full list, "
                "`update_project_task` to change status, or `create_project_task` to add new ones."
            )
    except Exception as _exc:
        # Tasks injection is best-effort — never block project context build.
        import logging as _logging
        _logging.getLogger(__name__).warning(f"[project context] Could not inject tasks: {_exc}")

    # ── Active project-bound triggers (Phase 5 P0-B) ─────────────────────
    # Surface the agent's own triggers that are bound to THIS project (via
    # focus_ref="project:<pid>") so it knows what's already scheduled and
    # how to set new ones with the correct focus_ref convention.
    try:
        from app.models.trigger import AgentTrigger
        async with async_session() as db:
            trig_rows = (await db.execute(
                _select(AgentTrigger).where(
                    AgentTrigger.agent_id == agent_id,
                    AgentTrigger.focus_ref == f"project:{project.id}",
                    AgentTrigger.is_enabled == True,  # noqa: E712
                ).order_by(AgentTrigger.created_at.desc()).limit(10)
            )).scalars().all()
        if trig_rows:
            parts.append("")
            parts.append(f"### Scheduled triggers in this project ({len(trig_rows)})")
            for tr in trig_rows:
                cfg = ""
                if isinstance(tr.config, dict):
                    if tr.type == "cron":
                        cfg = tr.config.get("expr", "")
                    elif tr.type == "interval":
                        cfg = f"every {tr.config.get('minutes', '?')}m"
                    elif tr.type == "once":
                        cfg = f"at {tr.config.get('at', '')}"
                bits = [f"[{tr.type}]", tr.name]
                if cfg:
                    bits.append(f"({cfg})")
                parts.append(f"- {' '.join(bits)}")
        parts.append("")
        parts.append(
            f"To add a new trigger that fires inside this project's chat context "
            f"(BRIEF / files / tasks all available on wake), call set_trigger with "
            f"`focus_ref=\"project:{project.id}\"`. Without that exact prefix the "
            f"trigger will only appear in your Aware tab and fire in your default context."
        )
    except Exception as _exc:
        import logging as _logging
        _logging.getLogger(__name__).warning(f"[project context] Could not inject triggers: {_exc}")

    return "\n".join(parts)

