import pytest
from pdfminer.pdfexceptions import PDFException

from app.services import text_extractor


@pytest.mark.parametrize(
    ("extension", "extractor_name"),
    [
        (".pdf", "_extract_pdf"),
        (".docx", "_extract_docx"),
        (".xlsx", "_extract_xlsx"),
        (".pptx", "_extract_pptx"),
    ],
)
def test_extract_text_passes_bytes_without_interpreting_adversarial_filename(
    monkeypatch: pytest.MonkeyPatch,
    extension: str,
    extractor_name: str,
) -> None:
    file_bytes = b"not-a-real-document"
    filename = f"report');__import__('os').system('id');#{extension}"
    captured: list[bytes] = []

    def fake_extract(data: bytes) -> str:
        captured.append(data)
        return "safe extracted text"

    monkeypatch.setattr(
        text_extractor,
        extractor_name,
        fake_extract,
    )

    assert text_extractor.extract_text(file_bytes, filename) == "safe extracted text"
    assert captured == [file_bytes]


def test_extract_text_returns_none_for_supported_parser_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_pdf(_data: bytes) -> str:
        raise PDFException("malformed PDF")

    monkeypatch.setattr(text_extractor, "_extract_pdf", fail_pdf)

    assert text_extractor.extract_text(b"malformed", "report.pdf") is None


def test_extract_text_does_not_hide_internal_defects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_pdf(_data: bytes) -> str:
        raise RuntimeError("implementation defect")

    monkeypatch.setattr(text_extractor, "_extract_pdf", fail_pdf)

    with pytest.raises(RuntimeError, match="implementation defect"):
        text_extractor.extract_text(b"malformed", "report.pdf")
