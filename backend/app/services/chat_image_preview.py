"""Bounded previews for image-bearing chat messages."""

import base64
from io import BytesIO

from PIL import Image, ImageOps, UnidentifiedImageError

MAX_VISION_IMAGE_BYTES = 512 * 1024
MAX_VISION_IMAGE_EDGE = 1568
MAX_VISION_IMAGE_PIXELS = 40_000_000

_MIME_BY_EXTENSION = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".bmp": "image/bmp",
}

_MIME_BY_FORMAT = {
    "BMP": "image/bmp",
    "GIF": "image/gif",
    "JPEG": "image/jpeg",
    "PNG": "image/png",
    "WEBP": "image/webp",
}


class VisionImagePreviewError(ValueError):
    """The uploaded image cannot be converted into a safe preview."""


def build_vision_image_data_url(content: bytes, extension: str) -> str:
    """Return a bounded data URL while leaving the stored original intact."""
    try:
        with Image.open(BytesIO(content)) as opened:
            if opened.width * opened.height > MAX_VISION_IMAGE_PIXELS:
                raise VisionImagePreviewError("Image dimensions are too large")
            source_format = opened.format
            opened.load()
            image = ImageOps.exif_transpose(opened)
            if (
                len(content) <= MAX_VISION_IMAGE_BYTES
                and max(image.size) <= MAX_VISION_IMAGE_EDGE
            ):
                mime = _MIME_BY_FORMAT.get(
                    source_format, _MIME_BY_EXTENSION.get(extension, "image/png")
                )
                encoded = base64.b64encode(content).decode("ascii")
                return f"data:{mime};base64,{encoded}"

            if image.mode in {"RGBA", "LA"} or (
                image.mode == "P" and "transparency" in image.info
            ):
                rgba = image.convert("RGBA")
                background = Image.new("RGB", rgba.size, "white")
                background.paste(rgba, mask=rgba.getchannel("A"))
                image = background
            else:
                image = image.convert("RGB")

            image.thumbnail(
                (MAX_VISION_IMAGE_EDGE, MAX_VISION_IMAGE_EDGE),
                Image.Resampling.LANCZOS,
            )
            quality = 85
            for _ in range(16):
                output = BytesIO()
                image.save(output, format="JPEG", quality=quality, optimize=True)
                preview = output.getvalue()
                if len(preview) <= MAX_VISION_IMAGE_BYTES:
                    encoded = base64.b64encode(preview).decode("ascii")
                    return f"data:image/jpeg;base64,{encoded}"
                if quality > 55:
                    quality -= 10
                else:
                    width, height = image.size
                    image = image.resize(
                        (max(1, int(width * 0.8)), max(1, int(height * 0.8))),
                        Image.Resampling.LANCZOS,
                    )
                    quality = 75
    except (OSError, UnidentifiedImageError, ValueError) as exc:
        raise VisionImagePreviewError("Invalid or unsupported image") from exc

    raise VisionImagePreviewError("Could not create a bounded image preview")
