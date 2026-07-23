"""Canonical multimodal parsing and context-budget tests."""

from __future__ import annotations

import base64
from io import BytesIO
import json
import re

from PIL import Image
import pytest

from app.services.llm.multimodal_content import (
    MultimodalContentError,
    estimate_multimodal_tokens,
    multimodal_context_stats,
    parse_multimodal_content,
    project_multimodal_for_summary,
)


def _data_url(
    width: int,
    height: int,
    *,
    image_format: str = "PNG",
    quality: int = 90,
) -> str:
    output = BytesIO()
    Image.new("RGB", (width, height), color=(40, 90, 130)).save(
        output,
        format=image_format,
        quality=quality,
    )
    mime = "jpeg" if image_format == "JPEG" else image_format.lower()
    encoded = base64.b64encode(output.getvalue()).decode("ascii")
    return f"data:image/{mime};base64,{encoded}"


def test_legacy_marker_becomes_standard_multimodal_content() -> None:
    data_url = _data_url(16, 12)

    parsed = parse_multimodal_content(f"[image_data:{data_url}]\nDescribe this image")

    assert isinstance(parsed, list)
    assert parsed == [
        {"type": "image_url", "image_url": {"url": data_url}},
        {"type": "text", "text": "Describe this image"},
    ]
    assert data_url not in parsed[1]["text"]


def test_image_context_uses_dimensions_instead_of_compressed_bytes() -> None:
    png = _data_url(1000, 1000, image_format="PNG")
    jpeg = _data_url(1000, 1000, image_format="JPEG", quality=30)

    png_stats = multimodal_context_stats([{"type": "image_url", "image_url": {"url": png}}])
    jpeg_stats = multimodal_context_stats([{"type": "image_url", "image_url": {"url": jpeg}}])

    assert png_stats.image_context_tokens == 1296
    assert jpeg_stats.image_context_tokens == 1296
    assert png_stats.decoded_bytes != jpeg_stats.decoded_bytes


def test_oversized_image_effective_dimensions_obey_both_caps() -> None:
    data_url = _data_url(4000, 3000, image_format="JPEG")

    projected = project_multimodal_for_summary([{"type": "image_url", "image_url": {"url": data_url}}])
    stats = multimodal_context_stats([{"type": "image_url", "image_url": {"url": data_url}}])

    assert stats.image_context_tokens <= 1568
    serialized = json.dumps(projected)
    matched = re.search(r"effective_dimensions=(\d+)x(\d+)", serialized)
    assert matched is not None
    effective_width, effective_height = map(int, matched.groups())
    assert max(effective_width, effective_height) <= 1568
    assert "data:image/" not in serialized


def test_multiple_images_add_context_without_counting_base64_as_text() -> None:
    first = _data_url(1000, 1000, image_format="PNG")
    second = _data_url(560, 280, image_format="JPEG")
    content = [
        {"type": "image_url", "image_url": {"url": first}},
        {"type": "image_url", "image_url": {"url": second}},
        {"type": "text", "text": "Compare them"},
    ]

    stats = multimodal_context_stats(content)
    estimated = estimate_multimodal_tokens(content, chars_per_token=3)

    assert stats.image_count == 2
    assert stats.image_context_tokens == 1296 + 200
    assert estimated < stats.image_context_tokens + 200


def test_compact_projection_never_contains_image_base64() -> None:
    data_url = _data_url(64, 64)
    value = {
        "message": {
            "role": "user",
            "content": f"[image_data:{data_url}] inspect",
        }
    }

    projected = project_multimodal_for_summary(value)
    serialized = json.dumps(projected, ensure_ascii=False)

    assert "base64," not in serialized
    assert "image omitted from compact prompt" in serialized
    assert "inspect" in serialized


def test_invalid_image_data_is_rejected_with_a_stable_code() -> None:
    invalid = base64.b64encode(b"not an image").decode("ascii")

    with pytest.raises(MultimodalContentError) as raised:
        parse_multimodal_content(f"[image_data:data:image/png;base64,{invalid}]")

    assert raised.value.code == "invalid_image_data"
