"""Vision injection utilities for AgentBay screenshot tools.

Architecture: "Ephemeral screenshots" pattern
- Internal screenshots (save_to_workspace=False) are held in a process-level memory cache.
  The tool returns a short sentinel string: "[ImageID: <uuid>]".
  When websocket.py invokes try_inject_screenshot_vision(), it finds the ImageID,
  pops the bytes from the cache (consumed once, then gone), compresses to base64 JPEG,
  and injects a vision content array into the LLM message.
  Net result: zero disk writes, zero frontend rendering, zero DB bloat.

- Persistent screenshots (save_to_workspace=True) bypass this module and are saved
  to workspace/ by agent_tools.py, returning a standard Markdown image link.
  Vision injection for those uses the existing file-path code path.
"""

import base64
import re
import time
import uuid as _uuid_mod
from io import BytesIO
from pathlib import Path
from typing import Optional

from loguru import logger


# ─── Memory Image Cache ────────────────────────────────────────────────────────
# Maps a short UUID key -> (raw_bytes, created_at_timestamp).
# Items older than _CACHE_TTL_SECONDS are pruned lazily on each store() call.
_memory_image_cache: dict[str, tuple[bytes, float]] = {}
_CACHE_TTL_SECONDS = 120  # Safety TTL to prevent leaks if the consumer never fires


def store_temp_screenshot(raw_bytes: bytes) -> str:
    """Store screenshot bytes in the in-memory cache and return a unique image ID.

    The caller (screenshot tool handler) should embed the returned ID in the tool
    result string as: [ImageID: <id>].  vision_inject will then consume it.

    Args:
        raw_bytes: Raw PNG/JPEG bytes from the AgentBay SDK.

    Returns:
        A short UUID string that identifies this image in the cache.
    """
    # Lazily prune expired entries to prevent unbounded memory growth
    _prune_expired_cache()

    img_id = str(_uuid_mod.uuid4())
    _memory_image_cache[img_id] = (raw_bytes, time.monotonic())
    cache_size = len(_memory_image_cache)
    logger.debug(f"[VisionInject] Stored temp screenshot id={img_id}, cache_size={cache_size}")
    return img_id


def _prune_expired_cache() -> None:
    """Remove entries older than _CACHE_TTL_SECONDS from the memory cache."""
    now = time.monotonic()
    expired_keys = [
        k for k, (_, ts) in _memory_image_cache.items()
        if now - ts > _CACHE_TTL_SECONDS
    ]
    for k in expired_keys:
        del _memory_image_cache[k]
    if expired_keys:
        logger.debug(f"[VisionInject] Pruned {len(expired_keys)} expired cache entries")


def pop_temp_screenshot(img_id: str) -> Optional[bytes]:
    """Consume and remove a screenshot from the memory cache.

    Returns raw bytes if found, None otherwise (already consumed or expired).
    """
    entry = _memory_image_cache.pop(img_id, None)
    if entry is None:
        return None
    raw_bytes, _ = entry
    return raw_bytes


# ─── Regex Patterns ─────────────────────────────────────────────────────────────

# Matches the in-memory sentinel: [ImageID: <uuid>]
_IMAGE_ID_RE = re.compile(r"\[ImageID:\s*([0-9a-f-]{36})\]", re.IGNORECASE)

# Matches workspace file paths for persistent screenshots (save_to_workspace=True)
# Handles: workspace/screenshot_1234.png, workspace/desktop-screenshot-1234.png
_SCREENSHOT_PATH_RE = re.compile(
    r"workspace/(?:desktop[_-])?screenshot[_-]\d+\.png"
)

# Tool names that can produce screenshots (either in-memory or file-based)
SCREENSHOT_TOOL_NAMES = frozenset({
    "agentbay_browser_navigate",
    "agentbay_browser_screenshot",
    "agentbay_computer_screenshot",
})

# Sentinel text that replaces consumed [ImageID: ...] markers in DB-stored history
IMAGE_ID_PLACEHOLDER = "[screenshot - internal analysis only, not available in history]"

# Max width for compressed screenshots sent to the LLM
_MAX_WIDTH = 1920
# JPEG quality (higher = more detail for icons/text readability)
_JPEG_QUALITY = 80


# ─── Compression Helpers ────────────────────────────────────────────────────────

def compress_bytes_to_base64(raw_bytes: bytes) -> Optional[str]:
    """Compress raw image bytes to a base64 JPEG data URL.

    Resizes to _MAX_WIDTH (preserving aspect ratio) and compresses to JPEG.
    Returns None if Pillow is missing or the bytes are unreadable.
    """
    try:
        from PIL import Image

        img = Image.open(BytesIO(raw_bytes))

        # Resize if too wide (preserving aspect ratio)
        if img.width > _MAX_WIDTH:
            ratio = _MAX_WIDTH / img.width
            new_size = (int(img.width * ratio), int(img.height * ratio))
            img = img.resize(new_size, Image.LANCZOS)

        # Convert RGBA/P to RGB for JPEG compatibility
        if img.mode in ("RGBA", "P"):
            img = img.convert("RGB")

        # Compress to JPEG
        buf = BytesIO()
        img.save(buf, format="JPEG", quality=_JPEG_QUALITY, optimize=True)
        b64_data = base64.b64encode(buf.getvalue()).decode("ascii")

        size_kb = len(buf.getvalue()) / 1024
        logger.info(
            f"[VisionInject] Compressed (Memory): {img.width}x{img.height}, {size_kb:.0f}KB"
        )
        return f"data:image/jpeg;base64,{b64_data}"

    except ImportError:
        logger.warning("[VisionInject] Pillow not installed, cannot compress screenshots")
        return None
    except Exception as e:
        logger.warning(f"[VisionInject] Failed to compress screenshot bytes: {e}")
        return None


def compress_screenshot_to_base64(file_path: Path) -> Optional[str]:
    """Read a screenshot file, compress it, and return a base64 data URL.

    Used only for persistent screenshots saved to workspace/ (save_to_workspace=True).
    Returns None if the file doesn't exist or processing fails.
    """
    if not file_path.exists():
        logger.warning(f"[VisionInject] Screenshot file not found: {file_path}")
        return None
    try:
        raw_bytes = file_path.read_bytes()
        return compress_bytes_to_base64(raw_bytes)
    except Exception as e:
        logger.warning(f"[VisionInject] Failed to read screenshot file: {e}")
        return None


# ─── Main Entry Point ────────────────────────────────────────────────────────────

def try_inject_screenshot_vision(
    tool_name: str,
    result_text: str,
    ws_path: Path,
) -> Optional[list]:
    """Try to extract a screenshot from a tool result and build a vision content array.

    Handles two modes:
    1. In-memory mode: result_text contains [ImageID: <uuid>] (save_to_workspace=False).
       Pops the bytes from the memory cache, compresses, and injects.
    2. File mode: result_text contains a workspace/ path (save_to_workspace=True).
       Reads from disk, compresses, and injects.

    Args:
        tool_name: Name of the tool that produced the result.
        result_text: Plain text result from the tool.
        ws_path: Agent workspace root path (only needed for file mode).

    Returns:
        A list suitable for LLMMessage.content (with text + image_url parts),
        or None if no screenshot was found / tool is not a screenshot tool.
    """
    if tool_name not in SCREENSHOT_TOOL_NAMES:
        return None

    # ── Mode 1: In-memory ephemeral screenshot (preferred path) ──
    id_match = _IMAGE_ID_RE.search(result_text)
    if id_match:
        img_id = id_match.group(1)
        raw_bytes = pop_temp_screenshot(img_id)
        if raw_bytes is None:
            # Cache miss (expired or already consumed) — degrade gracefully
            logger.warning(f"[VisionInject] ImageID {img_id} not found in cache (expired?)")
            return None
        data_url = compress_bytes_to_base64(raw_bytes)
        if not data_url:
            return None
        # Strip the [ImageID: ...] marker from the text that goes to the LLM
        clean_text = _IMAGE_ID_RE.sub("", result_text).strip()
        logger.info(f"[VisionInject] Injected in-memory screenshot for {tool_name}")
        return [
            {"type": "text", "text": clean_text},
            {"type": "image_url", "image_url": {"url": data_url}},
        ]

    # ── Mode 2: File-based persistent screenshot (save_to_workspace=True) ──
    path_match = _SCREENSHOT_PATH_RE.search(result_text)
    if path_match:
        rel_path = path_match.group(0)
        abs_path = ws_path / rel_path
        data_url = compress_screenshot_to_base64(abs_path)
        if not data_url:
            return None
        logger.info(f"[VisionInject] Injected file-based screenshot for {tool_name}")
        return [
            {"type": "text", "text": result_text},
            {"type": "image_url", "image_url": {"url": data_url}},
        ]

    return None


def sanitize_history_tool_result(result_text: str) -> str:
    """Replace any stale [ImageID: ...] markers in a DB-loaded tool result.

    When re-loading old conversation history, the in-memory cache has long since
    been flushed.  Leaving the raw [ImageID: xxxx] in the LLM context would
    confuse the model.  Replace with a human-readable placeholder instead.

    Args:
        result_text: The raw tool result string from historical DB record.

    Returns:
        Cleaned string with all [ImageID: ...] markers replaced.
    """
    if "[ImageID:" not in result_text:
        return result_text
    return _IMAGE_ID_RE.sub(IMAGE_ID_PLACEHOLDER, result_text)
