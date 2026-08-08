from pathlib import Path

import pytest

from app.api import upload


@pytest.mark.parametrize("extension", [".pdf", ".docx", ".xlsx", ".xls"])
def test_office_extraction_uses_file_bytes_for_adversarial_filename(
    monkeypatch, tmp_path: Path, extension: str
) -> None:
    """Uploaded names are data, never part of Python source passed to a subprocess."""
    file_path = tmp_path / f"report');__import__('os').system('id');#{extension}"
    file_path.write_bytes(b"not-a-real-pdf")
    captured: dict[str, object] = {}

    def fake_extract(file_bytes: bytes, filename: str) -> str:
        captured["file_bytes"] = file_bytes
        captured["filename"] = filename
        return "safe extracted text"

    monkeypatch.setattr(upload, "extract_document_text", fake_extract)

    assert upload.extract_text(file_path, extension) == "safe extracted text"
    assert captured == {"file_bytes": b"not-a-real-pdf", "filename": file_path.name}
