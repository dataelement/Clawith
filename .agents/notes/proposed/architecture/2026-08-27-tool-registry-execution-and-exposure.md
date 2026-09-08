# Agent Note: Tool Registry, Execution, and Exposure

Status: proposed — the target Tool contract and ownership boundaries are agreed but not implemented

## Problem

The target Tool system needs one model-visible contract, one registration protocol, and one Registry authority for Builtin, MCP, product, and external Tools. It must stop deriving Tool identity or behavior from names, prefixes, handler types, product metadata, or legacy execution variants.

The model currently does not benefit from receiving every registered Tool schema. Tool discovery must support a small mature default set plus authorized search without moving exposure, authorization, scheduling, execution, or UI concerns into the base Tool definition.

The backend and frontend Tool implementations must also be separable by capability owner. Large aggregation files that mix unrelated Tool definitions, execution, product policy, and presentation are not retained as the target structure.

## Proposal

### Base contracts

The model-visible Tool definition contains exactly three facts:

```text
Tool Definition
  - name
  - description
  - input_schema
```

`name` is the unique and stable machine identity used by the model and Registry. `description` explains the capability to the model. `input_schema` validates model-produced JSON arguments. Canonical naming follows the scoped rules below; registration never infers identity from an Executor type or compatibility prefix.

Execution is bound separately:

```text
Tool Registration
  - definition
  - executor
```

The model produces and consumes these envelopes:

```text
Tool Call
  - id
  - name
  - input

Tool Result
  - call_id
  - content
  - is_error
```

`content` is model-visible `ContentBlock[]`, not an output-schema-governed business object. The supported block variants and error wording are implementation contracts to define later. `call_id` correlates the result with the originating call. Ordinary invalid input, unknown Tool, capability error, API error, and business failure return `is_error: true` with model-visible content so the Agent can repair, retry, choose another Tool, or explain the failure.

```text
Tool Definition ----+
                    |
Tool Executor ------+----> Tool Registry
                              |
                              v
                         Tool Registration

Model ----> Tool Call ----> Tool System ----> Tool Result ----> Context ----> Model
```

### One Registry and one registration protocol

Every Tool source adapts to the same Tool Registration before entering the Registry:

```text
Builtin Tools ----+
MCP Tools --------+
Product Tools ----+----> Tool Registration ----> Tool Registry
External Tools ---+
```

The Registry is the only current capability directory. A canonical name resolves to one Definition and one Executor. Duplicate registration fails. Source teardown unregisters its entries through the Registry rather than maintaining a second directory. The Agent Loop and Tool execution path do not branch on whether a Tool came from Builtin code, MCP, a product capability, or another external provider.

The clean-break target has no aliases, old-name dispatch, legacy protocol adapters after registration, or runtime name inference. A source adapter may construct an explicit canonical name before registration, but downstream code consumes that name without parsing it for behavior.

### Minimal configuration persistence

Tool System persists Tenant-scoped `tool_definitions` and `agent_tool_grants`; shared Tool, MCP, and Skill discovery and installation are owned by [Tenant Capability Market and Agent Installation](2026-08-31-tenant-capability-market-and-agent-installation.md). `tool_definitions` stores stable identity, Tenant, source, related Tenant Catalog or MCP identity, upstream name when applicable, model description and input schema, schema version, executor key, non-Secret configuration, enabled state, and timestamps. Code-owned Builtins remain global Registry capabilities, but bootstrap materializes their fixed Definition identity once per Tenant so every persisted Grant and Run reference uses an ordinary same-Tenant foreign key. `agent_tool_grants` stores Tenant, Agent, Tool Definition, optional same-Agent MCP connection, optional non-MCP Credential reference, non-Secret per-Agent configuration, granting Membership, revocation timestamp, and timestamps, with one row per Agent and Tool Definition. A non-MCP Grant may reference only a Tenant Credential or a Credential owned by that same Agent; personal Credential uses the separate Membership-Agent-Tool connection. Default Tools become explicit Grants when an Agent is created rather than remaining implicit through missing assignment rows.

Builtin and code-owned Product Definitions and Executors remain code-owned. Bootstrap materializes their fixed identities for foreign keys and management projection; an editable database row cannot redefine their contract or Executor, and startup fails if its materialized Definition contradicts code. An MCP Catalog Item owns its Tenant-scoped server route and discovery revision while related Tool Definitions store stable upstream identity and Executor binding. Secret material never enters Definition or non-Secret configuration and follows [Credential and Secret Boundary](2026-08-31-credential-and-secret-boundary.md).

Canonical model-facing names are immutable lowercase Provider-compatible identifiers matching `^[a-z][a-z0-9_]{0,63}$`. Builtin and reserved names are unique globally; Tenant Tool names are unique within their Tenant and cannot shadow a global or reserved name. Two Tenant MCP servers with the same upstream Tool name receive distinct stable names within that Tenant, while different Tenants may use the same model-facing name. Registry uses scoped identity and explicit Executor binding; prefixes and source names never authorize, schedule, or dispatch behavior.

Tool Call and Tool Result require no execution table. The committed normalized model output records each Tool Call before dispatch, and each bounded Tool Result appends directly to Run History. The initial physical design has no Tool execution, Ledger, Lease, Progress, recovery, or reconciliation table.

### Skills remain separate

Installed Skills are authoritative Workspace packages bound only to Agents in the first release. User and Group Workspaces contain no Skill installations or discovery indexes. A logical Skill catalog indexes the executing Agent's authorized bindings and provides instructions, workflows, examples, and static resources to Context. Workspace owns current shared/private package content and bindings; the catalog is not a second persistence authority, and Skills do not enter the Tool Registry.

```text
Agent Workspace skills/ ----> Skill catalog ----> Context

Tool Sources -----> Tool Registry -----> Tool Definitions and execution
```

A reusable script that authenticates to or calls an external system API is an executable capability and should become a Tool. The Skill explains when and why to use that Tool. A Skill may ship instructions and a separate Tool registration, but those artifacts retain separate identities and owners. Skill instructions and resources load on demand through the authorized Workspace file capability; individual Skills do not masquerade as Tools.

Capability Management invokes Workspace's controlled complete-package publication. Shared updates affect every Agent still bound to the shared package; private updates affect only the owning Agent. Skill installation or content never grants the Tools it mentions. Existing discovered Skills retain load-time freshness, without immutable per-Run package revisions.

### Authorization and the available Tool set

Authorization runs before exposure and Context assembly. It resolves the Registry into one immutable Available Tool Set containing the Definitions and Executor bindings that the current execution may use. Run Snapshot stores the complete secret-free set: canonical name, model description, input schema and schema version, Definition identity, versioned executor key, Grant or connection identity, and the complete versioned resolved executor configuration assembled from Definition, Catalog route, Agent Grant, MCP connection, and other capability-owned non-Secret settings. It also stores every authorized Membership, Agent, and Tenant connection descriptor that the model may select, including stable reference, owner kind, label, capabilities, and configuration schema version but no Secret bytes. Default versus searchable exposure is a view over this fixed set and does not require every Definition to enter the prompt.

```text
Tool Registry
      |
      v
Authorization
      |
      v
Available Tool Set
  - Definitions
  - Executor bindings
      |
      +------------> Exposure ----> Context ----> Model
      |
      +------------> Tool Call dispatch
```

Dispatch performs an ordinary name lookup in the same frozen Available Tool Set supplied to exposure. An absent name returns Unknown Tool. During ordinary in-service Waiting resume, Tool System reconstructs dispatch target and non-Secret parameters from the persisted Run Snapshot rather than current mutable Definition, Catalog, Grant, or Connection configuration. After service restart the first-release Runner has interrupted old non-terminal Runs; Snapshot decoding supports inspection, not resuming their dispatch. Execution uses its resolved Tenant and binding scope; current Credential bytes may be read for actual use or rotation, and missing resources return owned errors. Current permission or configuration rows cannot redirect or reconfigure the active Run. Tool System does not rerun complete discovery or exposure policy when the model calls a Tool, and the concrete execution boundary remains constrained by the resolved Tenant and capability scope without live role/grant revalidation.

Executor keys are versioned code contracts. Deployment validation must retain every executor key referenced by a non-terminal Run and every decoder required by its Snapshot schema; an upgrade that removes one is blocked rather than silently binding the Run to new behavior. Definition refresh changes current catalog and new Runs but never rewrites a stored Available Tool Set. External service behavior may still fail at call time, but the Tool name, schema, authorization identity, and local dispatch meaning observed by the Run do not drift.

Human permission is resolved at login. Each new Run resolves current Agent-owned Tool/MCP configuration within that scope and freezes its Available Tool Set. A newly installed capability can become usable in a subsequent Run without another login. Permission changes do not expand existing Runs or trigger cancellation sweeps; actual Secret or resource failure remains an owned execution error.

Tools with no permission never enter the direct or searchable candidate set. Approval policy, approval persistence, approver selection, and approval-driven Run behavior are deferred to the future Permission architecture and do not add fields or states to the first-release Tool contract.

MCP authentication is required only when the server requires it; an unauthenticated service still requires explicit platform Agent grants. MCP selects the Agent account by default. Personal use requires an explicit user request, an authorized Membership-Agent-Tool connection and eligible resolved task scope; it does not replace the Agent default connection or permit account fallback. Discovery is account-scoped and cannot silently overwrite an incompatible shared definition or authorize another account. These facts are resolved before the immutable Available Tool Set is supplied to execution.

Workspace Tool eligibility also applies the accepted directional contract. Direct and Group Main Runs receive one dedicated Agent Memory distillation Tool but no Agent Skill mutation, Agent-file write, or private-to-Agent copy capability. Subagent Runs do not receive Memory distillation and return candidate reusable knowledge to Main. Agent-owned Main Runs may receive ordinary Agent Workspace file mutation but no Skill mutation. Controlled Capability Management installs Market Skills outside model-authored Workspace editing. These role rules do not change the inherited Workspace authorization set.

### Exposure and search

Exposure divides the authorized set into a small direct default and a searchable remainder. Exposure policy is separate from Tool Definition.

```text
Authorized Tool Set
      |
      +── Default Tools ------> Context
      |
      └── Searchable Tools ---> search_tools
```

`search_tools` is a directly exposed Builtin Tool. It searches only the current authorized searchable set. Matching Tool Definitions become available in the next model request and remain available for the current Run, so the model does not need to repeat the same search.

```text
Default Tool Definitions + search_tools
                  |
                  v
                Model
                  |
          search_tools(query)
                  |
                  v
          Search authorized Registry
                  |
                  v
          Load matching Definitions
                  |
                  v
             Next model call
```

Context carries Tool Definitions into the model request; Tool System owns which Definitions are supplied. Default membership, search ranking, result bounds, and caching are deferred implementation decisions.

### Execution and dependency injection

Executor dependencies are injected when the capability is assembled or registered. Per-call execution context stays narrow: it carries only the correlation identity and cancellation needed for one accepted invocation. It does not expose Session, Task, RuntimeLifecycle, product metadata, a database session, mutable global state, or a generic service locator.

```text
Tool Registration
  - definition
  - executor
      └── explicit injected capability services

Tool Call ----> Executor.execute(input, narrow context) ----> Tool Result
```

Workspace, Memory, Task, messaging, or another capability remains responsible for its own facts and operations. Tool execution calls that owned interface instead of reading or mutating shared Agent Runner state.

Audit receives already-observed outcomes through its independent non-blocking interface. Its implementation owns asynchronous processing and its own storage transaction; Executors do not pass a business TransactionContext to Audit, wait for audit persistence or query audit logs to determine execution success. Tool Call and Result durability remains Run History's separate authoritative contract.

### Scheduling

Model System may tell a capable model that parallel Tool Calls are allowed. The model expresses independent work by emitting multiple Tool Calls in one model output and expresses dependency by waiting for one Tool Result before emitting another call in a later model turn.

```text
One model output
  ├── Tool Call A
  ├── Tool Call B
  └── Tool Call C
          |
          v
     Tool Scheduler

Dependent calls
  Tool Call A ----> Tool Result A ----> next model turn ----> Tool Call B
```

Scheduler does not reorder calls, infer dependencies, or combine calls from different model turns. New Tools default to serial execution. A separate execution policy may opt known-safe Tools into bounded parallel execution; no `effect` taxonomy or name-based read/write inference enters Tool Definition. Model-facing Tool Results return in the model's original call order even if approved parallel executions settle in another order. Different Runs are scheduled by Agent Runner rather than Tool Scheduler.

### Agent-calling Tools

Agent-calling Tools preserve three product intents: `notify`, `consult`, and `task_delegate`. `notify` sends information without waiting for completed work. `consult` asks for an answer, and `task_delegate` asks the target Agent to complete and return work. The technical execution contract has two semantics: one-way send for `notify`, and asynchronous request-result for both `consult` and `task_delegate`. Product meaning remains distinct from technical execution shape.

A2A content may contain text, file references, Artifact references, and other supported content blocks. Sending a file does not create another A2A lifecycle mode. Whether the model-facing surface uses one Tool with modes, multiple Tools, or a separate file convenience Tool is deferred implementation design.

The concrete Agent-calling Tool Executor requests Agent Runner to create the target Agent's independent Main Run. That Run resolves the target Agent's own authorization and Workspace and receives only the content and authenticated request-scoped Membership connection references explicitly carried by the A2A Input. It never receives Token bytes or implicit sender authorization. The Tool System does not create a Run directly, the target execution is not a Subagent Run in the caller's Task tree, and no separate A2A Runtime or Agent role is introduced.

```text
notify
  Tool Call ----> Agent-calling Executor ----> Agent Runner ----> target Main Run
       |
       +----> Tool Result: request accepted

consult or task_delegate
  source Tool Call ----> Agent-calling Executor ----> A2A capability ----> Agent Runner ----> target Main Run
       |
       +---- Tool Result: request accepted + request reference

  source Main Run ----> Running or Waiting

  target Run Output ----> A2A capability ----> correlated A2A Result Input ----> source Main Run History
```

Every Agent-calling Tool Call settles immediately with acceptance and a stable A2A request reference. Repeating the same request reference returns the existing target Main Run relation rather than starting another Run. For `notify`, acceptance does not claim that the target Main Run completed, and its later output does not resume the source Run. For `consult` and `task_delegate`, the source Main Run may continue briefly or enter Waiting after acceptance. When the target Main Run finishes, Agent Runner returns its output to the A2A capability, which records the result and submits one correlated A2A Result Input to the exact source Main Run. Duplicate delivery of the same request result is ignored by the Runner idempotency contract. Agent Runner resumes the source if Waiting or appends the input for its next Model Step if Running. A2A owns request correlation and result routing; Agent Runner owns only each Run's lifecycle and history and does not infer A2A intent.

The target Main Run remains independent from the source Main Run. Failure, cancellation, interruption, or completion of the source does not cancel the target. A late result is recorded by A2A but cannot resume or revive a terminal source Run; later product presentation of that result remains an A2A implementation decision.

A2A never carries the sender's User or Group Workspace, Agent Workspace, broad Tool authorization, Run History, or implicit Context. Text, files, Artifacts, and explicitly delegated Membership connection references cross the boundary only when the A2A Input includes their bounded authorized reference. Credential material never crosses.

### User-message outlet

The [unified user-message outlet](2026-09-09-user-messages-and-run-completion.md) separates communication from Final settlement. A Main-only Tool is one possible encoding, not a selected or implemented requirement. If implemented as a Tool, it uses the initiating product owner and trusted destination rather than arbitrary recipient selection; it does not control Run lifecycle. Native output is another possible encoding of the same contract. Subagents retain Parent-directed results without direct user-message access.

### Session work-control Tools

Main-only Tools expose bounded work discovery, supplementation and cancellation through the [Session-owned conversational work-control contract](2026-08-27-direct-session-input-history-and-concurrency.md#conversational-work-control). The new Main interprets the human message and selects an explicit target; the Session service validates the caller and same-Session Main relation before using Run's public ports. These operations neither dispatch new Child work through Task Tool nor ask another Agent through A2A. Their bindings use trusted execution scope, explicit services and correlated Tool results; names and schemas remain G006 implementation details. They are not available to Subagents, and registration or schema presence alone does not imply authorization.

### Task Tool

Task Tool is the optional Main Agent delegation surface. Main Agent decides whether to invoke it from current Product Input, Main-role guidance, and the Tool Description. The architecture does not define how the model detects an explicit work method or judges complexity, and Agent Loop does not force any request or model-generated step through Task Tool.

Task Tool belongs to the Main Run's small directly exposed core set. A Subagent Run is a leaf execution and does not receive Task Tool in its directly exposed set, searchable candidates, or dispatchable Run bindings. This role eligibility is separate from inherited business, Tool, and Workspace authorization.

One Task Tool Call accepts one or more delegated work descriptions and requests Agent Runner to create one Child Run for each accepted description. Each Child creation has stable correlation derived from the existing Parent Run, Tool Call, and assignment within that call, so retry cannot duplicate a Child and no Task ID is introduced. Each description becomes its Child Run Input, and every Child inherits the parent Main Run's complete resolved authorization and Workspace access. The Tool returns acceptance and Child references immediately rather than waiting for Child completion. A later Task Tool Call appends new work and starts new Child Runs; it does not update or reopen an earlier Task object.

Task Tool has no target-Agent selection. Every Child Run uses the responsible Main Run's Agent Identity, Soul, Agent Workspace, and resolved authorization. Work assigned to another Agent uses an A2A Tool and creates the target Agent's independent Main Run; that target Main Run may use its own Task Tool to create its own same-Agent Child Runs.

```text
Main Run Tool Call
      |
      v
Task Tool Executor ----> Agent Runner ----> one Subagent Run per work description
      |
      +---- immediate accepted Tool Result

Child Result / Need Input ----> correlated Child Input ----> Main Run History
```

The accepted Tool Result settles the Task Tool Call. Later Subagent Run Results and Need Input signals arrive as correlated Child Inputs to the responsible Main Run. Agent Runner resumes Main if Waiting or appends them for a later Model Step if Running. Main may submit more work through another Task Tool Call and judges whether the parent requirement is complete. Task Tool does not create a Task table, ID, persistent record, status, result object, lifecycle controller, planner, Workspace, Run type, or completion state machine.

Authorization inheritance does not copy parent model context or private Run History. Task description and normal authorized reads supply the Subagent's model-visible context.

If a Subagent determines that the delegated work needs another specialist, further decomposition, or broader coordination, it reports that need in its Run Result. The responsible Main Agent decides whether to submit another work description through Task Tool. Subagent Runs cannot recursively delegate through Task Tool.

If required human or product input is missing, Agent Runner atomically moves the Subagent to Waiting and appends a correlated Child Need Input containing the exact Child Run reference and requested information to its non-terminal Main. Main may answer from Context or request input through its product capability and enter Waiting. Task Tool then resumes the same Child Run with the answer and returns acceptance immediately; the Child's prior Context and Run History remain intact. A terminal Main causes Child cancellation in that transaction.

### Todo Tool

Todo Tool is the Subagent Run's directly exposed planning surface. It lets the Subagent record and update a small structured list of pending, in-progress, and completed execution steps for the current delegated work.

Todo belongs only to the current Subagent Run. It does not create Tasks or Runs, select Agents, own Workspace, grant permissions, persist across Runs, or act as a completion state machine. Current Todo is available to later model calls in the same Run and is re-injected after Compaction without replacing Run History.

Todo does not block Final Output. Tool guidance asks the Subagent to review remaining items before finishing and report unresolved work in Run Result. Main Run does not receive Todo Tool; Main coordination uses Task Tool and correlated Results.

### Run history without a Tool Ledger

Tool Call and Tool Result are recorded directly in Run history and become available to Context. The target architecture has no generic Tool Ledger, execution reservation, Lease, takeover, replay, or side-effect reconciliation protocol.

```text
Run History
  ├── Tool Call
  └── Tool Result
```

A terminal interrupted Run may contain a Tool Call without a Tool Result. That absence records an incomplete exchange and never authorizes automatic replay. A concrete Tool may own a provider idempotency key, receipt lookup, or status query when that external provider supports one; Tool System does not generalize those operations.

Tool Executor derives a stable external idempotency key from the committed Run and Tool Call identity and supplies it when the provider supports idempotent operations. A definite provider rejection or failure returns an ordinary error Tool Result. A timeout, disconnect, or ambiguous provider response after a possible external side effect returns a bounded `uncertain_outcome` Tool Result when the execution boundary remains alive; it must not be presented as a definite failure or authorize automatic retry. The Agent may issue an explicit capability-owned receipt or status query when available. If the execution process disappears before any Tool Result is committed, the Run becomes Interrupted and the recorded Tool Call remains without a Result.

### Presentation

Frontend Tool presentation is separate from backend Tool registration. Backend emits the canonical Tool Call and Tool Result. A frontend Presentation Registry explicitly maps canonical Tool names to capability-specific presenters; unknown or dynamic Tools use a generic presenter.

```text
Backend Tool Call / Tool Result
              |
              v
Frontend Presentation Registry
      ├── known name ----> dedicated Presenter
      └── unknown name --> generic Presenter
```

The canonical Tool name is not the localized UI title. Display title, icon, input summary, result view, and error view belong to the frontend Presenter. Presentation does not change execution facts, judge success, determine Task Completion, or enforce authorization. Other Channels own their own rendering adapters.

### No generic progress protocol

The base Tool protocol has no Tool Progress event. A Tool Call is pending until its Tool Result arrives. Progress units, meaning, frequency, and rendering differ across terminal execution, upload, remote jobs, Agent work, and other capabilities, so a shared percentage or message protocol would not provide a stable semantic contract.

If a concrete capability later needs streaming output, it may add a capability-specific side-channel event correlated by `call_id`. That additive event does not change Tool Definition, Tool Call, Tool Result, Executor settlement, or Agent Loop. Independent long-running work uses Task and Subagent Runs rather than a generic Tool Progress state machine.

### Source ownership and atomic files

Backend source is divided into Registry, authorization, exposure, scheduling, execution, source adapters, and capability-owned Tool modules. Frontend source is divided into its presentation Registry, generic fallback, and capability-owned presenters. These are module boundaries, not a requirement for separate deployable services.

The implementation removes the existing giant backend and frontend Tool aggregation files after all registrations and presenters have moved to their owning modules. It does not retain the old files as compatibility facades or secondary registries. Tool-related code leaves `AgentDetailPage.tsx`; restructuring unrelated Agent Detail behavior remains a separate task.

## Alternatives considered

### Put execution, authorization, exposure, scheduling, and presentation on one Tool interface

This creates a superclass that changes for model protocol, backend execution, permission, performance, and frontend reasons. New Tool authors must understand unrelated policies, and changing one concern reopens every Tool implementation.

### Include output schema, effect, retry, recovery, deadline, and concurrency in the base definition

These fields do not have universal current consumers and would recreate the durable execution protocol that the target architecture removes. Output remains model-visible Content Blocks; capability-specific policy stays with its real owner.

### Treat every Skill as a Tool

This conflates instructions with executable capabilities and duplicates Skill discovery in the Tool catalog. Only executable operations register as Tools.

### Expose every registered Tool directly

This repeatedly sends dozens of full schemas to the model, increases Context cost, and gives rarely used capabilities the same prominence as mature defaults. Authorized search preserves discoverability without default exposure.

### Re-resolve the complete authorized Tool set after every model output

Rebuilding exposure after the model produces a call can disagree with the Definition set the model received and adds unnecessary work. One immutable Available Tool Set supplies both model presentation and dispatch, while the concrete protected operation stays inside the captured authorization scope and reports actual resource errors.

### Keep the generic Tool Ledger for audit

The existing Ledger exists to reserve, replay, lease, take over, and reconcile exact executions. Those behaviors are explicitly excluded. Run history already records the model-visible call and result needed for audit and Context reconstruction.

### Add a generic Tool Progress stream

Different capabilities do not share one meaningful progress vocabulary. A generic protocol would add ordering, replay, throttling, and UI obligations without a current common consumer.

### Give notify, consult, and task delegation separate execution protocols

The three intents have different product meaning, but only `notify` is one-way; `consult` and `task_delegate` both settle their Tool Call with acceptance and then wait through the same correlated A2A Result Input path. Separate lifecycle machinery would duplicate Run creation, waiting, and result routing without changing their product meaning.

## Acceptance criteria

- The model-visible Tool Definition contains only `name`, `description`, and `input_schema`.
- Every Builtin, MCP, product, and external Tool enters one Registry through Tool Registration containing one Definition and one Executor.
- Tool System persists Tool Definitions and explicit Agent Tool Grants; Capability Market and Agent MCP Connections own shared registration and Agent-specific authentication, and no Secret or approval policy enters Tool Definition.
- Canonical Tool names are stable Provider-compatible identifiers, while upstream MCP names and explicit Executor bindings remain separate and no behavior is inferred from a name prefix.
- Duplicate canonical names fail registration; no downstream behavior is inferred from name prefixes, aliases, source types, or Executor classes.
- Skills remain authoritative Workspace file packages behind a logical catalog and do not enter the Tool Registry; reusable external API scripts become Tools.
- Skill bindings belong only to Agents; User/Group Skill sources are absent, and shared/private package updates follow Workspace ownership rather than granting Tool access.
- MCP uses explicit server authentication requirements and Agent-default account selection; personal use requires explicit user selection, authorized connection and task scope, without account fallback or cross-account discovery assumptions.
- Authorization produces one immutable Available Tool Set used by both Context and dispatch; Run Snapshot persists every Definition, versioned executor binding, complete resolved non-Secret executor configuration, and authorized connection descriptor required for Waiting resume.
- Deployment retains every executor binding referenced by a non-terminal Run and blocks an incompatible upgrade rather than dispatching that Run through new Tool semantics.
- Human permission follows login-session lifetime; new Runs resolve current Agent capability configuration, and active Runs retain their frozen bindings without revocation sweeps.
- Unauthorized Tools are neither directly exposed nor searchable.
- A small default Tool set and one `search_tools` Tool provide access to the authorized searchable remainder.
- Tool Call contains `id`, `name`, and `input`; Tool Result contains `call_id`, model-visible `content`, and `is_error`.
- Ordinary Tool failures return Tool Results to the Agent Loop rather than terminating the Run or entering Verify.
- Tool execution distinguishes definite failure from `uncertain_outcome`; possible external side effects are never retried automatically, while provider-supported idempotency and explicit capability-owned status lookup remain available.
- Executor dependencies are explicit and the per-call context does not become a shared lifecycle or service bus.
- Model output determines Tool Call grouping and dependency order; Scheduler only applies bounded execution policy to one model-produced batch.
- Agent-calling Tools preserve `notify`, `consult`, and `task_delegate` as product intents and support text, file, Artifact, and other approved content without treating attachments as another lifecycle mode.
- The concrete Agent-calling Executor requests Agent Runner to create the target Agent's Main Run, and every Agent-calling Tool Call settles immediately with acceptance.
- A2A request reference makes target Run creation and result delivery idempotent without a separate A2A Runtime.
- `notify` uses one-way send; `consult` and `task_delegate` use one asynchronous request-result path whose correlated A2A Result Input enters the exact non-terminal source Main Run.
- Source termination never cancels the independent target Main Run, and a late A2A result cannot revive a terminal source Run.
- A2A carries only explicit Input content and optional authenticated request-scoped Membership connection references; it never inherits the sender's Workspace, broad Tool authorization, Credential material, Run History, or implicit Context.
- Task Tool is optional and selected by Main Agent through ordinary model behavior rather than an Agent Loop rule; one call submits one or more work descriptions and starts one Child Run for each accepted description.
- Task Tool creates only same-Agent Child Runs and cannot select another Agent; A2A is the sole cross-Agent execution path.
- Child creation is idempotent from existing Parent Run, Tool Call, and assignment correlation and adds no Task ID.
- Later Task Tool Calls append new delegated work rather than updating a persistent Task; Task has no table, ID, status, result object, cross-Run lifecycle, or Workspace.
- Task Tool Executor returns acceptance immediately; correlated Child Result and Need Input enter Main Run History later without adding another lifecycle owner.
- Task Tool is directly exposed only to Main Runs; Subagent Runs cannot discover, dispatch, or recursively invoke it.
- Subagent Need Input emits a non-terminal correlated event to Main; Task Tool later resumes the exact Waiting Child with Main- or human-supplied input.
- Todo Tool is directly exposed only to Subagent Runs; it is a current-Run planning aid and never a Task, lifecycle state machine, descendant-Run creator, or completion gate.
- Tool Call and Tool Result are recorded in Run history without a generic Tool Ledger, Lease, takeover, replay, or reconciliation protocol.
- Audit uses an independent non-blocking interface and owns asynchronous persistence outside business transactions; it never determines Tool or Run outcomes.
- Frontend presentation is registered separately and has a generic fallback for unknown Tools.
- The base Tool protocol contains no generic Progress event.
- Backend and frontend Tool code is split into capability-owned modules, Tool code leaves `AgentDetailPage.tsx`, and the original giant Tool aggregation files are deleted rather than retained as compatibility authorities.

## Risks and open questions

The canonical name format and MCP namespace rules must preserve stable uniqueness without restoring name parsing as behavior. The implementation must define this contract before migrating registrations.

The initial default Tool set, search ranking and bounds, basic authorization policy, safe parallel allowlist and limit, ContentBlock variants, A2A Tool names and interface count, and frontend presentation details remain implementation decisions. Approval remains outside this Tool design until the Permission architecture owns it.

The migration must trace every current Tool producer, persisted representation, consumer, compatibility path, and cleanup path before deleting the old protocols and aggregators. This Note authorizes a clean target, not partial coexistence between old and new authorities.
