# Agent Note: Target Agent Execution Architecture

Status: proposed — the complete clean-break target architecture is agreed as the source for implementation planning but is not implemented

Authorization timing follows [Login-Session Authorization](2026-09-06-login-session-authorization.md): human access is fixed for a login session; Agent execution configuration is resolved at each new Run; live revocation tracking is excluded.

## Problem

Clawith needs a simpler execution architecture that keeps human conversation responsive, delegates planned work to isolated Subagent Runs, supports User, Agent, and Group Workspaces, progressively assembles Context, normalizes Model and Tool execution, and lets product capabilities start independent Main Runs without preserving the current Runtime topology or compatibility protocols.

The target is a clean break. It does not preserve existing Run checkpoints, Tool Ledger and Lease semantics, legacy execution variants, nested Workspace roots, relationship-specific Memory, or unused product paths merely because code exists.

## Proposal

### System map

```text
Human / Session Goal / Group / Heartbeat / Trigger / A2A
                         |
                         v
                Product capability owner
                 - records Product Input
                 - selects target Agent
                 - initiates start or resume
                         |
                         v
                    Agent Runner
                 - creates Run identity
                 - owns Status and History
                 - parent-child cancellation
                         |
                         v
                      Main Run
                         |
                         v
                     Agent Loop
              +----------+-----------+
              |                      |
              v                      v
           Context               Tool System
              |                      |
              v                      +---- Task Tool ----> Subagent Run
         Model System                +---- ordinary Tools
              |                                      |
              v                                      v
      normalized Model Step                     Tool Results
              |                                      |
              +-------------------> Run History <-----+
                                      |
                                      v
                                  Run Output
                                      |
                                      v
                           initiating product capability
```

### Modular Backend boundary

The first release is one modular Backend deployment, not a collection of microservices. Identity and Permission, Agent, Product Input, Run, Context, Model, Tool and Capability, Workspace, Credential, Channel and Trigger, A2A, and Audit are explicit code modules inside the same application and PostgreSQL boundary. Each module owns its domain records, repository, mutation service, public contracts, and tests. Minimal Auth joins the G003 foundation; later registration, recovery and SSO workflows remain separate product slices. Another module calls those public contracts rather than writing its tables, importing its private repository, or redefining its facts.

Modules may participate in one same-database transaction when the architecture requires atomic handoff. The orchestrating application service controls transaction scope, while each owner writes only its own records; atomicity does not transfer fact ownership. Read-only projections may join owner-produced database shapes for bounded product queries, but they cannot mutate source tables or become another authority.

Audit follows the agreed [asynchronous observation boundary](2026-09-06-asynchronous-audit-observation.md): business owners submit observed outcomes through an independent non-blocking interface, without passing their TransactionContext. Audit owns asynchronous processing and its own storage transactions; it does not block business commits, change their outcome or supply facts for main-flow decisions. Run History and other authoritative owner records remain outside Audit. This target decision replaces G003's Audit coupling, which remains in code pending the documented amendment.

The target introduces no internal HTTP or RPC hop, per-module deployment, shared event bus, distributed transaction, service discovery, or duplicated cross-service DTO merely to imitate microservices. In-process typed calls are the default. External Provider, Tool, MCP, Channel, Sandbox, and object-storage adapters remain narrow infrastructure boundaries. A module may be extracted into another service later only after it has an independent scaling, availability, security, or deployment requirement and a durable handoff contract.

### Agent product boundary

An Agent is a Tenant-owned durable model identity and configuration, not a process, container, current task, Main/Subagent type, or execution status. Its direct attributes are its product identity (`name`, `avatar`, `description`, optional `greeting`), mandatory `soul`, timezone, enabled flag, archive timestamp, creation audit reference, and timestamps. `greeting` is optional Session presentation data and never a Platform Instruction; `soul` is the Agent-owned instruction source fixed into every Run and cannot be modified by an Agent Run.

Workspace, Model Policy, Skill bindings, Tool grants with capability-owned Credential references, Channel configurations, Heartbeat configuration, Trigger configurations, Sessions, and Runs remain separately owned records or capabilities related to the Agent. Agent never receives a generic grant to a raw Credential. Templates may initialize an Agent but do not continue to control it. Usage counters, quotas, unread state, Channel availability, Sandbox resources, Worker state, and current execution activity likewise remain with their actual owners rather than becoming Agent fields.

Agent has no `creating`, `running`, `idle`, `stopped`, or `error` lifecycle. An enabled Agent may accept new Runs. Disabling or archiving an Agent changes its availability for new execution under the owning product rule; it does not trigger cancellation of existing Runs. Archiving retains historical references. Administrator execution exceptions are settled during Permission implementation. Run creation fixes the executing Agent Identity and Soul together with resolved Model Policy, authorization, and Workspace access. Later configuration changes affect new Runs rather than dynamically rewriting an existing Run.

Product User means one Tenant Membership, while Account is the global natural person and authentication subject. Tenant Principal represents ordinary Membership product access; Platform Principal represents an explicit platform operation against one target Tenant and cannot enter ordinary Agent execution or Workspace Context. User Workspace therefore means Membership Workspace and never crosses Tenant through a shared Account. The complete identity boundary is owned by [Account, Membership, Tenant, and Principal](2026-08-31-account-membership-tenant-principal.md).

Audit attribution is a closed union of Membership, Platform Account, Agent, and System actors. Every audit fact names its target Tenant. Platform administration uses the authenticated global Account without fabricating a Membership; Agent mutations use the same-Tenant Agent and optional Run relation; ordinary human Tenant operations use Membership.

### Main Agent and Subagent

Main Agent owns human or product interaction, intent understanding, direct execution, delegation, result synthesis, requests for human input, and completion judgment. It may perform work directly or delegate through Task Tool. How it interprets a requested work method or judges unspecified work complexity belongs to Main Prompt and Tool Description rather than Agent Loop or lifecycle architecture.

Task Tool is optional and directly exposed only to Main Runs. One call accepts one or more delegated Task work descriptions and starts one Subagent Run for each accepted description. Task is a Run-scoped model working view derived from Task Tool Calls, Child Run facts, and Child Results, not a table, ID, persistent record, status, result object, planner, Workspace, execution engine, lifecycle controller, or state machine.

Every Task Tool Child Run uses the same Agent as its Parent Main Run. Task Tool accepts work descriptions but no target Agent. Cross-Agent work always uses A2A and creates the target Agent's independent Main Run; that Agent may then use its own Task Tool to create same-Agent Child Runs.

Subagent is a leaf executor. It inherits the parent Main Run's complete resolved Tenant, RBAC, Workspace, and ordinary Tool authorization without inheriting parent or sibling Run History. It receives Task description as Run Input, uses Todo Tool for current-Run planning, performs and verifies the work, and returns Run Result. Subagent cannot invoke Task Tool recursively.

A Subagent missing required human or product input atomically enters Waiting with a correlated Child Need Input committed to its non-terminal Main. Main Agent may answer from its Context or request human input and enter Waiting. Task Tool then resumes the exact Child Run, preserving its original input and accumulated Run History. A terminal Main cancels the Child in the same transaction.

Main and Subagent Runs use the same Agent Runner, Agent Loop, Model System, Tool System, Status, and Run History contracts. Their role, input, Context, and Tool exposure differ.

The current non-OpenClaw execution type is the Native Agent, not a "local Agent" tied to one machine. The target removes OpenClaw and therefore has only one Agent execution form: every Agent executes through the shared Agent Runner and Agent Loop. The target data model does not retain an `agent_type="native"` discriminator. The clean-break refactor also removes OpenClaw API keys, Gateway polling and message paths, remote online status, and related compatibility behavior rather than adding a second execution protocol beside the shared loop.

### Agent Runner and Run lifecycle

Product capabilities initiate Main Runs using stable source identity, so retry returns the existing Run reference. Related input submission is likewise idempotent per target Run and source identity. One Main Task Tool Call submits one or more work descriptions, starts one idempotent Child Run per accepted assignment using existing Parent Run and Tool Call correlation, and returns acceptance immediately. Later calls append new delegated work rather than updating a persistent Task. Agent Runner is the only Run creator and the only Run Status and Run History writer.

Run Status is limited to Running, Waiting, Completed, Failed, Cancelled, and Interrupted. Agent Runner atomically appends explicitly related input to any non-terminal Run: Waiting becomes Running, while Running retains status and consumes the input in a later Model Step. Concurrent inputs retain commit order in Run History. Completed cannot commit over already-recorded input absent from its producing Model Step, and terminal Runs cannot be revived. Ordinary per-Run Parent terminal settlement, including Completed, cancels active Child Runs; premature Main completion is accepted without adding a completion gate. Service-wide cleanup instead marks every non-terminal Main/Subagent Run Interrupted without waking a Parent.

Agent Runner does not provide cross-Worker takeover, arbitrary execution-point recovery, generic side-effect reconciliation, or automatic replay. Lost execution ends as Interrupted; later work starts a new Run from committed facts.

### User messages and execution completion

The [unified user-message outlet](2026-09-09-user-messages-and-run-completion.md) handles acknowledgements, progress and final-result delivery through one product-owned message contract. Final separately settles Run and initiating-owner execution results without generating another chat reply. Message sending does not imply Waiting or terminal status, create a Run, or wake one. Tool/native-output encoding remains undecided; parent-child and interruption rules are unchanged.

### Session and product inputs

Direct Session is a human-facing conversation. Only authenticated human input creates Session Input. An explicit reply resumes its exact Waiting Main Run; every other input starts a new Main Run. Each new Main Run receives a fixed Session-history cutoff, and concurrent replies commit when ready while retaining their originating Input relation. A new Main may interpret a follow-up or stop request and use [Session-owned work control](2026-08-27-direct-session-input-history-and-concurrency.md#conversational-work-control) to explicitly supplement or cancel another Main in the same Session; ordinary chat does not require a user-created Task, transfer Child ownership or implicitly merge Run histories.

Group, Heartbeat, Trigger, and A2A remain independent product capabilities. Each records its own input, initiates or resumes Main Run through Agent Runner, consumes Run Output, and owns product projection and delivery. There is no global Product Event bus and no routing of non-human events through direct Session.

Heartbeat is independent from Trigger. A2A creates the receiver's independent Main Run and transfers only explicit input, never sender authorization or implicit Context. A2A Tool Calls settle with immediate acceptance; `consult` and `task_delegate` later submit correlated A2A Result Input to the exact non-terminal source Main Run, while `notify` remains one-way.

Goal mode is lightweight direct Session configuration and continuation policy. One Session stores at most one active objective, committed progress, wait condition, and relation to the existing `/goal` Session Input without a Goal table or ID. Each iteration starts a new ordinary Main Run related to that input and cutoff with bounded committed facts rather than inheriting an earlier Run History or restoring a terminal Run. `continue` completes the current iteration and starts the next immediately; Goal `wait` completes it and delays the next Run until a future condition is satisfied. Goal has no `require_user` disposition; missing human information uses ordinary Need Input, Run Status Waiting, and Resume of the same Run. Goal result messages use the unified message outlet without new Goal-specific output types; terminal dispositions do not automatically send replies.

### Context

Context is a per-model-call sourced view with eight owner categories: Platform Instructions, Agent Identity, Product Input, Run Context, Workspace Discovery, Tool Exposure, Retrieved Content, and Model Context Profile.

Platform Instructions and executing Agent Soul are mandatory fixed instruction sources. Product owners supply bounded Product Input. Agent Runner supplies isolated Run History. Authorized Workspaces supply labeled Memory entry sections; only the executing Agent supplies a Skill Index. Tool System supplies only directly exposed Tool Definitions. Retrieved content enters only through Tool Results. Model System supplies an immutable secret-free Model Context Profile; credentials and secret-bearing Provider configuration never enter Context.

Context uses a Run-scoped immutable source snapshot, current Compaction Base, and incremental event Delta. Logical segments remain Provider-neutral; Model System chooses physical order and cache controls. Compaction first removes stale high-volume Tool Results, then uses a structured derived summary, recent complete interaction tail, and coverage cursor without deleting source facts.

### Workspaces

Every User, Agent, and Group has exactly one Workspace. Only Agents have Skills in the first release:

```text
User/Group Workspace       Agent Workspace
  memory/MEMORY.md           memory/MEMORY.md
  files/                     skills/
                             files/
```

`MEMORY.md` begins with a compact Guide and Index entry section injected into Context; remaining content is searched and read by line range. The executing Agent's Skill Index is injected and full Skill packages load on demand. `files/` has no automatic directory summary and is inspected through Workspace Tools when current work requires it.

Workspace owns current Skill packages and Agent bindings; Market owns discovery metadata. Updating a Tenant-shared package affects every Agent still bound to it. Updating a same-Agent private package affects only its owner; a private update of a shared installation first creates a private package and rebinds only that Agent. Shared storage is not a fourth Workspace type, and User/Group Skill bindings or empty Skill areas are absent.

Humans may inspect and preview authorized Workspace content but cannot mutate it directly. Authorized Agent Runs perform every Workspace create, edit, delete, move, rename, import, and cross-Workspace publication through Workspace Tools. Mutations are current-revision checked and atomic. A write lock is resource-scoped and held only for storage commit, never across model, Run, or surrounding Tool latency. Agent-Agent conflicts use semantic merge and bounded retry. Current revision is a concurrency token rather than Git history or a recovery guarantee; version retention and accidental-deletion recovery are deferred.

Non-Sandbox file writes prepare complete temporary content before replacement; controlled Skill installation and update prepare and validate a complete temporary directory before activation. The [Workspace contract](2026-08-27-user-agent-group-workspaces.md#agent-only-mutation-and-concurrency) owns publication and failure semantics. Audit is independent of publication. Sandbox file mapping, editing and write-back remain for the Sandbox review and do not expand this design.

Direct and Group Runs write ordinary files to their Membership or Group Workspace and treat Agent files as read-only. Agent-owned Main Runs may write Agent files. Agent file publication is one-way Copy from Agent Workspace into Membership or Group Workspace; reverse file publication is absent. Direct and Group Main Runs may explicitly distill generalized Agent Memory through one dedicated Tool, while Subagent Runs only return proposals to Main. Agent Runs cannot mutate Skill in the first release; Market/Admin installation and later Frontend editing use controlled Workspace mutation and cache invalidation. Skill Index discovery is fixed for the Run, while the next explicit load may read updated current content; no Skill revision history is introduced. First-release humans remain preview-only; later Frontend editing must reuse the same Permission, Revision/CAS and atomic mutation boundary, with independent asynchronous Audit observation.

Soul, Heartbeat policy, Group Announcement, Session and its Goal-mode configuration, Run, Task Tool Calls, Child Run facts, Focus, Trigger, messages, credentials, permissions, Model configuration, Runtime state, revisions, locks, and audit metadata remain outside Workspace.

### Tool System

Every Builtin, MCP, product, and external Tool enters one Registry through one Definition and Executor registration. Authorization, Run-role eligibility, direct exposure, searchable exposure, scheduling, execution, and presentation remain separate concerns.

MCP requires credentials only when its service requires authentication; platform Agent grants always apply. The Agent account is the default. A personal account requires explicit user selection, an authorized Membership connection and eligible resolved task scope, without changing the Agent default or falling back to another account. Account-scoped discovery cannot authorize a different account or silently overwrite its incompatible Tool definitions.

The model receives a small directly exposed Tool set plus authorized search, not the complete Registry. Main Runs directly receive Task Tool and not Todo Tool; Subagent Runs directly receive Todo Tool and cannot discover or invoke Task Tool. A2A preserves `notify`, `consult`, and `task_delegate` product intent over two technical execution semantics: one-way send and asynchronous request-result. Exact model-facing Tool shape remains implementation design.

Tool Calls and Tool Results enter Run History. Ordinary Tool failure returns a model-visible Tool Result rather than failing the Run. The target has no generic Tool Ledger, Lease, takeover, replay, reconciliation, or Progress state machine.

### Model System

Every Run fixes one Model Policy. Model System supplies Context Profile and Provider capabilities; Context owns budgeting and compression. Provider Adapter maps logical Context segments to physical requests, caching, streaming, and continuation mechanisms.

Agent Loop consumes one normalized Model Step Result. Streaming, Usage, Error, and opaque Provider Execution Metadata use separate narrow contracts. Optional Provider conversation state and caches are disposable optimizations and may use TTL. Required continuation metadata is persisted by Model System before Model Step settlement, has no independent expiry, replays exactly through Waiting and resume, and is removed when no longer needed or after the Run becomes terminal; it never becomes Context, product truth, or Run History authority.

### Minimal Tenant and RBAC

The initial architecture retains only Tenant isolation and minimal product relationships:

```text
cross-Tenant access --> denied
User Workspace ------> human owner previews; authorized User Runs mutate
Agent Workspace -----> Memberships that can see the Agent preview; authorized Agent Runs mutate
Group Workspace -----> active members preview; authorized Group Runs mutate
Soul / Agent config -> Tenant administrator
```

Subagent inherits Parent Main Run authorization exactly. A2A resolves receiver authorization independently. The initial implementation has no company/private/custom Agent modes, per-file ACL, directory ACL, ABAC, policy engine, relationship Workspace, or capability-token hierarchy.

Agent visibility is owned by [Minimal RBAC and Agent Visibility](2026-08-31-minimal-rbac-and-agent-visibility.md), not Workspace. Tenant equality is required but does not grant visibility by itself. Agent discovery, Session creation, A2A target discovery, Agent Workspace preview, Run start, and Capability installation consume the same result, and a known Agent or Workspace identity cannot bypass it.

Auth and Permission resolve human identity, roles and admitted Agent access at login; Backend entrypoints consume that valid session scope without live permission refresh. Product intake resolves Agent-owned Model, Tool/MCP and Workspace configuration for each new Run, then fixes it in Snapshot. Runner does not authenticate users or track permission changes. There are no authorization generations, Run authorization-dependency projections or revocation cancellation sweeps. Explicit cancellation, resource failures, Tenant isolation and Secret handling retain their owning boundaries.

Approval is deferred to the future Permission architecture. The first release has no Agent autonomy levels, Tool Grant approval field, Approval Request table, approver policy, or approval-driven Waiting/Resume protocol. Tool System consumes only the basic allow or deny result in this target. Adding approval later requires an owning Permission decision and coordinated updates to Permission, Tool, Run Input, and product presentation contracts rather than a Tool- or Runner-local special case.

### Fresh installation and persistence baseline

The target Backend starts from an empty installation and provides no upgrade or data-migration path from the current product. After the target Models are fixed, Alembic contains one new initial schema migration rather than the existing migration chain. The new schema uses required foreign keys, nullability, uniqueness, and indexes directly; it contains no legacy tables or columns, backfills, rename compatibility, dual writes, startup schema repair, or upgrade tests from the pre-target product.

Every Tenant-owned table has a global primary identity plus a unique `(tenant_id, id)` key. A relationship whose correctness depends on Tenant equality carries `tenant_id` and uses a composite foreign key; application query filters are not the isolation constraint. Optional-owner relations use explicit `CHECK` constraints and partial unique indexes rather than nullable uniqueness assumptions. Authoritative records use required keys and `RESTRICT` deletion; the first release exposes disablement or archival rather than product hard deletion. Only explicitly replaceable projections and caches may be deleted and rebuilt under their owning contract.

Indexes follow real admission, lookup, cancellation, and history paths: Run start and related-input source identities, per-Run History order, active Runs by Tenant and Agent, Agent visibility subjects, capability source identity, and enabled Agent installations. The schema does not add speculative indexes for arbitrary JSON fields. Concurrency control is aggregate-scoped: History mutation briefly locks one Run row, while registration and installation races settle through their unique indexes. Unrelated Runs and Agents proceed independently; no global application lock, Tenant-wide lock, or generic lock table is introduced.

PostgreSQL, Redis, object storage, and Workspace storage use target-owned schemas, key namespaces, and prefixes. Target code never falls back to old Redis keys, storage objects, Workspace paths, checkpoints, or persisted Runtime protocols. Fixed code definitions remain in code, while required initial product records use a separate idempotent bootstrap rather than data migration hidden inside the schema migration.

A fresh installation does not authorize automatic deletion of an existing environment. Development and verification use a new database and new persistence namespaces; removal of old databases, Redis keys, or stored files remains a separate explicit operation.

This clean break applies only to entry from the current product. Once the new initial baseline is released, every later target version must provide a forward data upgrade from its supported predecessor versions. Future Alembic migrations are retained rather than squashed away. Authoritative Account, Membership, Agent, Session, Run, Run History, immutable Run Snapshot, Workspace, product, permission, and audit facts survive upgrades. Versioned structured payloads and stored package or layout formats use explicit schema versions and typed decoders or deliberate data migrations; application code never treats arbitrary JSON as a stable contract.

An upgrade that stops the Runner interrupts all non-terminal Main/Subagent Runs, including Waiting, under the first-release lifecycle policy. Their committed History, Snapshots and Context source data remain readable after upgrade, but data preservation does not promise automatic resumption or reversal of a terminal outcome. Replaceable Context projections, caches, search indexes, and other derived state may be invalidated and rebuilt instead of migrated. Forward data preservation is required; downgrade and zero-downtime deployment are separate product decisions unless later required.

### Capacity and responsiveness

The platform capacity floor is 50 simultaneously active Main and Subagent executions without control-plane or Frontend lag. API, Session intake, Context, Workspace, Streaming, browser interaction, rendering, bounded execution, backpressure, and performance evidence follow [Capacity, Performance, and Responsiveness](2026-08-28-capacity-performance-and-responsiveness.md).

The first release uses one non-overlapping Agent Runner instance with bounded in-memory admission and asynchronous execution. It adds no distributed Worker ownership or recovery protocol. Runner shutdown and restart cleanup mark all remaining Running or Waiting Main/Subagent Runs Interrupted; startup finishes this sweep before readiness and does not resume old execution; multiple Runner instances or rolling execution overlap require a later explicit architecture decision.

### Owner documents

| Owner | Contract |
|---|---|
| Direct Session | [Direct Session Input, History, and Concurrency](2026-08-27-direct-session-input-history-and-concurrency.md) |
| Main Agent, Task, Subagent, Goal mode | [Session, Main Agent, Task, and Agent Loop Model](2026-08-27-session-main-agent-parallel-task-model.md) |
| Run lifecycle and history | [Agent Runner Lifecycle and Run History](2026-08-27-agent-runner-lifecycle-and-history.md) |
| Tool, Task Tool, Todo, A2A Tool | [Tool Registry, Execution, and Exposure](2026-08-27-tool-registry-execution-and-exposure.md) |
| User, Agent, Group Workspace | [User, Agent, and Group Workspaces](2026-08-27-user-agent-group-workspaces.md) |
| Context | [Context Source and Assembly Model](2026-08-28-context-source-and-assembly-model.md) |
| Model and Provider | [Model System and Provider Boundary](2026-08-28-model-system-provider-boundary.md) |
| Credential and Secret | [Credential and Secret Boundary](2026-08-31-credential-and-secret-boundary.md) |
| Account, Membership, Tenant, and Principal | [Account, Membership, Tenant, and Principal](2026-08-31-account-membership-tenant-principal.md) |
| Capability Market and Agent installation | [Tenant Capability Market and Agent Installation](2026-08-31-tenant-capability-market-and-agent-installation.md) |
| Minimal RBAC and Agent visibility | [Minimal RBAC and Agent Visibility](2026-08-31-minimal-rbac-and-agent-visibility.md) |
| Product input and output | [Product Input, Main Run, and Output Boundaries](2026-08-28-product-input-main-run-and-output-boundaries.md) |
| Capacity and Frontend responsiveness | [Capacity, Performance, and Responsiveness](2026-08-28-capacity-performance-and-responsiveness.md) |

## Alternatives considered

### Preserve the current Runtime and migrate incrementally

The accepted target removes ownership and protocol layers rather than maintaining compatibility between old and new authorities. Current checkpoint and execution compatibility is not a requirement.

### Upgrade the existing database into the target schema

An upgrade path would require retaining intermediate fields, backfills, compatibility reads, and migration-only behavior across every redesigned domain. The target is a new installation, so it creates the final schema directly and does not migrate existing product data.

This rejection does not permit later target releases to discard data created by the new baseline. Those releases own explicit forward migrations and stored-format evolution from the first target version onward.

### Add one global lifecycle or event bus

Session, Group, Trigger, Tool, Run, Workspace, and Model facts have independent owners. One shared bus would recreate optional-field protocols and duplicate authority.

### Make Main Agent execute every request

Long multi-step work can block or flood the human-facing context. Task Tool provides isolated leaf Subagents and bounded Results, but architecture does not force Main Agent to use it for a particular Prompt.

### Give Subagents recursive delegation

Recursive Task trees add coordination, cancellation, and Context complexity. Subagents remain leaf executors; Main Agent owns decomposition and may submit additional delegated work through later Task Tool Calls.

### Add Task or Goal state machines

Task and Todo are Run-scoped model working views. Goal mode is configuration embedded in Session plus a continuation policy. None requires an independent domain object, status state machine, lifecycle controller, or execution engine.

### Put all durable state in Workspace

Workspace stores subject-owned Memory, Skills, and Files. Product, security, execution, and operational facts remain with their owning modules.

### Store execution and capability state on Agent

Container state, current activity, usage, Heartbeat, Trigger, Channel, Tool, Credential, Session, and Run facts change under different owners and lifecycles. Keeping them as Agent columns would turn Agent into a duplicate authority. Agent retains only its durable identity and direct configuration while those modules reference it explicitly.

### Keep OpenClaw as another Agent execution type

OpenClaw requires remote authentication, polling, delivery, availability, and execution semantics that do not use the shared Agent Loop. Keeping it as an Agent type would preserve a second execution protocol in the new core. The initial target removes this support; a future external-Agent capability requires a separate product decision and must not reintroduce hidden branches into Agent Runner.

## Acceptance criteria

- The complete target uses one Agent Runner, one Agent Loop, one Tool Registry, one Context contract, and one Model System boundary.
- The Backend is one modular deployment: each capability owns its records and mutation surface, cross-module work uses public typed contracts, and required same-database handoffs remain atomic without an internal RPC layer, event bus, or distributed transaction.
- Every Agent executes through the shared Agent Runner and Agent Loop; the target contains no Agent-type discriminator, OpenClaw API key, Gateway polling, remote message, online-status, or compatibility path.
- Agent contains only its Tenant-owned product identity, Soul, and minimal long-lived controls; independently owned Workspace, Model, Skill, Tool, Credential, Channel, Heartbeat, Trigger, Session, Run, usage, quota, Sandbox, and Worker facts are not Agent fields.
- Agent has no execution-status lifecycle; enabled/archive configuration controls availability without revocation-driven cancellation or deletion of historical references.
- Product capabilities initiate only Main Runs; one Main Task Tool Call submits one or more work descriptions, starts one Child Run per accepted description, and returns acceptance immediately.
- Child Results and Need Input signals become ordered correlated Child Inputs; Waiting Main resumes and Running Main consumes them in later Model Steps.
- A2A Tool Calls settle immediately; `notify` is one-way, while `consult` and `task_delegate` submit correlated A2A Result Input to the exact non-terminal source Main Run.
- A2A target Runs remain independent from source lifecycle, and late results cannot revive terminal source Runs.
- Main Agent may perform work directly or delegate to leaf Subagents; Prompt interpretation and complexity judgment are model behavior rather than a lifecycle rule.
- Task and Todo add no independent table, ID, persistent record, result object, state machine, Workspace, Agent role, Run type, or execution engine; Goal mode adds only lightweight Session configuration and no separate table, ID, domain object, status state machine, Reply type, projection type, or history.
- Run Status and History have one owner and no automatic replay, takeover, or arbitrary recovery.
- Run start and related-input submission are idempotent by owner-issued source identity; Task and A2A reuse existing correlation rather than adding domain IDs.
- Direct Session accepts only human input and supports concurrent Main Runs with fixed history cutoffs.
- Product capabilities retain independent input, result, projection, and delivery ownership without a shared event bus.
- Context retains source ownership, builds incrementally, preserves stable cacheable segments, and never deletes source facts during Compaction.
- User, Agent, and Group each own one Workspace with `memory/MEMORY.md` and `files/`; only Agent Workspace has `skills/` and Skill bindings.
- Shared Skill updates affect all still-bound Agents, private updates affect only their owning Agent, and explicit loads retain current-package freshness without historical revisions.
- Humans receive Workspace preview but no direct mutation surface; authorized Agent Runs mutate through revision-checked atomic Workspace Tools and automatically resolve Agent-Agent conflicts.
- Tool System separates registration, authorization, role eligibility, exposure, scheduling, execution, and presentation.
- Main has Task Tool, Subagent has Todo Tool, and Subagent cannot recursively delegate.
- Task Tool creates only same-Agent Child Runs; A2A is the only path that asks another Agent to execute work, and its target receives an independent Main Run.
- Subagent missing input remains Waiting and notifies Main through a correlated event; Main or human supplies input and Task Tool resumes the exact Child.
- Every Run fixes one Model Policy and Agent Loop consumes only normalized Model output.
- Every Run fixes exactly one Model and Provider; no fallback field, ordered list, cross-Model retry, cross-Provider failover, or automatic Model switch exists in the target.
- Agent stores one explicit same-Tenant Model reference; hard Model capabilities resolve from Provider metadata, Builtin Catalog, or explicit manual input before enablement, and every enabled Agent Model supports Tool Calling.
- Agent and Model Policy contain no maximum Model Step, model-turn, or renamed Tool-round counter; time, admission, cancellation, and Provider hard limits remain with their actual owners.
- Run has no total wall-clock or idle timeout; individually hung Provider, Tool, Sandbox, and external I/O operations use owner-specific technical timeouts defined during implementation.
- Agent, Run, and Model Policy contain no configurable Token usage limit, daily or monthly Token quota, or per-Run Token allowance; usage is observed while Context respects the selected Model's hard request capacities.
- Model System distinguishes disposable Provider optimizations from required continuation metadata and persists the required form before Model Step settlement without creating generic recovery.
- Tenant isolation and minimal owner/member RBAC are enforced at real execution boundaries.
- Approval is outside the first-release architecture; no L1/L2/L3 autonomy policy, Tool approval mode, Approval Request, or approval-specific Run behavior is implemented before the Permission module owns the complete contract.
- Human authorization follows the login session; each new Run resolves current Agent execution configuration, and neither permission changes nor configuration updates rewrite an active Run.
- A new empty environment reaches the complete target schema through one initial migration and an idempotent product bootstrap, with no pre-target data migration, compatibility read, dual write, startup repair, or automatic deletion of an existing environment.
- Composite same-Tenant foreign keys, explicit optional-owner checks, partial unique indexes, and aggregate-scoped idempotency constraints prevent cross-Tenant references and duplicate authoritative facts under concurrency.
- Database verification includes negative constraint cases and concurrent duplicate start, related-input, visibility-grant, Market-registration, and Agent-installation attempts; each identity settles once without blocking unrelated aggregates.
- After the new baseline is released, later versions retain forward migrations and preserve authoritative data; Waiting Runs and terminal history remain readable across upgrades, while replaceable projections and caches may be invalidated and rebuilt.
- At least 50 Agent executions remain active without control-plane, Streaming, or Frontend responsiveness falling below the linked performance contract.
- The first release uses one bounded single Runner with no Worker ownership, heartbeat, claim, fencing, or durable execution queue; startup interruption of inherited Running and Waiting Main/Subagent Runs completes before the deployment becomes ready.
- Existing checkpoints, legacy Runtime variants, compatibility protocols, and unused paths are not preserved by default.
- Each detailed contract has one linked owner document rather than copied implementations across notes.

## Risks and open questions

Implementation planning must still map current source consumers, decide exact data shapes, choose deletion and cutover order, define focused tests, research stable-source fingerprinting and Provider cache controls, and select metrics before optimization. These are implementation decisions under this target, not reasons to retain the old architecture.
