"""Snapshot-before / diff-after capture for `session.done` diff_summary.

Two backends:
  - `git` (preferred): record HEAD + working-tree status before, `git diff --stat`
    against that snapshot at session end.
  - `mtime fallback`: scan the cwd tree, remember (path, size, mtime) for each
    file; at session end, diff against a re-scan. No content diff — just
    files_changed / created / deleted counts plus per-file size deltas.

Both return a dict matching the `DiffSummary` schema shape.
"""
from __future__ import annotations

import asyncio
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class _GitSnapshot:
    cwd: str
    head: str
    stash_ref: str | None = None  # reserved; unused V1


@dataclass
class _MtimeSnapshot:
    cwd: str
    files: dict[str, tuple[int, float]] = field(default_factory=dict)  # path -> (size, mtime)


async def _run(cmd: list[str], cwd: str) -> tuple[int, str, str]:
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=cwd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        out, err = await proc.communicate()
        return (
            proc.returncode or 0,
            out.decode("utf-8", errors="replace"),
            err.decode("utf-8", errors="replace"),
        )
    except FileNotFoundError:
        return 127, "", "command not found"


async def _is_git_repo(cwd: str) -> bool:
    rc, out, _ = await _run(["git", "rev-parse", "--is-inside-work-tree"], cwd=cwd)
    return rc == 0 and out.strip() == "true"


async def _git_head(cwd: str) -> str:
    rc, out, _ = await _run(["git", "rev-parse", "HEAD"], cwd=cwd)
    if rc == 0:
        return out.strip()
    return ""


def _mtime_scan(cwd: Path, max_files: int = 20000) -> dict[str, tuple[int, float]]:
    out: dict[str, tuple[int, float]] = {}
    count = 0
    skip_dirs = {".git", "node_modules", "__pycache__", ".venv", "venv", ".tox", "dist", "build"}
    for root, dirs, files in os.walk(cwd):
        dirs[:] = [d for d in dirs if d not in skip_dirs and not d.startswith(".")]
        for f in files:
            full = Path(root) / f
            try:
                st = full.stat()
            except OSError:
                continue
            rel = str(full.relative_to(cwd))
            out[rel] = (st.st_size, st.st_mtime)
            count += 1
            if count >= max_files:
                return out
    return out


async def snapshot(cwd: str | None) -> _GitSnapshot | _MtimeSnapshot | None:
    if not cwd:
        return None
    if not os.path.isdir(cwd):
        return None
    if await _is_git_repo(cwd):
        head = await _git_head(cwd)
        return _GitSnapshot(cwd=cwd, head=head)
    files = await asyncio.to_thread(_mtime_scan, Path(cwd))
    return _MtimeSnapshot(cwd=cwd, files=files)


_NUMSTAT_LINE = re.compile(r"^(\d+|-)\s+(\d+|-)\s+(.+)$")


async def _git_diff_summary(snap: _GitSnapshot) -> dict[str, Any]:
    # Include both committed changes (HEAD..HEAD) — N/A here, snap.head == current HEAD
    # and uncommitted changes (working tree + index vs. HEAD).
    rc, out, err = await _run(["git", "diff", "--numstat", snap.head], cwd=snap.cwd)
    if rc != 0:
        return {"files_changed": 0, "insertions": 0, "deletions": 0, "files": [], "warning": err.strip()}
    files: list[dict[str, Any]] = []
    total_ins = 0
    total_del = 0
    for line in out.splitlines():
        m = _NUMSTAT_LINE.match(line.strip())
        if not m:
            continue
        ins_raw, del_raw, path = m.groups()
        ins = int(ins_raw) if ins_raw.isdigit() else 0
        dels = int(del_raw) if del_raw.isdigit() else 0
        total_ins += ins
        total_del += dels
        files.append({"path": path, "+": ins, "-": dels})
    # Untracked files — show as created
    rc2, out2, _ = await _run(["git", "ls-files", "--others", "--exclude-standard"], cwd=snap.cwd)
    if rc2 == 0:
        for path in out2.splitlines():
            path = path.strip()
            if not path:
                continue
            files.append({"path": path, "+": 0, "-": 0, "status": "untracked"})
    return {
        "files_changed": len(files),
        "insertions": total_ins,
        "deletions": total_del,
        "files": files[:200],
    }


async def _mtime_diff_summary(snap: _MtimeSnapshot) -> dict[str, Any]:
    after = await asyncio.to_thread(_mtime_scan, Path(snap.cwd))
    before = snap.files
    files: list[dict[str, Any]] = []
    total_delta = 0
    for path, (size, mtime) in after.items():
        if path not in before:
            files.append({"path": path, "+": size, "-": 0, "status": "created"})
            total_delta += size
        else:
            old_size, old_mtime = before[path]
            if mtime != old_mtime or size != old_size:
                delta = size - old_size
                files.append({
                    "path": path,
                    "+": max(0, delta),
                    "-": max(0, -delta),
                    "status": "modified",
                })
                total_delta += abs(delta)
    for path in before.keys() - after.keys():
        old_size, _ = before[path]
        files.append({"path": path, "+": 0, "-": old_size, "status": "deleted"})
        total_delta += old_size
    return {
        "files_changed": len(files),
        "insertions": 0,  # mtime scan doesn't know line counts
        "deletions": 0,
        "files": files[:200],
        "note": "mtime-based summary; install git for line-level counts",
    }


async def diff_summary(snap: _GitSnapshot | _MtimeSnapshot | None) -> dict[str, Any] | None:
    if snap is None:
        return None
    if isinstance(snap, _GitSnapshot):
        try:
            return await _git_diff_summary(snap)
        except Exception as e:
            return {"files_changed": 0, "insertions": 0, "deletions": 0, "files": [], "warning": f"git diff failed: {e}"}
    if isinstance(snap, _MtimeSnapshot):
        try:
            return await _mtime_diff_summary(snap)
        except Exception as e:
            return {"files_changed": 0, "insertions": 0, "deletions": 0, "files": [], "warning": f"mtime scan failed: {e}"}
    return None
