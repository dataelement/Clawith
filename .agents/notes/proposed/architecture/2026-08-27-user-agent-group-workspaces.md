# Agent Note: User, Agent, and Group Workspaces

Status: proposed — the Workspace ownership, contents, progressive-loading, and minimal Tenant/RBAC model is agreed as the basis for implementation planning but is not implemented

## Problem

The target architecture needs durable files for Users, Agents, and Groups without creating a separate Workspace for every relationship or mixing product state, Runtime internals, and subject-owned content in one file tree. Memory, Skills, and ordinary work files need one consistent file capability while retaining different model-loading semantics.

The current Agent file root and its nested `workspace/` use Workspace to mean two different things. The target model needs one top-level Workspace per subject and no second nested Workspace boundary.

## Proposal

### One Workspace per subject

Every User, Agent, and Group has exactly one persistent Workspace. In this product vocabulary, User means one Tenant Membership defined by [Account, Membership, Tenant, and Principal](2026-08-31-account-membership-tenant-principal.md), not the global Account. All three use the same fixed top-level areas:

```text
User Workspace
  ├── memory/
  ├── skills/
  └── files/

Agent Workspace
  ├── memory/
  ├── skills/
  └── files/

Group Workspace
  ├── memory/
  ├── skills/
  └── files/
```

Workspace identity is keyed only by its owning Membership, Agent, or Group. A User Workspace key contains Tenant and Membership identity; two Memberships of the same Account never share one Workspace. The architecture does not create a Workspace for each `(User, Agent)`, `(User, Group)`, Task, or other relationship. Conversation, Task Tool Call, Child Run, and other execution facts remain with Session and Run History unless an explicit operation writes selected content into a Workspace.

The same Workspace capability provides scoped list, search, read, preview, write, move, delete, current-revision conflict, and audit behavior for all three subject types. Humans receive only authorized list, search, read, and preview surfaces. Workspace mutation is available only to authorized Agent Runs through Workspace Tools. Subject type changes authorization and available content, not the basic file protocol.

### Visibility and isolation

A User Workspace is one Membership's private persistent Workspace across every same-Tenant Agent the Membership authorizes. Its owner may inspect and preview it, while authorized Runs acting for that Membership may read and mutate it. Different Memberships' Workspaces are isolated, including Memberships of the same Account in different Tenants.

An Agent Workspace is the Agent's shared persistent Workspace across its authorized Users and Runs. A Tenant Membership may inspect and preview the Agent Memory, Skills, and Files exactly when the Agent visibility owner says that Membership can see the Agent; Tenant membership alone is not sufficient. Only authorized Agent Runs mutate the Workspace.

A Group Workspace is shared within the Group. Active members may inspect and preview it, and authorized Group Runs may read and mutate it. Individual members' User Workspaces remain private and are not imported automatically.

```text
Direct Run for User U by Agent A
  ├── User U Workspace: read and write
  └── Agent A Workspace: read; Main-only Memory distillation exception

Group Run for Group G by Agent A
  ├── Group G Workspace: read and write
  └── Agent A Workspace: read; Main-only Memory distillation exception

Heartbeat for Agent A
  └── Agent A Workspace: read and write

Agent-owned Trigger or A2A Main Run for Agent A
  └── Agent A Workspace: read and write

A2A Main Run received by Agent B
  └── Agent B Workspace: read and write

Subagent Run created by Main Run A
  └── Parent Workspace direction, without Agent Memory distillation Tool
```

Subagent Run inherits the complete resolved Workspace access of its parent Main Run. A delegated Task work description has no Workspace or persistence of its own. A2A is different: its receiving Main Run uses the receiver's own Workspace and only explicit A2A Input, never the sender's Workspaces.

### Memory

Memory initially consists of one authoritative `memory/MEMORY.md` file per Workspace. The architecture has no required structured Memory database, vector store, embedding index, relationship-specific Memory, or automatically synchronized copy.

The beginning of `MEMORY.md` contains a standard compact Guide and Index. When a Run is authorized to use that Workspace, Context injects only this entry section with an explicit User, Agent, or Group source label. The remaining content is searched and read by line range on demand.

Multiple authorized Memory Indexes remain separate. Context does not merge them into one Memory source, and search is always scoped to an explicitly authorized Workspace.

Every Memory creation, edit, deletion, and Index update is explicit. Agent Final Output, Run completion, delegated-work judgment, Context compaction, search, and reads do not mutate Memory implicitly.

Direct and Group Main Runs may update the executing Agent's own Memory only through a dedicated distillation Tool. Distillation creates new Agent-owned generalized knowledge rather than copying a Membership or Group file or Memory entry. Memory owner defines first-release privacy and Secret filtering during implementation and records source Run, source Workspace type, and content hash in non-model-visible Audit. Subagent Run cannot distill; it returns a proposed reusable insight to Main for judgment. The current Run observes a successful write only through Tool Result, while the updated Agent Memory Index becomes a source only for later Runs.

### Skills

Skills are authoritative file packages under `skills/`. A Skill may contain instructions, workflows, scripts, templates, examples, references, and static resources. Skills describe how an Agent should perform work; they do not grant permissions, own product state, create another Agent role, or replace executable Tools.

Each Workspace exposes a compact Skill Index to authorized Runs. Complete `SKILL.md` instructions and auxiliary files are read on demand through the same Workspace file capability. A Skill activation is a current-Run fact, not another persistent copy of the Skill.

User Skills provide methods reusable across the User's authorized Agents. Agent Skills provide methods shared across that Agent's authorized Users and Runs. Group Skills provide flexible collaboration methods and shared working conventions without creating a Group Soul, hidden Group prompt, or collaboration state machine.

When a Run receives more than one Workspace, each Skill Index retains its User, Agent, or Group source. Same-named Skills from different Workspaces do not silently overwrite or merge. Context selection and precedence are defined later.

Installed Workspace Skill files are the only authority for that subject's Skill package. [Tenant Capability Market and Agent Installation](2026-08-31-tenant-capability-market-and-agent-installation.md) owns shared package discovery, deduplication, source, and version metadata. An authorized Agent may install a Market Skill into its Workspace, but another Agent receives no files or Context until it installs the item separately. Market source does not replace the installed Workspace content authority, and installation affects only new Runs.

The first release prohibits Agent Runs from creating, editing, deleting, or publishing Skill content. Agent may install an existing Market Skill only through Capability Management, which validates and atomically materializes the package but does not let the model rewrite it. Tenant management and later Frontend editing may update installed Skill through the same Workspace Service, Permission, package validation, atomic commit, cache invalidation, and Audit boundary.

Skill uses mainstream load-time freshness rather than immutable per-Run package revisions. The Run fixes only the Skill Index visible at start, so a newly installed, removed, or renamed Skill changes discovery from the next Run. Full `SKILL.md` and auxiliary files are read from the current installed package on explicit load; an update never retroactively changes content already placed in a model request or Run History, but the next load after Workspace Service invalidates the Skill cache reads the new content. Process restart is not required, and the first release has no Skill revision table, retained package history, or file-system watcher outside controlled Workspace mutations.

### Files

`files/` contains ordinary durable work material and outputs. Its subtree is managed through Agent Workspace Tools and has no required category layout.

```text
files/
  └── arbitrary folders and files
```

Directories such as `projects/`, `reports/`, `source-code/`, `datasets/`, and `images/` are examples only. The platform does not pre-create them, treat them as product objects, or move files automatically based on type.

Generation, authorized import, and delivery are ways a file enters or leaves a Workspace, not separate persistent namespaces. Human-uploaded or externally received attachments are usable as Product Input without being written to Workspace. The initiating product owner retains their input association and availability through execution and Waiting; temporary staging is not the sole source of a committed input. An Agent explicitly writes an attachment to its authorized Membership or Group Workspace only when the task requires it. User-private files belong to a User Workspace, Group-shared files belong to a Group Workspace, and Agent-shared files belong to an Agent Workspace.

The first release permits file publication only from Agent Workspace into the current Membership or Group Workspace. It uses revision-checked Copy, never Move, and does not mutate the Agent source. Direct and Group Runs cannot copy Membership or Group files into Agent Workspace and cannot write Agent `files/`; their outputs go directly to the Membership or Group Workspace. Agent-owned Main Runs may write Agent `files/`. Memory distillation is the only Direct or Group exception for writing the executing Agent Workspace and is not generic cross-Workspace copy.

Run Output and Child Result content may reference Workspace files without creating a separate Artifact store. Runtime temporary files, sandbox copies, caches, and uncommitted candidates are not Workspace content; they become durable only through an explicit write to an authorized Workspace.

### Agent-only mutation and concurrency

Workspace Tools are the only mutation boundary for `memory/`, `skills/`, and `files/`; Agent Runs do not bypass them to modify underlying storage, and first-release human product surfaces expose no direct mutation operation. Every readable mutable resource has a logical current revision. An Agent mutation supplies the revision it was based on, and Workspace commits only when that revision is still current.

Later Frontend editing may let an authorized human mutate Workspace content, but it must call the same Workspace mutation contract with Permission, Revision/CAS, atomic commit, and Audit. It cannot write storage directly or introduce a second mutation authority.

```text
read content + revision
        |
        v
Agent prepares mutation without holding a lock
        |
        v
atomic commit if revision still matches
        |
        +---- success ----> new revision
        |
        `---- conflict ---> latest revision ---> Agent rereads, merges, and retries
```

Workspace uses only a short resource-scoped write lock while validating and atomically committing one mutation. No lock extends beyond that storage commit into model execution, the surrounding Tool operation, a Run, or another external operation. Readers observe either the complete earlier revision or the complete committed revision and never a partial write.

A revision conflict means another Agent committed first. It is a model-visible Workspace Tool Result, not human Need Input. The executing Agent reads the latest content, semantically combines the concurrent Agent change with its intended change, and retries against the new revision. Workspace does not apply silent last-write-wins, discard either accepted change, or guess a generic text merge. The Agent must not persist unresolved conflict markers as a successful merge.

Automatic resolution is bounded so sustained contention cannot create an infinite retry loop. If repeated conflicts prevent convergence, the Agent chooses a non-destructive resolution that preserves the competing content, such as producing a separate candidate for a non-mergeable resource, and reports the resulting file relation in its normal Run Result. Conflict handling never pauses for a human merge decision and never overwrites a newer revision silently.

Create, delete, move, and rename operations apply equivalent revision checks to the affected resource and namespace. A multi-file Skill installation or update is staged and published atomically as one package so another Run cannot observe a partially updated Skill. Current revision is a compare-and-swap concurrency token, not a Git commit, retained version history, branch, snapshot, recycle bin, or recovery guarantee. The concrete revision representation, storage lock, retry bound, and merge prompt remain implementation decisions; version retention, backup, and accidental-deletion recovery are deferred product decisions.

### Product configuration stays outside Workspace

Workspace is not a file serialization of every subject or Runtime fact. Product configuration and lifecycle state remain with their owning modules.

```text
User product object  ----> Profile and identity
Agent product object ----> Soul
Group product object ----> Announcement and Group settings
Heartbeat module --------> Heartbeat policy and scheduling
```

Soul is mandatory Agent identity and behavior configuration. It is loaded by Context for every Agent model call, cannot be modified by the Agent, and is edited only through an authorized Agent-management operation. It may use Markdown internally but is not exposed through Workspace file operations.

Group Announcement is public Group product content, not Group Soul, Memory, or Skill. Long-term Group knowledge belongs in Group Memory, and flexible collaboration behavior belongs in Group Skills.

Session and its Goal-mode configuration, Task Tool Calls, Child Run facts, Run History, Focus, Trigger, Schedule, messages, credentials, permissions, model configuration, file revisions, locks, and audit metadata remain outside Workspace even when their implementations use persistence.

### Minimal Tenant and RBAC boundary

Workspace authorization uses only the existing product relationships needed for the first implementation:

```text
Tenant boundary
  - cross-Tenant access is denied

User Workspace
  - owned by that User
  - human owner may list, search, read, and preview
  - authorized Runs acting for that User may read and mutate

Agent Workspace
  - a Membership that can see the same-Tenant Agent may list, search, read, and preview
  - an unseen Agent cannot be reached through Workspace API, direct path, or known Agent identity
  - authorized Agent Runs may read and mutate
  - Soul and Agent product configuration remain Tenant-admin operations

Group Workspace
  - active members may list, search, read, and preview
  - authorized Group Runs may read and mutate
```

Subagent Runs inherit the parent Main Run's resolved Workspace access exactly. A2A resolves the receiver's own Tenant and Workspace access and never inherits the sender's. The initial architecture has no company/private/custom Agent modes, per-file ACL, directory ACL, ABAC, policy engine, or capability-token hierarchy. More granular policy requires a later product decision.

Workspace does not own or duplicate Agent visibility rules. [Minimal RBAC and Agent Visibility](2026-08-31-minimal-rbac-and-agent-visibility.md) supplies one visibility decision used consistently by Agent discovery, Session creation, A2A target discovery, and Agent Workspace preview.

Human Workspace access follows the captured login scope. New Runs resolve current Agent-owned Workspace configuration within that scope; existing Runs keep their Snapshot and do not poll later permission changes. Explicit cancellation remains Runner-owned, and an actually missing file returns an owned resource error. Workspace does not rewrite prior Context or Run History.

The initial product exposes no human Workspace create, edit, delete, move, or rename operation. A human changes Workspace content by instructing an Agent, which performs the authorized mutation through Workspace Tools. Concurrent Agent mutation uses revision checks and Agent-managed merge without introducing another permission layer.

## Alternatives considered

### Create a Workspace for every User-Agent relationship

This multiplies state with every relationship, fragments one User's continuity across Agents, and requires ambiguous merge and precedence rules. One Workspace per subject preserves continuity without combinatorial storage.

### Keep a subject root plus a nested workspace directory

Two Workspace meanings make path ownership and Tool behavior unclear. The subject Workspace is the only root; ordinary files live under `files/`.

### Store Memory, Skills, and Files in separate persistence systems

All three are subject-owned files and benefit from one file capability. Their different Context and behavior semantics are expressed by their fixed top-level areas rather than duplicate storage and mutation protocols.

### Put Soul, Heartbeat, Announcement, and product state in Workspace

These facts drive identity, product behavior, scheduling, or lifecycle and have independent owners. File placement for convenient editing would create competing authorities and allow general Workspace mutation to change protected product behavior.

### Write Memory automatically when work ends

Automatic summarization can persist incorrect conclusions or move private information into shared Workspaces. Memory changes remain explicit Tool or product actions.

## Acceptance criteria

- Every User, Agent, and Group has exactly one persistent Workspace.
- No Workspace is created for a User-Agent or other relationship pair.
- Every Workspace has fixed `memory/`, `skills/`, and `files/` areas and no nested second Workspace boundary.
- User Workspaces are isolated from other Users and remain continuous across authorized Agents.
- User Workspace means Membership Workspace and is keyed by Tenant and Membership, never by global Account alone.
- Agent Workspaces are shared across the Agent's authorized Users and Runs.
- A Membership can preview an Agent Workspace if and only if it can see that Agent; direct Workspace access cannot bypass Agent visibility.
- Group Workspaces are shared within the Group without importing members' User Workspaces.
- Memory initially consists of one `memory/MEMORY.md` per Workspace; only its labeled Guide and Index entry section is injected automatically and all other content requires scoped search and read.
- Skills are authoritative Workspace file packages; only their labeled Index is injected automatically and full instructions and resources are read on demand.
- User, Agent, and Group Memory and Skill Indexes remain separate and retain source identity.
- `files/` is an arbitrary durable file tree; uploads and outputs do not create additional persistent namespaces or a separate Artifact store.
- Direct and Group Runs write ordinary files only to their Membership or Group Workspace; Agent-owned Main Runs may write Agent files, and Agent-to-Membership/Group file publication is one-way Copy.
- Membership and Group files cannot be copied or moved into Agent Workspace in the first release.
- Direct and Group Main Runs may distill only generalized Agent Memory through a dedicated Tool; Subagent Runs only return proposals, and new Memory Index content becomes available from the next Run.
- Agent Runs cannot create, edit, delete, or publish Skill in the first release; controlled Market/Admin installation or future Frontend editing invalidates caches, and the next explicit load reads current content without a Skill revision system.
- Authorized humans may inspect and preview Workspace content but cannot mutate it directly; all Workspace mutations come from authorized Agent Runs through Workspace Tools.
- Every Workspace mutation and cross-Workspace publication is explicit and authorized.
- Every mutable Workspace resource uses revision-checked atomic mutation; locks remain resource-scoped and cover only storage commit rather than model, Run, or surrounding Tool latency.
- Agent-Agent Workspace conflicts are resolved automatically by the executing Agent through latest-content semantic merge and bounded retry, never by silent last-write-wins or human conflict handling.
- Repeated contention preserves competing content through a non-destructive Agent-selected result rather than overwriting a newer revision or persisting unresolved conflict markers.
- Multi-file Skill installation and update publish one complete package atomically.
- Soul, Heartbeat, Announcement, product state, Runtime state, and operational metadata remain outside Workspace with their owning modules.
- Workspace authorization uses Tenant isolation, User ownership, resolved Agent visibility for Agent Workspace preview, and active Group membership; these relations grant humans preview access and authorized Runs scoped mutation access without a relationship Workspace or fine-grained file policy.
- Workspace consumes pre-resolved login/Run scope without live permission polling or revocation-driven cancellation.
- Permission detail beyond the accepted minimal Tenant/RBAC model, Context precedence, and concrete file APIs remain later product or implementation decisions.

## Risks and open questions

Subagent Runs inherit their parent Main Run's resolved Workspace authorization but not Main-only Agent Memory distillation eligibility. Concrete authorization queries must implement the accepted Tenant, User-owner, Agent-visibility, and Group-member rules without adding relationship Workspaces or finer ACLs.

Context must preserve source identity when same-named Skills or conflicting Memory appear in multiple authorized Workspaces. Any permission model beyond the minimal Tenant, owner, and membership rules requires a later product decision.

Agent Memory distillation is an accepted first-release privacy risk. It may transform facts observed in a Membership or Group Run into Memory shared with every Membership that can see the Agent. The first release relies on the Memory owner's bounded content, source audit, and implementation-time privacy and Secret filtering, but it does not provide deterministic data-owner consent, PII classification, preview approval, or revocable publication. Those controls belong to the later Memory security version; this capability must not be represented as safe for untrusted private data merely because the model calls it generalized knowledge.

The implementation must choose current-revision, atomic-commit, bounded-retry, and package-publication mechanisms that preserve these semantics across every Agent process that may mutate the same Workspace. This choice must not turn model latency into lock duration or require human conflict resolution. Retained version history and accidental-deletion recovery are not part of the initial Workspace contract.
