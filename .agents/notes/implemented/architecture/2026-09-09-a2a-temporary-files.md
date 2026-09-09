# Agent Note: Keep A2A working files under the request until their return is saved

Status: implemented — request-owned temporary publication, guarded storage, Main/Child Tools, nested return copies and source-side saves.

## Problem

An A2A receiver must process private delegated files without writing them into its shared Agent Workspace. Returning a mutable path would also allow later writes or premature cleanup to change or erase the result before the source saves it.

## Decision

A2A keeps a closed versioned temporary-file manifest on its existing request. Logical filenames select internal storage keys, never caller-supplied filesystem paths. The manifest admits eight files, four MiB per file, sixteen MiB total and 64 KiB of metadata. Pending publication counts toward the total. A2A owns authorization, publication intent, frozen returns, save receipts and cleanup claims; an injected storage port supplies guarded CAS and bounded coherent reads without owning product lifecycle or closing the shared backend.

The target Main and its Children may process their request's temporary files. Publication records its intended content digest, size and expected revision before physical I/O, then verifies the actual revision before acknowledging completion. Unresolved publication remains owned after failure or cancellation. A returned file freezes its exact revision and cannot be rewritten. Different result content requires another logical file.

A later Tool call may confirm an unresolved publication only when logical name, expected revision, digest, size and media type exactly match. It consumes the original recorded publication intent rather than replacing its operation identity. Different content remains a conflict, and an old confirmation cannot replace a newer pending intent. An actual write followed by a provider error can therefore be reconciled through a new model call without rewriting confirmed bytes or requiring the model to recover a private operation identifier.

Text reads paginate by Unicode code-point offset within the complete 64 KiB serialized JSON budget, including file metadata and escaping. A page always advances when content remains; `next_offset` is null only at the end. Chinese, emoji and control-character-heavy files are reconstructed without dropping continuation text. Binary metadata remains distinct from text extraction.

Only the effective delivery Main may read returned files and save them through its actual captured output Workspace. A new save requires the existing Workspace CAS condition. A confirmed save returns its recorded receipt without rewriting the file. An unresolved prior save may be confirmed only from matching observed destination bytes; a differing destination is not permission to overwrite. Explicit same-conversation takeover remains owned by A2A's delivery policy, not the file manifest.

When B delegates further to C, B imports C's returned file into B's own request-local file using both request authorizations. Original binary bytes are preserved. C's return becomes cleanup-eligible only after B's manifest confirms the exact copied revision, digest and size; merely reading C's file does not acknowledge its delivery. B may then process and return its own file without acquiring a shared Workspace write grant.

Terminal A2A delivery appends the bounded safe returned-file list to correlated Run input. Authorized `send_message_to_agent` inspection exposes the same name, media type, byte size, hash and revision projection. Storage keys, pending publication and save-intent details are excluded. The request's existing closed result payload is unchanged; file authority remains in the manifest. A discovers B's chosen names through these consumers instead of relying on an agreed filename or prose in B's Final.

Cleanup may claim unreturned files after the producing family terminates, or returned files after their source save is confirmed. It never expires an unsaved returned file merely because either Agent ended. Cleanup checks known published or pending content and conditionally deletes only the observed revision. Claims survive interruption. A lost save receipt cannot justify deleting the only retained return.

## Alternatives considered

Writing delegated work into B's shared Workspace was rejected because that would expose task-private files. A new Workspace type or Artifact table would duplicate ownership already available on the A2A request. Immutable per-revision blobs would require additional retained-garbage bookkeeping; guarded CAS keeps one physical key per logical temporary file.

## Consequences

The receiving Agent's Workspace scope separately denies shared-file mutations; hiding Tools alone does not enforce this boundary. File manifests do not resume old Runs. Source references and save receipts are product facts, not a Task state machine. Returned files can remain retained indefinitely when no source has confirmed saving them.

## Verification

Owner tests use real PostgreSQL for publication/return/save/cleanup transitions, conflicting writes, wrong-source denial and manifest bounds. Storage tests use Local files and native S3 clients against controlled HTTP with PostgreSQL locks for create/replace CAS, immutable retry content, stale deletion and byte bounds. Model-visible import, Child use, source save, cancellation and application cleanup require their assembled execution tests; these checks do not establish live S3 or formal platform capacity.

Application tests verify actual Workspace conflicts, cancellation after a physical save but before its receipt, byte-based confirmation without repeating that write, and conditional cleanup that retains unknown bytes while cleaning another file. The controlled Model E2E suite exercises direct B processing, B's real Child, and an A→B→C binary return chain through registered Tools and the original User Workspace. It verifies B's shared-file denial, frozen-return rewrite rejection, idempotent source save and physical temporary cleanup. These tests do not call a live Model or external S3 service.
