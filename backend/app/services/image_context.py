"""Re-hydrate image content from disk for LLM multi-turn context.

Scans history messages for [file:xxx.jpg] patterns,
reads the image file from agent workspace, and injects base64 data
so the LLM can see images from previous turns.
"""

import base64
import re
from pathlib import Path
from typing import Optional

from loguru import logger
from app.config import get_settings

IMAGE_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp'}
FILE_PATTERN = re.compile(r'\[file:([^\]]+)\]')
IMAGE_DATA_PATTERN = re.compile(
    r'\[image_data:data:image/[^;]+;base64,[A-Za-z0-9+/=]+\]'
)
MAX_IMAGE_BYTES = 5 * 1024 * 1024  # 5MB per image


def rehydrate_image_messages(
    messages: list[dict],
    agent_id,
    max_images: int = 3,
) -> list[dict]:
    """Scan history for [file:xxx.jpg] and inject base64 image data for LLM.

    Only processes the most recent `max_images` user image messages
    to limit context size and cost.

    Args:
        messages: List of {"role": ..., "content": ...} dicts
        agent_id: Agent UUID for resolving file paths
        max_images: Max number of historical images to re-hydrate

    Returns:
        New list with image messages enriched with base64 data.
        Non-image messages and messages with existing image_data are unchanged.
    """
    settings = get_settings()
    upload_dir = (
        Path(settings.AGENT_DATA_DIR) / str(agent_id) / "workspace" / "uploads"
    )

    # Find user messages with [file:xxx.jpg] (newest first, skip current turn)
    image_indices: list[tuple[int, str]] = []  # (index, filename)
    for i in range(len(messages) - 1, -1, -1):
        msg = messages[i]
        if msg.get("role") != "user":
            continue
        content = msg.get("content", "")
        if not isinstance(content, str):
            continue
        # Skip if already has image_data (current turn)
        if "[image_data:" in content:
            continue
        match = FILE_PATTERN.search(content)
        if not match:
            continue
        filename = match.group(1)
        ext = Path(filename).suffix.lower()
        if ext not in IMAGE_EXTENSIONS:
            continue
        image_indices.append((i, filename))
        if len(image_indices) >= max_images:
            break

    if not image_indices:
        return messages

    # Re-hydrate in-place (working on a copy)
    result = list(messages)
    rehydrated = 0

    for idx, filename in image_indices:
        file_path = upload_dir / filename
        if not file_path.exists():
            logger.warning(f"[ImageContext] File not found: {file_path}")
            continue
        try:
            img_bytes = file_path.read_bytes()
            if len(img_bytes) > MAX_IMAGE_BYTES:
                logger.info(
                    f"[ImageContext] Skipping large image: "
                    f"{filename} ({len(img_bytes)} bytes)"
                )
                continue

            b64 = base64.b64encode(img_bytes).decode("ascii")
            ext = file_path.suffix.lower().lstrip('.')
            mime = f"image/{'jpeg' if ext == 'jpg' else ext}"
            marker = f"[image_data:data:{mime};base64,{b64}]"

            # Append image_data marker to existing content
            old_content = result[idx]["content"]
            result[idx] = {**result[idx], "content": f"{old_content}\n{marker}"}
            rehydrated += 1
            logger.debug(f"[ImageContext] Re-hydrated: {filename}")

        except Exception as e:
            logger.error(f"[ImageContext] Failed to read {filename}: {e}")

    if rehydrated > 0:
        logger.info(
            f"[ImageContext] Re-hydrated {rehydrated} image(s) "
            f"for agent {agent_id}"
        )

    return result
