"""Canonical parsing and budgeting for image-bearing LLM content."""

from __future__ import annotations

import base64
import binascii
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from io import BytesIO
import json
import math
import re
from typing import cast

from PIL import Image, UnidentifiedImageError


_IMAGE_MARKER = re.compile(
    r"\[image_data:(data:image/[^;,\]]+;base64,[A-Za-z0-9+/=]+)\]",
    re.IGNORECASE,
)
_DATA_IMAGE_URL = re.compile(
    r"^data:(image/[^;,]+);base64,([A-Za-z0-9+/=]+)$",
    re.IGNORECASE,
)
_PATCH_SIZE = 28
_MAX_EFFECTIVE_EDGE = 1568
_MAX_IMAGE_CONTEXT_TOKENS = 1568


class MultimodalContentError(ValueError):
    """Image-bearing content cannot be decoded into a safe Runtime input."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class ImageContextInfo:
    """Bounded metadata used in context budgets and Compact prompts."""

    mime_type: str
    decoded_bytes: int | None
    width: int | None
    height: int | None
    effective_width: int | None
    effective_height: int | None
    context_tokens: int


@dataclass(frozen=True, slots=True)
class MultimodalContextStats:
    """Aggregate image facts safe to emit in request-start logs."""

    image_count: int = 0
    decoded_bytes: int = 0
    image_context_tokens: int = 0


def _effective_dimensions(width: int, height: int) -> tuple[int, int]:
    if width <= 0 or height <= 0:
        raise MultimodalContentError(
            "invalid_image_dimensions",
            "Image dimensions must be positive",
        )

    def dimensions(scale: float) -> tuple[int, int]:
        return max(1, math.floor(width * scale)), max(1, math.floor(height * scale))

    max_edge_scale = min(1.0, _MAX_EFFECTIVE_EDGE / max(width, height))
    effective_width, effective_height = dimensions(max_edge_scale)
    if (
        math.ceil(effective_width / _PATCH_SIZE) * math.ceil(effective_height / _PATCH_SIZE)
        <= _MAX_IMAGE_CONTEXT_TOKENS
    ):
        return effective_width, effective_height

    low = 0.0
    high = max_edge_scale
    for _ in range(48):
        candidate = (low + high) / 2
        candidate_width, candidate_height = dimensions(candidate)
        patches = math.ceil(candidate_width / _PATCH_SIZE) * math.ceil(candidate_height / _PATCH_SIZE)
        if patches <= _MAX_IMAGE_CONTEXT_TOKENS:
            low = candidate
        else:
            high = candidate
    return dimensions(low)


def _data_url_info(data_url: str) -> ImageContextInfo:
    matched = _DATA_IMAGE_URL.fullmatch(data_url)
    if matched is None:
        raise MultimodalContentError(
            "invalid_image_data_url",
            "Image content must use a base64 data URL",
        )
    mime_type = matched.group(1).lower()
    try:
        raw = base64.b64decode(matched.group(2), validate=True)
    except (ValueError, binascii.Error) as exc:
        raise MultimodalContentError(
            "invalid_image_base64",
            "Image data URL contains invalid base64",
        ) from exc
    try:
        with Image.open(BytesIO(raw)) as image:
            width, height = image.size
            try:
                orientation = image.getexif().get(274)
            except OSError:
                # Dimensions come from the decoded header. Some valid provider
                # inputs do not expose a fully loadable EXIF stream.
                orientation = None
    except (
        Image.DecompressionBombError,
        OSError,
        UnidentifiedImageError,
        ValueError,
    ) as exc:
        raise MultimodalContentError(
            "invalid_image_data",
            "Image data URL is not a supported image",
        ) from exc
    if orientation in {5, 6, 7, 8}:
        width, height = height, width
    effective_width, effective_height = _effective_dimensions(width, height)
    context_tokens = math.ceil(effective_width / _PATCH_SIZE) * math.ceil(effective_height / _PATCH_SIZE)
    return ImageContextInfo(
        mime_type=mime_type,
        decoded_bytes=len(raw),
        width=width,
        height=height,
        effective_width=effective_width,
        effective_height=effective_height,
        context_tokens=context_tokens,
    )


def _remote_image_info() -> ImageContextInfo:
    return ImageContextInfo(
        mime_type="remote",
        decoded_bytes=None,
        width=None,
        height=None,
        effective_width=None,
        effective_height=None,
        context_tokens=_MAX_IMAGE_CONTEXT_TOKENS,
    )


def _image_url(part: Mapping[str, object]) -> str | None:
    if part.get("type") != "image_url":
        return None
    image_url = part.get("image_url")
    if not isinstance(image_url, Mapping):
        return None
    url = image_url.get("url")
    return url if isinstance(url, str) and url else None


def parse_multimodal_content(content: str | list) -> str | list:
    """Convert legacy image markers and validate structured image data URLs."""
    if isinstance(content, str):
        images = _IMAGE_MARKER.findall(content)
        if not images:
            return content
        for image in images:
            _data_url_info(image)
        text = _IMAGE_MARKER.sub("", content).strip()
        parts: list[dict[str, object]] = [{"type": "image_url", "image_url": {"url": image}} for image in images]
        if text:
            parts.append({"type": "text", "text": text})
        return parts

    normalized: list[object] = []
    for raw_part in content:
        if not isinstance(raw_part, Mapping):
            normalized.append(raw_part)
            continue
        part = dict(raw_part)
        url = _image_url(part)
        if url is not None and url.startswith("data:image/"):
            _data_url_info(url)
        normalized.append(part)
    return normalized


def text_only_multimodal_content(content: str | list) -> str:
    """Remove image bodies for models that do not support vision."""
    parsed = parse_multimodal_content(content)
    if isinstance(parsed, str):
        return parsed
    texts: list[str] = []
    image_count = 0
    for part in parsed:
        if not isinstance(part, Mapping):
            continue
        if part.get("type") == "text" and isinstance(part.get("text"), str):
            texts.append(cast(str, part["text"]))
        elif part.get("type") == "image_url":
            image_count += 1
    if image_count:
        texts.append(f"[用户发送了 {image_count} 张图片，但当前模型不支持视觉，无法查看图片内容]")
    return "\n".join(text for text in texts if text).strip()


def _image_placeholder(info: ImageContextInfo) -> str:
    dimensions = f"{info.width}x{info.height}" if info.width is not None and info.height is not None else "unknown"
    effective_dimensions = (
        f"{info.effective_width}x{info.effective_height}"
        if info.effective_width is not None and info.effective_height is not None
        else "unknown"
    )
    decoded_bytes = str(info.decoded_bytes) if info.decoded_bytes is not None else "unknown"
    return (
        "[image omitted from compact prompt: "
        f"mime={info.mime_type}, dimensions={dimensions}, "
        f"effective_dimensions={effective_dimensions}, decoded_bytes={decoded_bytes}, "
        f"context_tokens={info.context_tokens}]"
    )


def _project(value: object) -> tuple[object, MultimodalContextStats]:
    if isinstance(value, str):
        parsed = parse_multimodal_content(value)
        if isinstance(parsed, list):
            return _project(parsed)
        return value, MultimodalContextStats()
    if isinstance(value, Mapping):
        url = _image_url(value)
        if url is not None:
            info = _data_url_info(url) if url.startswith("data:image/") else _remote_image_info()
            return (
                {"type": "text", "text": _image_placeholder(info)},
                MultimodalContextStats(
                    image_count=1,
                    decoded_bytes=info.decoded_bytes or 0,
                    image_context_tokens=info.context_tokens,
                ),
            )
        projected: dict[str, object] = {}
        stats = MultimodalContextStats()
        for key, nested in value.items():
            projected_value, nested_stats = _project(nested)
            projected[str(key)] = projected_value
            stats = MultimodalContextStats(
                image_count=stats.image_count + nested_stats.image_count,
                decoded_bytes=stats.decoded_bytes + nested_stats.decoded_bytes,
                image_context_tokens=(stats.image_context_tokens + nested_stats.image_context_tokens),
            )
        return projected, stats
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        projected_values: list[object] = []
        stats = MultimodalContextStats()
        for nested in value:
            projected_value, nested_stats = _project(nested)
            projected_values.append(projected_value)
            stats = MultimodalContextStats(
                image_count=stats.image_count + nested_stats.image_count,
                decoded_bytes=stats.decoded_bytes + nested_stats.decoded_bytes,
                image_context_tokens=(stats.image_context_tokens + nested_stats.image_context_tokens),
            )
        return projected_values, stats
    return value, MultimodalContextStats()


def project_multimodal_for_summary(value: object) -> object:
    """Replace image bodies with bounded metadata suitable for Compact."""
    projected, _ = _project(value)
    return projected


def multimodal_context_stats(value: object) -> MultimodalContextStats:
    """Return image count, decoded bytes, and unified image-context tokens."""
    _, stats = _project(value)
    return stats


def estimate_multimodal_tokens(
    value: object,
    *,
    chars_per_token: int,
    utf8_bytes: bool = False,
) -> int:
    """Estimate text plus image context without counting Base64 as text."""
    if chars_per_token <= 0:
        raise ValueError("chars_per_token must be positive")
    projected, stats = _project(value)
    serialized = json.dumps(
        projected,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    length = len(serialized.encode("utf-8")) if utf8_bytes else len(serialized)
    return max(
        1,
        math.ceil(length / chars_per_token) + stats.image_context_tokens,
    )


__all__ = [
    "ImageContextInfo",
    "MultimodalContentError",
    "MultimodalContextStats",
    "estimate_multimodal_tokens",
    "multimodal_context_stats",
    "parse_multimodal_content",
    "project_multimodal_for_summary",
    "text_only_multimodal_content",
]
