# Agent Note: Fixed Tool bindings and account-scoped MCP

Status: implemented — Tool configuration, resolution, scheduling and MCP adapters are available; Builtin and Runner assembly remain separate.

## Problem

Shared Tool registration must not imply shared credentials or identical account-specific capabilities. An executing Run also needs stable exposure and normalized results without querying current grants after each call.

## Decision

Tool keeps Definitions, grants and MCP connections private and exposes typed operations through `public.py`. Configuration flushes only the caller's transaction and performs no network I/O. Self-install uses a trusted Agent scope, binds only that Agent and records no fabricated human grantor. Credential metadata is checked through its owning public service against the exact Tenant and owner tuple before Secret resolution.

Resolution captures an immutable authorized Tool set, selected account-local MCP schemas and explicit Credential bindings. The Agent account is the default. A personal account requires an explicitly selected, authorized connection; unavailable selection does not switch accounts. Source availability is an injected bounded query over the same transaction. Source disablement affects new resolution, not a captured set. Search and exposure operate only within that captured set.

MCP definitions reuse stable Catalog, canonical name, upstream name and executor identity. Different accounts retain their discovered descriptions and schemas without overwriting shared metadata. Non-MCP definition conflicts remain explicit. Builtin definitions must match their code-owned executor binding; database rows cannot redefine them.

The scheduler preserves call/result order and serial barriers. Only explicitly safe executors run in bounded parallel groups. Ordinary capability failures return bounded Tool Results; uncertain external effects are not replayed. Cancellation cancels and awaits active work. Programming defects remain defects.

MCP supports explicitly selected Streamable HTTP and legacy SSE, initialization, bounded discovery and calls through the application-owned stateless HTTP pool. Neither transport guessing nor default client authentication supplies account policy. Context closure releases streams and attempts server-session cleanup without undoing a completed Tool effect.

## Alternatives considered

Requiring identical schemas for the same MCP identity was rejected because different credentials can expose different capabilities. Sharing account discovery or silently switching credentials would violate the selected account boundary. A Tool execution ledger was excluded by the approved architecture.

## Consequences

`ToolSearchExecutor` supplies the code-owned `search_tools` executor over a fixed Run-scoped set. A successful search exposes matching definitions only for subsequent requests and batches. Its result returns names; subsequent model requests obtain schemas from the updated view without repeating schema payloads in the Tool Result. Search changes exposure, not authorization or installation, and malformed or wrong-Run calls leave the view unchanged. Other concrete Builtins, Task/Todo, A2A and actual Runner integration remain in their owning stages. OAuth negotiation, optional MCP resource/prompt APIs and hosted-server compatibility are not implied by the implemented transport adapters.

## Verification

The joint storage, Workspace, Tool and Market suite passed 144 tests. Tests exercise account selection, cross-Agent denial, stable MCP identity, shared HTTP isolation, SSE transport, fixed source views, scheduling and normalized outcomes. Independent code and architecture reviews found no remaining service-slice blocker. Real HTTP peers are controlled transports; no deployment or 50-Agent acceptance is claimed.
