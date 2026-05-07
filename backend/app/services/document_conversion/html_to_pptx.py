"""HTML to PPTX conversion service."""

from pathlib import Path
from typing import Any

from app.services.document_conversion.pptx_renderer import render_html_to_pptx


async def convert_html_to_pptx(src_file: Path, tgt_file: Path, target_path: str, ws: Path, arguments: dict[str, Any]) -> str:
    return await render_html_to_pptx(src_file, tgt_file, target_path, ws, arguments)
