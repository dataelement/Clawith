# Agent Note: On-demand document extraction in bounded child processes

Status: implemented — the document Tool consumes an application-authorized reader and extracts text in isolated processes; live-model and deployment qualification remain separate.

## Problem

PDF and Office parsing can consume excessive CPU or memory and cannot be safely cancelled by abandoning a thread. Automatically extracting uploads into Workspace would also restore an explicitly removed ownership boundary.

## Decision

The `read_document` Builtin reads an explicit attachment reference, the current Workspace's ordinary `files/` path, or `temporary:filename` in the current A2A work through an injected application reader. Temporary references pass through A2A's current-request authorization and immutable-file reader before reaching the same parser. A filename is not authority for another Run, and extraction does not copy B's temporary document into B's shared Workspace. The Tool never opens a caller-selected path or URL itself. Existing pdfplumber, python-docx, openpyxl and python-pptx libraries extract embedded text and tables; DOCX extraction also includes body text boxes and unlinked section headers and footers. UTF-8 text is supported without Office conversion. No OCR, audio transcription, automatic Workspace import or persistent extracted Artifact is created.

One application-owned parser admits two active processes and at most 32 additional operations. Waiting for a processing slot is limited to ten seconds; worker execution is limited to fifteen seconds. Input is at most four MiB. The worker receives bytes on stdin, runs through the installed interpreter in isolated mode with a minimal environment, and returns only a bounded versioned JSON response. Source names and file bytes do not enter process arguments.

Office archives allow at most 2048 distinct members, eight MiB per member and 32 MiB total expanded size. PDF and presentation extraction processes at most 100 pages/slides. Worksheet, row, column, shape and table-cell limits constrain structure traversal. Extracted text is capped at 262144 Unicode code points and returned in pages of at most 16000 code points. Results include `next_offset`, its offset unit, source hash, processed units, and an explicit `truncated` reason when an extraction bound is reached. A truncated prefix never claims to be the whole document. Worker stdout and the complete Tool Result are each bounded to 256 KiB.

The parent samples worker RSS every 50 milliseconds and kills workers exceeding a 512-MiB supervision budget. Linux also applies a hard address-space limit. Darwin rejects the corresponding resource limit, so its evidence is RSS supervision, not a hard allocation guarantee; sampling may allow transient overshoot. Timeout, cancellation and resource failure kill and await the actual process before releasing its processing slot. Application shutdown drains Runtime callers before closing the parser. These controls contain resource consumption and parser failures but are not a filesystem or network security sandbox.

Each worker has one shared cleanup task for normal completion, failure, cancellation and parser closure. Cleanup first cancels and joins its monitor and protocol-exchange tasks so only one reader owns stdout. It closes stdin, kills any remaining process, and concurrently waits for process exit, input-pipe closure and stdout drainage. Drainage discards output in 64-KiB chunks without retaining it as a result. A killed process can already report an exit code while `Process.wait()` still waits for a saturated pipe to close; kill followed by wait alone is therefore insufficient. Repeated cancellation cannot release a parser slot before this cleanup finishes, and cancellation of parser closure still allows the other registered workers to be reclaimed.

## Alternatives considered

An abandoned worker thread could continue consuming resources after timeout. An unbounded process-per-call design could exhaust the host. Stopping stdout consumption before a kill-and-wait sequence could deadlock cleanup on a full pipe. Copying extracted companions into Workspace would create unwanted files and duplicate source ownership. No fallback parser or new persistent Artifact owner is used.

## Consequences

Encrypted, malformed, oversized or excessively complex documents return bounded Tool errors without failing unrelated model work. Parsing depends on the original immutable bytes and is repeated when another output page is requested; no extraction cache or lifecycle is introduced. Spreadsheet extraction uses cached values rather than executing formulas, and external workbook links are not followed.

## Verification

Tests generate real PDF, DOCX, XLSX, PPTX and UTF-8 documents and invoke the actual child interpreter and libraries. They verify content, DOCX headers/footers/text boxes, pagination, explicit truncation, Unicode wire bounds, oversized archive rejection and source-authorization denial. Hung or overproducing test workers exercise timeout, repeated cancellation, closure, output limits and admission capacity, with actual process disappearance checked. Four-MiB stdout producers also verify reclamation of saturated pipes and reuse of the released parser slot after timeout or cancellation. An injected excessive RSS sample verifies that the supervisor terminates a real worker without allocating a hostile amount of host memory. Linux hard-limit and live-model qualification are not established by Darwin tests.

`tests/e2e/test_document_input.py` verifies all four document formats through authenticated ASGI uploads, real PostgreSQL and storage, Tool discovery, isolated extraction, subsequent Model requests and Run completion. Its eight cases cover reading the original attachment and reading a current-Workspace file created through explicit `save_attachment`. The Model HTTP peer is controlled; the original input contains no extracted document text. The focused worker suite and these application cases pass 27 tests together.

`tests/e2e/test_a2a_temporary_document.py` verifies PDF and DOCX imports through Model-issued A2A file Tools followed by `read_document` on the temporary reference. While B and its stored temporary file remain active, an ordinary Session Run of the same Agent is denied access to the same filename. B's shared file directory remains empty before and after completion. The test uses real authorization, storage and parsing with a controlled Model peer; no automatic shared-Workspace copy is involved.
