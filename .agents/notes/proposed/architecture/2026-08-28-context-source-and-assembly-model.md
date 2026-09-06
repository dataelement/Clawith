# Agent Note: Context Source and Assembly Model

Status: proposed — the macro Context source categories, ownership, and assembly boundaries are agreed but not implemented

## Problem

Every model call needs instructions, product input, execution history, Workspace discovery, and executable capabilities from different authoritative owners. Without explicit source categories, Context can become another global state store, duplicate product facts, merge private sources, expose all Tools, or leak Runtime implementation vocabulary into the model prompt.

The architecture must define the macro composition before choosing exact Prompt text, product fields, Provider message roles, caching, context-window allocation, or persistence formats.

## Proposal

### Context is a sourced model view

Context is the traceable model-visible view assembled for one model call. It owns the assembled view and source attribution but does not own or mutate the underlying Platform, Agent, product, Run, Workspace, Tool, or Model facts.

```text
authoritative sources
       |
       v
Context selection and assembly
       |
       v
model-visible request
```

Context receives explicit Run identity and authorized source scope. It never discovers a global current Session, User, Group, Run, Workspace, or Tool set implicitly.

### Source categories

Context uses eight logical source categories:

```text
1. Platform Instructions
2. Agent Identity
3. Product Input
4. Run Context
5. Workspace Discovery
6. Tool Exposure
7. Retrieved Content
8. Model Context Profile
```

Each category retains its owner and source label. Provider adapters may encode categories through `system`, `developer`, `instructions`, messages, Tool schemas, or other request fields, but wire-format differences do not change the logical ownership model.

Context defines logical source segments, not one universal physical request order. Model System Adapter maps those segments to each Provider's required ordering, message roles, cache hierarchy, continuation features, and request fields. Optional Provider conversation state, prompt caches, and KV caches are execution optimizations. Provider data required for exact next-request continuation is separately persisted and replayed by Model System; it never becomes Context or Clawith source-of-truth storage.

### Platform Instructions

Platform Instructions are the platform-owned, mandatory foundation of the model instruction layer. They define broad work, factual-integrity, execution, input-safety, and completion behavior shared by Main Runs and Subagent Runs.

Platform Instructions use model-facing language and do not expose Run, Main Run, Subagent Run, checkpoint, Runtime scope, or other internal implementation vocabulary unless the model must act on that concept. They do not contain Agent personality, User or Group data, current work, Skill content, Tool catalogs, Workspace files, or Provider settings.

One Platform Instruction version is resolved when a Run starts and remains fixed for that Run, including after Waiting and resume. New Runs use the current version. Exact wording, storage, version representation, Prompt caching, and Provider mapping remain implementation decisions.

### Agent Identity

Agent Identity comes only from the Agent product owner. It includes the executing Agent's basic product identity and Soul.

Soul is mandatory for every model call by that Agent, whether the Agent is executing a Main Run, Subagent Run, Heartbeat, Trigger, or A2A work. The executing Agent keeps its own Soul even when a Subagent Run inherits its parent Main Run's authorization.

Soul is fixed for the Run, cannot be modified by the Agent, and is edited only through an authorized Agent-management operation. It is outside Workspace and contains durable identity, responsibility, personality, working style, and behavior boundaries rather than current User, Group, Task, Goal, Tool, permission, or Runtime facts.

### Product Input

Product Input comes from the product capability that initiates or resumes a Main Run. Session, including its Goal-mode continuation, and Group, Heartbeat, Trigger, A2A, and other product capabilities remain responsible for their own source facts and provide a bounded immutable input snapshot for execution.

```text
Session ------+
Group --------+
Heartbeat ----+
Trigger ------+----> Product Input ----> Main Run Context
A2A ----------+
Session Goal -+
```

Product Input supplies the current work and the product-owned context required to interpret it. Exact contents differ by product and remain with that product's architecture. A Goal-mode iteration reuses the original `/goal` Session Input relation and cutoff and receives the Session-owned objective, committed progress, preceding disposition or execution outcome, and satisfied wake condition without inheriting an earlier Run History. Direct Session input and fixed history cutoffs follow [Direct Session Input, History, and Concurrency](2026-08-27-direct-session-input-history-and-concurrency.md).

The shared start, resume, result, and source-specific ownership rules follow [Product Input, Main Run, and Output Boundaries](2026-08-28-product-input-main-run-and-output-boundaries.md).

Product Input does not grant Tool or Workspace authorization, does not become Run History owner, and cannot override Platform Instructions or Agent Soul. Subagent Runs receive their delegated Task work description as Run Input rather than an external Product Input.

### Run Context

Run Context comes from Agent Runner and includes Run Input, current isolated Run History, Waiting requests, and ordered related inputs such as Child Result, Child Need Input, A2A Result, or human reply. Main and Subagent histories remain isolated. Parent, Child, sibling, source, and target Run History never enters implicitly.

A delegated work description enters a Subagent Run through Run Input. Task Tool acceptance enters Main Run History immediately; later Subagent Result or Need Input enters the responsible Main Run as a correlated Child Input regardless of whether Main is Running or Waiting. Task view is derived from these Run facts and has no separate source category or persistence. Fixed Session history remains bounded by the initiating Session cutoff rather than growing with concurrent Session activity.

### Workspace Discovery

Workspace Discovery comes from the authorized User, Agent, and Group Workspaces defined by [User, Agent, and Group Workspaces](2026-08-27-user-agent-group-workspaces.md).

The initiating product capability and permission system resolve the complete authorized Workspace set when a Main Run starts. Context consumes that result and never discovers additional Users, Groups, or Workspaces from Agent relationships implicitly.

```text
Direct Main Run ----> User Workspace + Agent Workspace
Group Main Run -----> Group Workspace + Agent Workspace
Heartbeat Main Run -> Agent Workspace
A2A Main Run -------> receiver Agent Workspace + explicit A2A Input
Subagent Run -------> exact Parent Main Run Workspace authorization
```

Context injects the separately labeled Guide and Index entry section from each authorized Workspace's `memory/MEMORY.md` plus that Workspace's Skill Index. User, Agent, and Group sources remain distinct, and same-named Memory topics or Skills do not silently overwrite or merge.

`files/` has no automatically injected directory tree, listing, metadata summary, or content. A file enters model-visible input only when Product Input or delegated Child Run Input explicitly references it or the Agent uses an authorized Workspace Tool to list, search, or read it. Full Memory documents and Skill packages likewise require explicit retrieval.

Workspace Discovery includes only a compact model-facing usage rule that an authorized `files/` area exists and must be inspected through list, search, and read Tools when current work may depend on files. The rule does not claim that any particular file exists and does not enumerate paths. Actual file information enters the next model call only through the resulting Workspace Tool Result.

The authorized Workspace set, Memory entry sections, and Skill Indexes are fixed in the Run-scoped source snapshot. Subagent Runs inherit the parent snapshot. Explicit writes become visible through Tool Results when relevant, but entry sections and Indexes do not refresh silently; new Runs resolve the current Workspace versions. A full Skill package is retrieved explicitly from the current controlled Workspace installation and the resulting content enters History; later file updates cannot rewrite content already observed, but a later explicit load may read updated content because the first release keeps no immutable Skill revision archive. Newly granted authorization does not expand the active Run snapshot. Permission changes follow the login-scoped policy and never erase already-observed Context, rewrite History or trigger a Context-owned reauthorization loop.

Workspace Index presence is discovery context, not authorization enforcement. Every real Workspace read or mutation still enforces the resolved scope at the Tool execution boundary.

### Tool Exposure

Tool Exposure comes only from Tool System. Context receives the directly exposed Tool Definitions selected from the immutable authorized Tool set, not the complete Tool Registry. Additional authorized Tools enter later model calls only through the agreed discovery and exposure contract in [Tool Registry, Execution, and Exposure](2026-08-27-tool-registry-execution-and-exposure.md).

Tool definitions and Tool-specific model guidance remain owned by Tool System and are not copied into Platform Instructions, Soul, Product Input, or Skill Indexes.

Tool System also applies Run-role eligibility before exposure. Task Tool belongs to the Main Run's directly exposed core set and is absent from Subagent direct exposure, search candidates, and dispatch bindings. Subagent Runs inherit ordinary authorization without inheriting the Main-only orchestration capability.

Todo Tool belongs to the Subagent Run's directly exposed core set and is absent from Main Run exposure. Its current structured Todo snapshot enters Run Context as a derived planning view, is re-injected after Compaction, and remains scoped to that Run. Todo does not become Product Input, Workspace content, or a completion state machine.

When a Provider supports deferred Tool loading, Tool references, or Tool search without rewriting the stable request prefix, Model System should use that capability. Other Providers may update the logical Tool Exposure segment after discovery. Context does not require one Provider-specific mechanism and does not rebuild unrelated source segments merely because Tool exposure changes.

### Retrieved Content

Retrieved Content is produced only after the Agent explicitly searches or reads Memory, Skills, Files, or another authorized source through Tools. The resulting bounded Tool Result enters current Run History and later model calls through Run Context. Context does not create a hidden second channel for retrieved content.

### Model Context Profile

Model System supplies Context with an immutable, secret-free Model Context Profile containing only the model capabilities and limits required for budgeting and assembly. It may include model and Provider identity, context window, maximum output, token estimation behavior, supported input and Tool forms, request overhead, caching and continuation capabilities, and resolved non-secret behavior settings.

Context receives an explicit model-visible view rather than the complete Run Snapshot. Provider endpoints, Provider Credential references, access tokens, authorization headers, secret-store locations, and raw Provider configuration are excluded from that view. The existing Run Snapshot may retain non-Secret execution settings and Credential references for Model and Tool execution; product-managed Secret material remains with Credential and is obtained only for external calls. Context selects the fields needed by the model and does not copy private execution configuration into prompts, model-visible History, Tool Results, or their presentation. Context does not select Providers or resolve Secrets.

The fixed Model Policy, Model Context Profile, Provider Adapter, and normalized result contracts follow [Model System and Provider Boundary](2026-08-28-model-system-provider-boundary.md).

### Instruction and reference boundaries

Platform Instructions and Agent Soul form the mandatory instruction foundation. Product Input states the current work. Run History, Workspace Indexes, retrieved files, Memory, and external content remain sourced working context or reference data and cannot grant permissions or override higher-level instructions merely through their text.

Tool authorization, Workspace authorization, and security enforcement remain deterministic execution-boundary facts. Prompt text is not an authorization mechanism.

### Compaction

Compaction changes only the next model-visible Context view. It may summarize or omit older model-visible content within the model budget but does not delete or rewrite Platform Instructions, Agent Soul, Product Input, Run History, Workspace files, Tool Results, or another owner's source facts.

Compaction first considers old high-volume Tool Results, retrieved files, and search output whose raw content is no longer needed. Tool Calls and matching Tool Results remain structurally valid message units; a projection never leaves an orphan Tool Result or an unresolved Tool Call.

When summary compaction is needed, the derived summary preserves the original objective, constraints, progress, decisions, unresolved work, next actions, and critical references such as exact files, identifiers, symbols, and errors. The model view combines that structured summary with a bounded recent tail of complete interaction units.

```text
Derived Compaction Summary
  + bounded recent complete interaction tail
  + events after coverage cursor
```

The summary records what source position it covers and is never treated as durable truth. Fixed Platform Instructions, Soul, Product Input, and Workspace Indexes are reassembled from their owners rather than summarized into an alternate authority.

### Incremental assembly and cache-stable requests

Context assembly is incremental by contract. A Run resolves one immutable source snapshot, reuses one current compaction base, and loads only execution events added after the previous model-view cursor.

```text
Stable Prefix
  - Platform Instructions version
  - Agent Identity and Soul version
  - immutable Product Input
  - fixed Session or product history cutoff
  - labeled Workspace Memory and Skill Index snapshots
  - initial directly exposed Tool Definitions
  - fixed model-capability guidance

Run Base
  - Initial Run Input
  - current Compaction Summary
  - summary coverage position

Incremental Delta
  - new model-visible messages
  - new Tool Calls and Tool Results
  - new ordered related inputs, including Subagent Run Results
  - Waiting requests and human replies
  - newly exposed Tool Definitions
  - optional minute-level current time when the product requires it
```

Context does not reread or recompute unchanged Platform Instructions, Soul, fixed Product Input, history cutoff, Workspace Memory entry sections, Skill Indexes, old Tool Results, or other stable sources before every model step. A Provider may still require the complete logical message sequence on every request; Model System may serialize that full view without repeating source retrieval and assembly work.

The model request preserves exact stable-prefix ordering and serialization so Model System can use Provider KV cache, prompt cache, prompt cache keys, or equivalent mechanisms when available. Stable sections do not contain random identifiers, volatile formatting, or current time. Provider caching is an optimization owned by Model System; Context guarantees stable logical segments without assuming a Provider supports caching.

Current time is not injected universally. Model System and the initiating product capability first determine whether the model already has sufficient date knowledge and whether local time materially affects the work. When time is model-visible, it is rounded to the minute, includes its timezone, belongs in the volatile delta after stable instruction and source prefixes, and changes only when the displayed minute changes. Seconds and subsecond precision require a separate product need.

The current Run does not silently refresh fixed Soul, Platform Instruction, Memory Index, Skill Index, or Product Input versions after their sources change. Explicit Tool Results make current-Run mutations visible when relevant, a controlled update affects the next explicit full Skill load after cache invalidation, and a new Run resolves the new index versions.

Compaction intentionally creates a new Run Base and coverage position. Later model steps reuse that base and continue loading only events after its cursor. Tool discovery updates the Tool Exposure segment and later request view without requiring unrelated source categories to be read again.

The implementation plan must research and validate how each stable source is fingerprinted before choosing version identifiers, content hashes, composite cache keys, or another representation. The architecture requires stable source identity and deterministic invalidation but does not prescribe one fingerprint mechanism for every source or Provider.

### Context observability

Context optimization requires segmented measurements rather than aggregate model latency. The implementation must make at least the following evidence observable before performance tuning:

```text
local Context assembly duration
source read count by category
source snapshot reuse and refresh count
logical input tokens
Provider cache-read tokens
Provider cache-write tokens
uncached input tokens
Compaction count and duration
Tool Result tokens cleared or omitted
Compaction Summary coverage position
```

Provider-specific counters remain normalized observations rather than new Context facts. The final optimization pass must use these measurements to decide cache breakpoints, source reuse, compaction thresholds, and Tool Result clearing instead of assuming which layer dominates latency or cost.

## Alternatives considered

### Build one undifferentiated System Prompt

This loses ownership, source attribution, instruction priority, and Provider-independent composition. Context remains a sourced assembly rather than one mutable text blob.

### Tell every model that it is executing a Run

Run is internal execution vocabulary and does not help the model complete ordinary work. Platform Instructions use task-facing language; Subagent-specific responsibility belongs in Task description when needed.

### Maintain separate Platform Prompts for Main and Subagent Runs

Main and Subagent execution uses one Agent Loop. Their differences come from Run Input, relationships, Context sources, and Tool availability rather than duplicated base instruction contracts.

### Inject all authorized Tools

Authorization and exposure are different decisions. Tool System supplies a small directly exposed set and preserves authorized discovery without repeatedly sending the entire Registry.

### Inject complete Memory, Skills, and Files

This makes Context grow with Workspace size and loses progressive disclosure. Context injects labeled Indexes; the Agent retrieves complete content explicitly.

### Inject a complete files directory summary

Directory trees and metadata grow with Workspace size, change frequently, and destabilize prompt prefixes. Product Input references known files, while unknown files are discovered through explicit scoped list and search operations.

### Rebuild every source before every model step

This repeats stable file, database, authorization, prompt, and token work and produces volatile prefixes that reduce Provider cache reuse. Context uses one Run-scoped immutable snapshot, one current compaction base, and incremental event loading.

### Put precise current time in the stable System Prompt

Second-level timestamps invalidate otherwise identical request prefixes without a product need. Time is omitted when unnecessary; when required, it is minute-level, timezone-qualified, and placed in the volatile request tail.

## Acceptance criteria

- Context is a per-model-call sourced view and does not own or mutate source facts.
- Platform Instructions, Agent Identity, Product Input, Run Context, Workspace Discovery, Tool Exposure, Retrieved Content, and Model Context Profile remain distinct source categories.
- Platform Instructions use model-facing language, remain fixed per Run, and are shared by Main and Subagent Runs.
- Agent Soul is mandatory, comes from the executing Agent product owner, remains fixed per Run, and is outside Workspace.
- Each Main Run receives bounded immutable Product Input from its initiating product capability.
- Subagent Runs receive delegated Task work description rather than external Product Input.
- Run histories remain isolated; parent, sibling, and concurrent Session history do not enter implicitly.
- Authorized Workspace `MEMORY.md` entry sections and Skill Indexes retain User, Agent, and Group labels; full content requires explicit Tool retrieval.
- `files/` contributes no automatic listing or summary; files enter Context only through explicit Product or delegated Child Run references and Workspace Tool Results.
- Authorized Runs receive only a generic prompt-level instruction to inspect `files/` on demand; the instruction contains no file inventory or inferred content.
- The authorized Workspace set and its Memory and Skill Indexes are fixed per Run, and Subagent Runs inherit the parent Main Run's Workspace snapshot.
- Human permission changes follow login-session lifetime; Context consumes the fixed Run scope without live reauthorization or rewriting previously observed History.
- Tool System supplies only directly exposed Tool Definitions, never the complete Registry by default.
- Run-role eligibility is explicit: Main Runs directly receive Task Tool, while Subagent Runs cannot expose, search, or dispatch it recursively.
- Subagent Runs directly receive Todo Tool, while Main Runs do not; Todo remains a current-Run planning view and does not block completion.
- Provider-specific deferred Tool or Tool-reference capabilities preserve stable prefixes when available without becoming a Provider-neutral Context contract.
- Retrieved content enters through bounded Tool Results recorded in Run History.
- Context receives only a secret-free Model Context Profile; Provider credentials and secret-bearing request configuration never cross the Model System boundary.
- Compaction prioritizes stale high-volume Tool Results, preserves valid Tool Call and Result units, and combines a structured derived summary with a bounded recent tail without deleting source facts.
- Context reuses one Run-scoped immutable source snapshot and one current compaction base and loads only events after the previous model-view cursor.
- Stable request segments preserve deterministic ordering and serialization for Provider KV or prompt-cache reuse.
- Current time is injected only when materially required; model-visible time is timezone-qualified, no more precise than one minute, and never placed in the stable request prefix.
- Fixed source versions do not refresh silently within one Run; explicit Tool Results expose relevant mutations and new Runs resolve new versions.
- Optional Provider conversation state and caches are disposable optimizations; required opaque continuation metadata is Model System-owned per-Run execution state and neither class becomes Context or Run History authority.
- Stable source fingerprinting is selected only after implementation research compares version, hash, invalidation, and Provider-cache behavior.
- Context assembly, source reads, token usage, cache reads and writes, Compaction, Tool Result clearing, and Summary coverage are observable before optimization claims are made.
- Prompt text never substitutes for deterministic Tool, Workspace, permission, or security enforcement.

## Risks and open questions

Exact source payloads, Prompt wording, message-role mapping, Provider encoding, context-window allocation, compaction policy, stable-source fingerprinting, cache APIs and keys, event cursors, telemetry schemas, and persistence representations remain implementation decisions.
