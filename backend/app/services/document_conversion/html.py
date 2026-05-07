"""Compatibility exports for HTML document conversion helpers."""

from app.services.document_conversion.html_to_pdf import convert_html_to_pdf
from app.services.document_conversion.html_to_pptx import convert_html_to_pptx

__all__ = ["convert_html_to_pdf", "convert_html_to_pptx"]
