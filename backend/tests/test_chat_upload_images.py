"""Regression tests for bounded chat image payloads."""

import base64
import os
from io import BytesIO

from PIL import Image

from app.services.chat_image_preview import (
    MAX_VISION_IMAGE_BYTES,
    MAX_VISION_IMAGE_EDGE,
    build_vision_image_data_url,
)


def _decode_data_url(data_url: str) -> bytes:
    return base64.b64decode(data_url.split(",", 1)[1])


def test_small_chat_image_keeps_original_encoding() -> None:
    output = BytesIO()
    Image.new("RGB", (32, 24), "red").save(output, format="PNG")
    original = output.getvalue()

    data_url = build_vision_image_data_url(original, ".png")

    assert data_url.startswith("data:image/png;base64,")
    assert _decode_data_url(data_url) == original


def test_large_chat_image_is_bounded_for_multi_image_messages() -> None:
    image = Image.frombytes("RGB", (2400, 1800), os.urandom(2400 * 1800 * 3))
    original = BytesIO()
    image.save(original, format="JPEG", quality=100)
    assert len(original.getvalue()) > MAX_VISION_IMAGE_BYTES

    data_url = build_vision_image_data_url(original.getvalue(), ".jpg")
    preview = _decode_data_url(data_url)

    assert data_url.startswith("data:image/jpeg;base64,")
    assert len(preview) <= MAX_VISION_IMAGE_BYTES
    with Image.open(BytesIO(preview)) as bounded:
        assert max(bounded.size) <= MAX_VISION_IMAGE_EDGE
