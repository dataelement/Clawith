# Agent Note: Fixed Tool bindings and account-scoped MCP

Status: implemented — Tool configuration, resolution, scheduling, MCP adapters and Workspace/search Builtins are connected to per-Run assembly.

## Problem

Shared Tool registration must not imply shared credentials or identical account-specific capabilities. An executing Run also needs stable exposure and normalized results without querying current grants after each call.

## Decision

Tool keeps Definitions, grants and MCP connections private and exposes typed operations through `public.py`. Configuration flushes only the caller's transaction and performs no network I/O. Self-install uses a trusted Agent scope, binds only that Agent and records no fabricated human grantor. Credential metadata is checked through its owning public service against the exact Tenant and owner tuple before Secret resolution.

Resolution captures an immutable authorized Tool set, selected account-local MCP schemas and explicit Credential bindings. The Agent account is the default. A personal account requires an explicitly selected, authorized connection; unavailable selection does not switch accounts. Source availability is an injected bounded query over the same transaction. Source disablement affects new resolution, not a captured set. Search and exposure operate only within that captured set.

`PersonalAccountSelection` and its closed versioned codec identify exact connections for one target Agent. Human product intake must call `validate_personal_selections`, which reuses execution capture to check Membership ownership, target Agent availability, grants and selected account executability. It does not resolve Secret bytes. `capture_authorized` itself requires every explicitly selected personal Tool to be present in its resolved set; a later source or definition disablement therefore fails capture rather than silently omitting the user's selected account. Product callers must persist validated selections separately from model text and supply only those exact references when the named Agent executes. This Tool contract does not by itself establish Session or Group integration.

`capture_authorized` retains all eligible grants before Run-role filtering, including Subagent-only Todo. `AuthorizedToolSet.for_role` derives executable Main/Subagent views entirely in memory and intersects direct exposure with the remaining names. Main excludes Todo; Subagent excludes Task, A2A and Memory distillation. The existing `resolve` entry composes capture and role derivation rather than defining another policy. The capture has no direct exposure or execution methods; it cannot add ungranted core Tools.

MCP definitions reuse stable Catalog, canonical name, upstream name and executor identity. Different accounts retain their discovered descriptions and schemas without overwriting shared metadata. Non-MCP definition conflicts remain explicit. Builtin definitions must match their code-owned executor binding; database rows cannot redefine them.

Concurrent registration of the same canonical name or Agent grant reads and validates the committed winner after an insert-if-absent operation. It does not overwrite metadata or Credential bindings, restore revoked grants, or retry an external effect. Incompatible definitions/bindings and unsupported persisted grant configurations still fail at Tool's owning boundary. [Explicit Builtin provisioning](2026-09-07-explicit-builtin-provisioning.md) uses these operations in the caller's creation transaction.

The scheduler preserves call/result order and serial barriers. Only explicitly safe executors run in bounded parallel groups. Ordinary capability failures return bounded Tool Results; uncertain external effects are not replayed. Cancellation cancels and awaits active work. Programming defects remain defects.

MCP supports explicitly selected Streamable HTTP and legacy SSE, initialization, bounded discovery and calls through the application-owned stateless HTTP pool. Neither transport guessing nor default client authentication supplies account policy. Context closure releases streams and attempts server-session cleanup without undoing a completed Tool effect.

`tool_result_content` exposes a bounded text/image presentation of MCP results and Definitions explicitly declaring `result_format="content_blocks"`. It preserves text and image ordering, validates image MIME and Base64, retains unhandled blocks and metadata as text, and does not duplicate image bytes into that text. Undeclared non-MCP JSON remains opaque text, even when its shape or Tool name resembles media. The optional declaration persists in existing Definition configuration and Run Snapshot; a missing value is omitted from Snapshot v1 serialization so historical hashes remain unchanged. The function performs no I/O and does not rewrite the authoritative Tool Result; Run decides whether the captured exposed definition is eligible and retains original History. Invalid media presentation is a contained Tool-view error, not permission to replay the external call.

## Alternatives considered

Requiring identical schemas for the same MCP identity was rejected because different credentials can expose different capabilities. Sharing account discovery or silently switching credentials would violate the selected account boundary. A Tool execution ledger was excluded by the approved architecture.

Deriving Child authorization from a filtered Main view loses Todo. Resolving live grants when a Child starts would admit authorization changes after Parent capture. One role-independent capture avoids both without storing duplicate Main/Subagent catalogs.

## Consequences

`ToolSearchExecutor` supplies the code-owned `search_tools` executor over a fixed Run-scoped set. A successful search exposes matching definitions only for subsequent requests and batches. Its result returns names; subsequent model requests obtain schemas from the updated view without repeating schema payloads in the Tool Result. Search changes exposure, not authorization or installation, and malformed or wrong-Run calls leave the view unchanged. [Workspace Builtins](2026-09-07-workspace-builtin-composition.md), persisted provisioning and [Run composition](2026-09-08-run-tool-and-application-composition.md) are implemented. A2A remains product-stage work. OAuth negotiation, optional MCP resource/prompt APIs and hosted-server compatibility are not implied by the implemented transport adapters.

## Verification

The joint storage, Workspace, Tool and Market suite passed 144 tests. Tests exercise account selection, cross-Agent denial, stable MCP identity, shared HTTP isolation, SSE transport, fixed source views, scheduling and normalized outcomes. Independent code and architecture reviews found no remaining service-slice blocker. Real HTTP peers are controlled transports; no deployment or 50-Agent acceptance is claimed.

Capture tests compare Main/Subagent views, revoke and add grants after capture, and verify that only a fresh capture changes. They exercise role-ineligible calls through the real scheduler, cross-Tenant capture rejection, duplicate/count limits and invalid roles. This verifies the Tool-side inheritance prerequisite, not persisted Run Snapshot or Child creation.

The Tool suite passed 38 tests on an isolated export of the staged source, excluding deferred MCP import drafts. Package/import guards passed 110 tests; scoped Ruff and Pyright passed. Independent code and architecture reviewers approved the capture slice.
