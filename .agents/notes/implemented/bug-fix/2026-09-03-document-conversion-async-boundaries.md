# Agent Note: Document Conversion Async Boundaries

Status: implemented — HTML conversion no longer blocks the application event loop while starting Chrome or querying Chrome DevTools.

## Problem

The HTML-to-PDF and HTML-to-PPTX conversion paths are asynchronous, but they started Chrome with `subprocess.Popen` and queried Chrome DevTools with synchronous HTTP calls. One conversion could therefore block unrelated async work. Process cleanup also swallowed every exception and did not wait for a force-killed process to exit.

## Decision

Document conversion starts its owned Chrome process with `asyncio.create_subprocess_exec`. Synchronous Chrome DevTools discovery requests run in a worker thread; the WebSocket rendering protocol remains asynchronous.

The conversion owner terminates Chrome and waits for exit. If graceful termination exceeds two seconds, it kills and then reaps the process. Cleanup still runs when discovery, navigation, rendering, or result parsing fails. The Chrome attempt is an optional enhancement, so its fallback boundary deliberately contains every browser, HTTP, protocol, and parsing exception: PDF uses WeasyPrint, while PPTX uses DOM-flow rendering. Public conversion functions continue to normalize converter failures into their bounded string result.

## Alternatives considered

- Keep synchronous process and HTTP calls inside the async functions. Rejected because they can stall concurrent Agent work.
- Move the entire conversion into a worker thread. Rejected because Chrome rendering already uses an asynchronous WebSocket protocol and only the blocking discovery operations need isolation.
- Drop the existing fallback conversions. Rejected because this cleanup preserves the reviewed document-conversion capability rather than changing its product contract.

## Consequences

Chrome startup, discovery, and shutdown no longer monopolize the event loop. A force-killed Chrome process is reaped before conversion cleanup completes. The selected rendering order and user-visible success or failure result remain unchanged.

## Verification

- `uv run --extra dev pytest tests/test_html_to_pdf.py`
- `uv run --extra dev ruff check app/services/document_conversion tests/test_html_to_pdf.py`
- `uv run --extra dev pyright app/services/document_conversion tests/test_html_to_pdf.py`
- `uv run --extra dev pytest --collect-only -q`

The tests cover Linux and macOS Chrome arguments, Chrome timeout fallback, malformed DevTools HTTP response fallback for PDF and PPTX, no-Chrome fallback, graceful termination, and kill-then-reap cleanup. They do not execute a real local Chrome, WeasyPrint, or PowerPoint renderer.
