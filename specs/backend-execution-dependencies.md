# G004 execution dependencies implementation contract

Status: user-approved product decisions and independently reviewed implementation contract; implementation not yet verified.

## Scope and authority

G004 implements Workspace, Tool, Capability Market and the execution portion of Model, together with the [Audit amendment](backend-audit-observation.md). It registers the complete S2 schema for those owners and Session, A2A, Group, Trigger, Heartbeat and Channel. The latter six remain schema-only until G006. Run/Context services and core Runtime E2E remain G005. One application, Base and owner-private persistence remain unchanged; no startup DDL, target migration baseline, old-API compatibility or Sandbox activation is added.

Existing Model and Credential configuration/storage contracts continue to follow [Backend Foundation](backend-foundation.md) wherever this artifact does not amend them. G004 extends their execution and account-selection contracts without discarding the existing encrypted formats, identity constraints or administrative services. The independent Audit amendment is the explicit exception to the old coupled-write contract.

Controlling decisions are the [target architecture](../.agents/notes/proposed/architecture/2026-08-28-target-agent-execution-architecture.md), [Workspace](../.agents/notes/proposed/architecture/2026-08-27-user-agent-group-workspaces.md), [Tool](../.agents/notes/proposed/architecture/2026-08-27-tool-registry-execution-and-exposure.md), [Market](../.agents/notes/proposed/architecture/2026-08-31-tenant-capability-market-and-agent-installation.md), [Credential](../.agents/notes/proposed/architecture/2026-08-31-credential-and-secret-boundary.md), [Model](../.agents/notes/proposed/architecture/2026-08-28-model-system-provider-boundary.md) and [login authorization](../.agents/notes/proposed/architecture/2026-09-06-login-session-authorization.md) contracts. This artifact records the user-approved Agent-only Skill and account-selection amendments described below, not an approval of every later product workflow.

## Shared operations and input boundaries

Public services expose typed values. Another owner never imports private ORM or repositories. Application composition coordinates participating owner services and chooses transactions; it does not write their tables. Capability Market depends on public Tool and Workspace services for installation. A filesystem, HTTP request or other slow external operation does not hold a business database transaction open. Audit is an independent non-blocking observation interface, not an installation, permission or execution authority.

Principal and resolved execution scope are trusted in-process values produced at authenticated intake, never arbitrary request dictionaries. Human permissions retain the login lifetime. New Runs resolve current Agent-owned configuration inside that scope; current Runs retain their fixed Tool bindings and discovery. Group membership facts remain Group-owned and are supplied through a pre-resolved scope, without a reverse Workspace-to-Group service dependency or live authorization polling.

All list, search, read, download, schema, content and queue operations enforce owner-level byte/cardinality bounds before materialization. Resource errors are typed and do not reveal Secrets. JSON at boundaries is versioned and bounded; unsupported authoritative versions fail explicitly. Declared versions are not silent fallback selectors.

## Workspace and Skill ownership

`workspaces` contains Tenant and exactly one Membership, Agent or Group owner, with same-Tenant foreign keys and one Workspace per subject. Membership and Group spaces contain `memory/` and `files/`; only Agent spaces contain `skills/`. No relationship Workspace or empty future Skill area is created. Soul and product configuration remain outside the file API.

Workspace owns scoped listing, search, read/preview and conditional mutation. A read returns content with its current revision. Ordinary file content and its concurrency token have one authoritative storage operation; no secondary database revision write determines whether an already replaced file succeeded. Non-Sandbox writes stage complete content, verify the expected revision under a short resource-scoped commit boundary and replace atomically. Namespace mutation and source-checked one-way Agent-to-Membership/Group Copy preserve the same ownership rules. Preparation failure does not alter the visible content. Conflicts return a typed current-revision result; the Agent performs any semantic merge outside locks. No Git, retained history or generic rollback is added.

Direct/Group scope permits ordinary output only in the current Membership/Group Workspace and reads the executing Agent Workspace. Agent-owned scope permits its own ordinary file writes. Subagent scope inherits the parent's direction but not the Main-only distillation capability. The explicit Main distillation operation may update only generalized Memory of its own Agent; provenance is offered to Audit without governing write success. Arbitrary writes under `skills/` are rejected even when ordinary file mutation is authorized. Humans remain preview-only in this release.

Memory uses one `memory/MEMORY.md` per subject. Its bounded, source-labelled Guide/Index is the only automatic discovery input. Remaining content requires explicit scoped search/read, and creation, modification, deletion and index updates remain explicit. Completion and compaction do not write Memory.

Workspace owns current Skill package content and `agent_skill_bindings`; Market owns discovery source, not a second file authority. `skill_packages` identifies a Tenant-shared or same-Agent private package, its current complete storage location, content hash and format/current-revision metadata. Only current package state is stored, not historical revisions. Bindings identify Agent, canonical Skill name and package with same-Tenant constraints; a private package can bind only its owning Agent. Shared package storage is an internal Workspace mechanism, not a fourth type of Workspace. Authorized Agent paths resolve through the same Workspace service.

Updating a shared package changes the current package used by every Agent still bound to that shared package. Updating an Agent's own package affects only that Agent and never changes the shared source. A private update of an Agent's shared installation first obtains a private package and rebinds only that Agent. This split does not permit a Run to author Skill contents: installation/update is a controlled capability-management operation. Package preparation uses a temporary directory, validates all members and the manifest, then activates the complete package; bindings and current package metadata commit coherently after content exists. Unpublished preparation data is cleaned by its owning operation. No incomplete package is discoverable.

A Run fixes its Agent Skill discovery identities, not historical file copies. Newly installed/removed/renamed Skills change discovery from the next Run. Explicit loads of an existing discovered Skill read its current completely published content; already supplied request/History content is never rewritten. Cache keys and invalidation must work across processes and follow the current package source rather than silently pinning old content forever.

One Skill load resolves one complete package even when replacement or removal races it. Cleanup cannot delete content selected by an in-flight load or a newer successful publication. Bounded reader-safe temporary retention is not a per-Run revision archive or product history. Test the publication/removal/load race and cleanup ownership explicitly.

Local and S3 storage remain adapter mechanisms. The adapter must provide equivalent publication and bounded operations; this contract does not assume S3 supports directory rename. Materialization inside Sandbox, sandbox execution and write-back remain deferred.

## Tool and MCP

Tool owns Tenant `tool_definitions`, `agent_tool_grants`, `agent_mcp_connections` and `membership_agent_tool_connections`. Definitions retain stable canonical identity, upstream name, description, versioned input schema, explicit versioned executor key, source Catalog reference and non-Secret settings. Code-owned Builtins cannot be redefined by database contents. Grants are explicit; absence never implies permission. MCP grants bind the same Agent and Catalog as their connection and Definition through composite constraints. Non-MCP Credential grants accept only Tenant or same-Agent credentials.

A Tenant MCP source is registered once; each Agent has its own connection and explicit grants. Authentication requirement is explicit. A server that does not require authentication needs no fabricated Credential. A server that requires it but lacks valid credentials is unavailable pending authentication. Agent default MCP credentials remain same-Agent. Actual tool availability may differ by the credential presented; discovery from one account must not be treated as another account's authorization or overwrite an incompatible definition silently. Immutable Run resolution consumes the selected account-scoped discovery and binding facts, not mutable current Catalog data during each call.

MCP defaults to the Agent account. A personal connection is eligible only when the user explicitly requested that account, the Membership granted the connection and the task's resolved scope allows it. It uses the existing Membership-Agent-Tool relation and same-Membership Credential; it never replaces the Agent default connection. Group/Agent-owned/Trigger/Heartbeat/A2A execution does not gain personal accounts implicitly. Their explicitly delegated personal references follow the existing product-owned authorization contract. Failure does not switch to another account, Agent or Tenant Credential.

Registry binds a Definition and Executor. The model-visible Definition remains name, description and input schema; Tool Call and Result retain call identity and normalized content/error. Exposure is a small direct set plus search over the immutable authorized remainder, with selected definitions retained for subsequent requests in that Run. New installation does not expand an active Run. Role-ineligible Task/Todo/A2A capabilities remain absent rather than hidden only by prompts; G004 defines eligibility but does not implement their later product executors.

Executors receive explicitly injected services and a narrow per-call correlation/cancellation scope, not a generic service locator or database session. Scheduling preserves model call/result order, defaults unknown tools to serial and allows only explicitly known-safe bounded parallel work. Ordinary errors are Tool Results. Possible irreversible side effects return an explicit uncertain outcome and are not automatically replayed. No Tool Ledger, generic progress, execution lease, recovery table, new Task/Goal object or approval workflow is introduced.

MCP transport/discovery, connection tests and tool invocation are tested using real adapters with controlled HTTP peers. Supported legacy operations are inventoried before replacement; optional new protocol features are not claimed simply because an SDK exports them. Exact transport encoding, content variants and bounded timeout/retry choices belong to tested adapter contracts, without hidden fallback.

## Capability Market

`capability_catalog_items` stores a versioned bounded discovery manifest, kind, normalized source identity, display metadata, version, enablement, origin and installation attribution. Platform templates have null Tenant and never serve as executable bindings. First use materializes one Tenant entry; separate partial unique indexes cover platform and Tenant identity. Parallel installations converge on one Tenant registration without sharing Agent credentials or implicitly activating another Agent.

Search and installation use public Tool/Workspace/Credential/Permission contracts. External fetching and validation precede the short registration/binding transaction. A successfully registered source can remain when a later Agent-specific install fails; outcomes distinguish source registration from Agent activation. A source item is not proof that an Agent has installed or can execute it. Skill package publication and account selection follow their owning contracts above. Audit is neither an installation receipt nor a completion check.

Shared refresh and Agent-private update are distinct explicit operations. Shared Skill refresh publishes the shared package; private refresh publishes only the selected Agent package. No whole-platform simultaneous transaction across every Agent is promised. Actual package/binding facts, not Audit, determine availability. Management queries are bounded, Tenant-scoped and return no Secret bytes.

## Model execution and continuation

The four legacy adapter families remain acceptance scope: OpenAI-compatible chat, OpenAI Responses, Anthropic and Gemini. Inventory each supported streaming, Tool Calling, multimodal, usage and continuation behavior from the immutable reference before implementing its replacement. Existing fallback, quota and implicit default mechanisms are not retained. Network adapters use the existing dependency graph or explicit project approval for any added package.

Model resolves one immutable private Model Policy and a separate secret-free Context Profile. Context supplies logical segments and retained interaction identities; Model performs physical request encoding, capability/limit validation, transport, normalized streaming/usage/errors and exact required continuation handling. Configuration capability source literals follow G003's `provider_metadata`, `builtin_catalog` and `administrator`; Provider/Catalog resolution and enablement validation must not be confused with G003 field validation alone.

Required opaque continuation is encrypted and committed by Model before returning the complete normalized Model Step Result. It survives Waiting without independent TTL and is replayed only where required by retained interactions and the fixed Provider contract. Corruption, missing required state, unknown version or key failure yields the already agreed structured unrecoverable error; no reconstructed replay or model switch occurs. Provider network work holds no database transaction. Optional caches are disposable and bounded.

Model never imports or writes Run-private persistence to decide lifecycle. Runner supplies committed terminal facts to Model's cleanup port in G005; cleanup failure cannot change the terminal outcome. Context compaction cannot discard information still required for an applicable continuation. G004 tests the Model ports with schema fixtures; G005 verifies the assembled Runner/Context path.

## S2 schema-only product contracts

All these records retain explicit Tenant/Agent/Run/source columns and same-owner composite foreign keys; versioned JSON contains bounded content rather than hiding authoritative identities. No public services, routes, workers or second Run lifecycle are introduced for these owners in G004.

| Owner | Tables and authoritative relations |
| --- | --- |
| Session | `sessions`, `session_entries`, `session_run_links`: Membership and Agent ownership, one input/reply sequence, explicit related Waiting reply, stable input identity, immutable cutoff and Run links. Goal configuration is part of Session; one original goal input can have multiple iteration Run links. No Goal or Task table. |
| A2A | `a2a_requests`: source Agent/Run/Tool Call request identity, target Agent/Run, notify/consult/task_delegate intent, bounded explicit input and result. Target execution result and source delivery are separate facts; terminal source cannot be revived. No sender Workspace/implicit History transfer. |
| Group | `groups`, `group_memberships`, `group_events`, `group_run_links`: Tenant, announcement, membership, ordered source events and links to selected same-Tenant Agent Runs. One event may produce multiple independently correlated Agent Run links. Group membership admission follows captured human scope. |
| Trigger | `agent_triggers`, `trigger_occurrences`: Agent configuration, explicit delegated personal references where authorized, stable occurrence identity, input/admission/Run/result correlation. Trigger is not a Session or Run engine. |
| Heartbeat | `agent_heartbeats`, `heartbeat_occurrences`: one configuration per Agent, explicit personal delegation if authorized, stable due identity and Run/result relation. No generated Trigger record. |
| Channel | `agent_channel_configurations`, `channel_deliveries`: same-Agent configuration and compatible Credential, external identity/configuration, committed Session or Group reply source, delivery attempt/acknowledgement. Delivery failure cannot undo the committed Run or reply. Incoming provider identity supports product input deduplication rather than a generic event table. |

Source admission facts do not copy the six Run statuses. Run references constrain both Tenant and Agent. A2A source and target use their respective Agent identities. Credential references use the existing owner-kind/identity key. Personal Tool connections may represent multiple accounts for a Membership/Agent/Tool; they are not collapsed into one shared default Credential.

## Acceptance and commit sequence

1. Synchronize approved decisions and complete independent review; bind amendments and six new S2 owner contracts without rewriting original approval evidence.
2. Replace Audit coupling with its independent bounded observation interface and verify failure isolation.
3. Register the entire S2 foreign-key graph through the existing schema integration point. Real PostgreSQL creates/drops every registered table and rejects cross-Tenant, wrong-Agent, incompatible Credential, wrong MCP source and private-Skill cross-Agent relations.
4. Implement Workspace/Memory/Skill publication, Tool/MCP, Market installation and Model adapters in dependency order, using small independently testable commits and real service integration below controlled external I/O boundaries.
5. Verify concurrent same-resource conflict, unrelated-resource progress, staged publication failure, shared versus private updates, account choice/isolation, fixed Run views, install partial outcomes, bounded loads, streaming failure and continuation persistence/cleanup. Update owning Notes and gates with each intent.
6. Rerun cumulative G000–G004 checks and independent code/architecture review. Keep G005/G006 E2E, hosted CI, deployment, Sandbox, frontend and 50-execution load as separate unclaimed evidence until exercised.

This contract does not declare implementation complete. Exact columns, indexes, service names and policy bounds may be refined within these approved semantics with owning tests; behavior changes require a reviewed amendment rather than altered test expectations.
