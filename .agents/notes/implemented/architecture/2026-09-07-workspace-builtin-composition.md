# Agent Note: Workspace Builtin composition

Status: implemented — 15 code-owned Workspace Builtins, persisted provisioning and application resources are implemented; per-Run assembly remains G005 work.

## Problem

Workspace service methods do not by themselves make file and Skill operations callable through the Tool scheduler. Putting these adapters inside either Tool or Workspace would couple otherwise parallel owners.

## Decision

Application-side `execution_dependencies` adapters connect code-owned Tool Definitions to public Workspace methods. The application supplies one trusted Workspace scope and fixed Skill discovery. Model arguments select only the current or Agent space alias and operation data; they cannot create Tenant, Agent or Run authority. The execution boundary checks the injected Run and binding before calling Workspace.

File reads expose bounded UTF-8 slices with continuation offsets. Listing and search preserve pagination. Writes and edits use expected revisions; failed conditional mutations do not retry with a newer token. Directory operations retain the public manifest revision and partial outcomes. Controlled Skill loading uses captured discovery, never arbitrary access to a Skill directory. Explicit Agent Memory distillation is available only to Main and remains checked by Workspace at execution.

## Alternatives considered

Importing Workspace into Tool or Tool into Workspace would place application wiring inside a business owner. Calling raw storage from the executor would duplicate path and revision policy. Synthetic executor tests alone would not verify the real Workspace boundary.

## Consequences

Registry bindings do not automatically grant or expose these Tools to an Agent. Provisioning must create explicit Definitions and grants; each Run supplies its captured authorization and Skill discovery. Directory operations expose partial outcomes, not atomic tree changes. Skill member-name pagination is independent of content-slice pagination, so all permitted members remain discoverable.

## Verification and gaps

Nine focused tests run the real scheduler, adapter, Workspace service and Local storage with PostgreSQL metadata. They cover actual file effects, conflicts, denied scopes, Main-only distillation, all 128 Skill member names and bounded results. The combined Workspace/Tool/Market/Builtin suite passed 89 tests; independent code and architecture reviews found no remaining slice blocker. [Persisted provisioning](2026-09-07-explicit-builtin-provisioning.md) and [application resources](2026-09-07-application-execution-resources.md) have separate integration evidence. Source importers remain deferred; Runner consumption, Sandbox and platform load remain later work. This Note does not by itself claim G004 completion.
