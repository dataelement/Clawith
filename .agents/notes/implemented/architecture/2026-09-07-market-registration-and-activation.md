# Agent Note: Separate capability registration from Agent activation

Status: implemented — Market discovery and installation orchestration use public Tool and Workspace services; model-callable source preparation remains separate.

## Problem

A Tenant should register a capability once without implicitly installing it for every Agent. Registration may succeed while a later account-specific binding or Skill publication fails; one success flag would hide that distinction.

## Decision

Market owns bounded Platform/Tenant discovery metadata and normalized source identity. Platform templates materialize into a deduplicated Tenant record before installation. Catalog records do not contain executable credentials or become another Skill content authority.

Installation first establishes the source, then invokes the public Tool or Workspace owner to activate it for the selected Agent. External preparation occurs before the binding transaction. Results distinguish registration from activation, so an activation failure can leave a valid registered source. Agent self-install receives an already authorized scope and cannot act as an administrator or another Agent.

Shared Skill refresh updates the shared package through Workspace without rebinding an Agent's private fork. Private updates affect only the selected Agent. MCP installation passes explicit transport and account selection through Tool; Catalog reuse does not share account credentials or discovery.

Source-backed Tool and Skill resolution inject Market's bounded `enabled_source_ids` query. It uses the caller's existing TransactionContext and neither borrows another database connection nor invokes consumers recursively. This preserves the public dependency direction while allowing new discovery to exclude disabled sources. Captured execution views remain unchanged.

## Alternatives considered

One installation per Agent would duplicate shared source identity. Treating Catalog registration as execution permission would activate unrequested capabilities. A callback that borrows its own connection was rejected because saturated business pools could deadlock during resolution. Audit receipts do not determine installation success.

## Consequences

The caller still supplies validated Tool definitions, MCP discovery or prepared Skill content. Catalog service availability does not establish a downloadable source adapter, an executable installer Builtin or complete legacy source coverage. No installation workflow table, historical package archive or global refresh transaction is introduced.

## Verification

The joint storage, Workspace, Tool and Market suite passed 144 tests, including registration deduplication, explicit self-install, account-specific discovery, shared/private Skill updates, source disablement and same-transaction resolution under a saturated pool. Independent review found no remaining service-slice blocker. External source fetching, application assembly, product E2E and platform load remain separate evidence.
