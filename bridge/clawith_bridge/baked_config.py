"""Shared trailer format for per-agent config baked into the exe.

Server-side `render_installer(platform="windows")` appends a trailer to the
pristine PyInstaller exe so the user downloads a single self-configuring
setup.exe. On double-click, the bridge reads the trailer (this module) and
runs the install flow. The install flow then strips the trailer from the
copy it drops into %LOCALAPPDATA%\\Clawith\\bin\\ so subsequent service-mode
launches don't re-trigger install.

Trailer layout (read from end of file, backwards):

    [...pristine exe bytes...]
    [json utf-8 blob         ]  <- variable length
    [4 bytes pristine_len BE ]  <- uint32, offset where the trailer starts
    [8 bytes magic "CLWB!END"]  <- literal bytes at EOF

PE loaders only read what section headers point at, so an overlay at EOF is
ignored by the Windows loader. Appending doesn't break PyInstaller bootstrap.

This module stays dependency-free (only stdlib) so it's cheap to import from
both the server (which generates trailers) and the bridge (which reads them).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

MAGIC: bytes = b"CLWB!END"
TRAILER_LEN_BYTES = 4  # uint32 big-endian for pristine_len
TRAILER_FIXED_SUFFIX = TRAILER_LEN_BYTES + len(MAGIC)  # = 12


def build_trailer(config: dict[str, Any], pristine_len: int) -> bytes:
    """Encode `config` as JSON and produce the trailer bytes to append.

    Caller is responsible for supplying `pristine_len` = length of the original
    exe in bytes (i.e. the offset where the trailer starts after concatenation).
    """
    blob = json.dumps(config, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return blob + pristine_len.to_bytes(TRAILER_LEN_BYTES, "big") + MAGIC


def read_baked_config(exe_path: Path | None = None) -> dict[str, Any] | None:
    """Return baked config dict if the exe has a trailer, else None.

    Defaults to reading `sys.executable`. Safe to call on any file; returns
    None for files with no trailer, unreadable files, or malformed trailers.
    """
    path = exe_path or Path(sys.executable)
    try:
        data = path.read_bytes()
    except OSError:
        return None

    if len(data) < TRAILER_FIXED_SUFFIX:
        return None
    if data[-len(MAGIC):] != MAGIC:
        return None

    len_start = -TRAILER_FIXED_SUFFIX
    len_end = -len(MAGIC)
    pristine_len = int.from_bytes(data[len_start:len_end], "big")
    if pristine_len <= 0 or pristine_len > len(data) - TRAILER_FIXED_SUFFIX:
        return None

    blob = data[pristine_len:len_start]
    try:
        obj = json.loads(blob.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(obj, dict):
        return None
    return obj


def strip_trailer(exe_path: Path) -> bool:
    """If the file at exe_path has a trailer, truncate it to pristine bytes.

    Returns True if a trailer was found and stripped, False otherwise.
    Never raises on "no trailer" — only on filesystem errors from truncate().
    """
    try:
        with exe_path.open("rb") as f:
            f.seek(0, 2)
            size = f.tell()
            if size < TRAILER_FIXED_SUFFIX:
                return False
            f.seek(-TRAILER_FIXED_SUFFIX, 2)
            suffix = f.read(TRAILER_FIXED_SUFFIX)
    except OSError:
        return False

    if suffix[-len(MAGIC):] != MAGIC:
        return False
    pristine_len = int.from_bytes(suffix[:TRAILER_LEN_BYTES], "big")
    if pristine_len <= 0 or pristine_len >= size:
        return False

    with exe_path.open("r+b") as f:
        f.truncate(pristine_len)
    return True
