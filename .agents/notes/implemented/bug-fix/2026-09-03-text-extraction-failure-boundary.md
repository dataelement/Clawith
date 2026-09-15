# Agent Note: Text Extraction Failure Boundary

Status: implemented — expected document parser failures remain ordinary extraction failures, while implementation defects propagate.

## Problem

`extract_text` caught every `Exception` and returned `None`. Corrupt or unsupported document data should produce that bounded failure result, but the same catch also hid defects in the extraction implementation and made them indistinguishable from invalid input.

## Decision

Each supported file type defines the parser exceptions that represent an expected extraction failure. These include the format library's exception family plus malformed archive, XML, value, key, end-of-file, and I/O errors used by the parsers. `extract_text` logs these failures and returns `None`, preserving its existing invalid-document result.

Exceptions outside those parser families propagate so the owning caller and diagnostics can treat them as implementation failures.

## Alternatives considered

- Continue catching every exception. Rejected because it hides implementation defects as invalid files.
- Remove extraction failure normalization entirely. Rejected because invalid and unsupported document contents are an expected input-boundary outcome.

## Consequences

Callers still receive `None` for supported parser failures. Unexpected defects no longer disappear behind the same result. Adding another parser requires adding its documented input-failure exception family to this boundary.

## Verification

- `uv run --extra dev pytest tests/test_text_extractor.py`
- `uv run --extra dev ruff check app/services/text_extractor.py tests/test_text_extractor.py`
- `uv run --extra dev pyright app/services/text_extractor.py tests/test_text_extractor.py`
- `uv run --extra dev pytest --collect-only -q`

The focused tests cover all four format dispatch paths, one supported parser failure, and one unexpected implementation failure. They do not parse real PDF, DOCX, XLSX, or PPTX fixtures.
