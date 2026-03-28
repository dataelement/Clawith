"""Vision injection utilities for AgentBay screenshot tools.

Provides helpers to detect screenshot file paths in tool results,
read and compress the images, and convert them into base64 data URLs
suitable for injecting into LLM vision content arrays.
"""

import base64
import re
from io import BytesIO
from pathlib import Path
from typing import Optional

from loguru import logger


# Tool names whose results may contain screenshot file paths
SCREENSHOT_TOOL_NAMES = frozenset({
    "agentbay_browser_navigate",
    "agentbay_browser_screenshot",
    "agentbay_computer_screenshot",
})

# Regex to find screenshot paths in tool result text
# Matches patterns like: workspace/screenshot_1234567890.png
#                    or: workspace/desktop-screenshot-1234567890.png
_SCREENSHOT_PATH_RE = re.compile(
    r"(?:workspace/(?:desktop[_-])?screenshot[_-]\d+\.png)"
)

# Max dimension (width) for compressed screenshots sent to the LLM
# Use full desktop width to preserve icon/text detail for cloud desktops
_MAX_WIDTH = 1920
# JPEG quality for compressed screenshots (higher = more detail for icons/text)
_JPEG_QUALITY = 80


def compress_screenshot_to_base64(file_path: Path) -> Optional[str]:
    """Read a screenshot file, compress it, and return a base64 data URL.

    Resizes to max _MAX_WIDTH pixels wide (preserving aspect ratio),
    converts to JPEG at _JPEG_QUALITY, and returns a data:image/jpeg;base64,... URL.

    Returns None if the file doesn't exist or processing fails.
    """
    if not file_path.exists():
        logger.warning(f"[VisionInject] Screenshot file not found: {file_path}")
        return None

    try:
        from PIL import Image

        img = Image.open(file_path)

        # Resize if too wide (preserving aspect ratio)
        if img.width > _MAX_WIDTH:
            ratio = _MAX_WIDTH / img.width
            new_size = (int(img.width * ratio), int(img.height * ratio))
            img = img.resize(new_size, Image.LANCZOS)

        # Convert RGBA to RGB for JPEG compatibility
        if img.mode in ("RGBA", "P"):
            img = img.convert("RGB")

        # Compress to JPEG
        buffer = BytesIO()
        img.save(buffer, format="JPEG", quality=_JPEG_QUALITY, optimize=True)
        b64_data = base64.b64encode(buffer.getvalue()).decode("ascii")

        size_kb = len(buffer.getvalue()) / 1024
        logger.info(
            f"[VisionInject] Compressed screenshot {file_path.name}: "
            f"{img.width}x{img.height}, {size_kb:.0f}KB"
        )
        return f"data:image/jpeg;base64,{b64_data}"

    except ImportError:
        logger.warning("[VisionInject] Pillow not installed, cannot compress screenshots")
        return None
    except Exception as e:
        logger.warning(f"[VisionInject] Failed to compress screenshot: {e}")
        return None


def try_inject_screenshot_vision(
    tool_name: str,
    result_text: str,
    ws_path: Path,
) -> Optional[list]:
    """Try to extract a screenshot from a tool result and build a vision content array.

    Args:
        tool_name: Name of the tool that produced the result.
        result_text: Plain text result from the tool.
        ws_path: Agent workspace root path.

    Returns:
        A list suitable for LLMMessage.content (with text + image_url parts),
        or None if no screenshot was found / tool is not a screenshot tool.
    """
    if tool_name not in SCREENSHOT_TOOL_NAMES:
        return None

    # Find screenshot path in the result text
    match = _SCREENSHOT_PATH_RE.search(result_text)
    if not match:
        return None

    rel_path = match.group(0)
    abs_path = ws_path / rel_path

    data_url = compress_screenshot_to_base64(abs_path)
    if not data_url:
        return None

    # Build OpenAI-compatible vision content array
    return [
        {"type": "text", "text": result_text},
        {"type": "image_url", "image_url": {"url": data_url}},
    ]
