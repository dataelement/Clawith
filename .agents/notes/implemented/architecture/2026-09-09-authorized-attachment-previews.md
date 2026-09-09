# Agent Note: Explicit bounded attachment previews

Status: implemented — explicit read and save Tools consume application-owned authorization ports; attachment persistence and Workspace mutations remain with their existing owners.

## Problem

A four-MiB original image cannot be inserted into a 256-KiB Tool Result unchanged. Inferring media from a Tool name or arbitrary JSON shape would reinterpret unrelated results. Truncating text without a continuation offset would make the remainder inaccessible.

## Decision

The `read_attachment` Builtin explicitly declares content-block output through its captured Tool Definition. Its injected reader receives the trusted Run scope and an opaque reference; the reader owns authorization and original-blob retrieval. The executor does not fetch URLs, read Workspace paths or modify the original file.

UTF-8 text is read in pages of at most 32768 Unicode code points. Every result identifies its offset unit, current offset, next offset and source SHA-256. Non-image output is labelled `raw_utf8` with `document_text_extracted=false`; decoding a PDF or Office file as UTF-8 does not claim document-text extraction. Images produce a labelled first-frame JPEG preview, with source dimensions, preview dimensions, compression quality and source hash. The original is bounded to four MiB, decoded images to sixteen million pixels, previews to a 1024-pixel edge and the complete Tool Result to 256 KiB. Unsupported binary files and malformed images return explicit errors.

The application supplies one shared two-slot CPU semaphore. Preview work runs off the event loop; cancellation drains its actual worker before releasing the slot. No per-Run pool or unbounded image-worker queue is created by the executor.

`save_attachment` invokes the application save port only after an explicit Tool call. It forwards the trusted Run scope, source reference, destination path and required expected revision. Null requests create-only behavior; replacement requires the current revision. The application authorizes the source and writes its unchanged original bytes to the Run's current ordinary `files/` output through Workspace. The Tool neither decodes binary content nor accepts replacement content from model JSON. Workspace conflicts retain the current revision, uncertain mutations remain uncertain, and the executor does not retry them. Reading an attachment never calls the save port.

## Alternatives considered

Embedding the original image could exceed Tool and Model input bounds. Silent text truncation would lose access to content. Inferring media from names would couple Run to individual Tools. These approaches are not used.

## Consequences

The model sees an explicitly reduced image rather than an assertion that it read the original pixels. The original remains available to its attachment owner. The optional result-format declaration uses existing Tool configuration and Snapshot v1 fields; omitted declarations preserve the historical encoding and hash.

## Verification

Focused tests exercise Unicode page reconstruction, source hashes and image dimensions, explicit image projection, malformed arguments, owner denial and repeated cancellation while two real worker threads remain active. Save-adapter tests verify explicit revision arguments, unchanged binary delegation, absence of implicit saves, authorization denial and distinct conflict/uncertain outcomes. Snapshot tests verify omitted old fields and declared-field round trips. Product upload, storage authorization, actual Workspace saving and application end-to-end delivery require their own integration evidence.
