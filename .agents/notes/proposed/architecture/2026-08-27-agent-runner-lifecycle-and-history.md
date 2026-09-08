# Agent Note: Agent Runner Lifecycle and Run History

Status: proposed — the Agent Runner boundary is agreed but not implemented

## Problem

Main Runs and Subagent Runs use the same Agent Loop but need one execution boundary for identity, non-blocking start, waiting, resume, cancellation, status, parent-child relationships, and isolated history. Agent Runner must remain narrower than a conventional durable Runtime and must not absorb product routing, parent-requirement judgment, Context, Model, Tool, Memory, Workspace, or delivery ownership.

## Proposal

### Agent Runner

Agent Runner is the common execution and lifecycle entry for every Run. It may be an application module and does not require a separately deployed service.

```text
Product capability ----> Agent Runner ----> Main Run

Main Run Task Tool ----> Agent Runner ----> Subagent Run
```

Agent Runner creates Run identity, invokes Agent Loop, owns Run Status and Run History, records parent-child Run relationships, routes Main Run outcomes to the initiating product capability, routes Subagent outcomes to the responsible Main Run as correlated Child Result Inputs, and releases execution resources. It does not interpret product input or delegated work descriptions, judge parent-requirement or Goal completion, assemble Context, select a model, register or execute Tools, manage Workspace facts, or deliver product messages.

### Initiation and creation

The owning product capability initiates a Main Run and supplies Run Input. A Main Agent may call Task Tool; its concrete Executor requests Agent Runner to create one same-Agent Subagent Run per accepted delegated work description. Each Child Run Input contains that description, uses the Parent Run's Agent identity, and receives exactly the Parent Main Run's resolved authorization scope. Task Tool cannot select another Agent; cross-Agent work uses A2A and creates the target Agent's independent Main Run. Agent Runner remains the only Run creator.

Product source and output ownership follow [Product Input, Main Run, and Output Boundaries](2026-08-28-product-input-main-run-and-output-boundaries.md); Agent Runner consumes only the shared execution contract.

```text
Human or Goal continuation
  └── Session initiates Main Run
        └── Agent Runner creates Main Run

Group, Heartbeat, Trigger, A2A, or other product input
  └── owning product capability initiates Main Run
        └── Agent Runner creates Main Run

Main Run calls Task Tool
  └── Task Tool Executor requests Subagent Run
        └── Agent Runner creates Subagent Run related to parent Main Run
```

No external or product input enters a Subagent Run directly. Task is only the Run-scoped delegated work description carried by Task Tool into Child Run Input; it is not an initiator, record, identifier, or lifecycle actor.

Only Main Runs may request Subagent Runs through Task Tool. Agent Runner rejects a Subagent-originated recursive creation request even if ordinary business authorization is otherwise inherited.

### Minimal operations and non-blocking execution

Agent Runner supports three conceptual operations:

```text
start
resume
cancel
```

`start` creates a Run for one initiator-owned source identity and returns its reference without waiting for Agent Loop to finish. Repeating `start` with the same initiator and source identity returns the existing Run reference rather than creating another Run. `resume` submits one explicitly related input with its owner-issued source identity to an existing non-terminal Run; the input may be a human reply, correlated Child Result or Need Input, correlated A2A Result Input, or another authorized product input. If the Run is Waiting, Agent Runner records the input, changes it to Running, and schedules Agent Loop. If the Run is already Running, Agent Runner records the input without changing status, and Context includes it in the next Model Step Delta. `cancel` applies one authorized cancellation request from a product owner, User, administrator, permission owner, or other valid caller to the target Run.

Agent Runner atomically serializes related-input commits per Run and appends concurrent inputs in commit order. For one target Run, the same source identity is accepted at most once; a duplicate submission returns the already-accepted outcome without another History entry, status transition, or scheduling action. Run History and the existing model-view cursor are the pending-input and deduplication record; there is no separate input queue, idempotency table, or event bus. A terminal Run rejects new input for execution. The source owner retains a late result according to its own product contract, but the result cannot revive the Run.

Related-input acceptance acknowledges its History commit without waiting for a Model request or Tool execution. Agent Loop consumes new input at the next safe Model-call boundary after the current request and required Tool exchanges settle. Context reads only the delta after its existing history position and preserves valid Tool Call/Result units. This does not change Session routing: ordinary human input still starts a new Main Run unless it explicitly replies to the exact Waiting Run under the Session contract. A newly started Main may then use [Session-owned conversational work control](2026-08-27-direct-session-input-history-and-concurrency.md#conversational-work-control) to submit an authorized related input or cancellation to another Main in that Session. Run consumes the resolved operation without interpreting conversational intent, choosing a target or transferring Child ownership.

Wake notifications are scheduling hints; Run History and the model-view cursor remain the authority for pending input. Input acceptance and scheduling must not leave committed input stranded between an empty-input check and suspension. Repeated notifications never create a second execution loop for the same Run. This coordination uses the existing Run, History, and in-memory scheduler and does not add cross-Run recovery; execution loss retains the normal Interrupted contract.

One product owner may start multiple Main Runs independently. One Main Run may have multiple active Subagent Runs created through Task Tool. Product code or a Tool Executor may subscribe to output, wait for an outcome, or acknowledge start immediately without changing Run lifecycle ownership.

### Run Status

Run Status contains only execution lifecycle:

```text
Running
Waiting
Completed
Failed
Cancelled
Interrupted
```

```text
Running
   +---- Main Run Need Input ----------> Waiting ---- resume ----> Running
   +---- Subagent Need Input ----------> Waiting ---- resume ----> Running
   +---- child Result received --------> Running
   +---- Final Output -----------------> Completed
   +---- unrecoverable error ----------> Failed
   +---- explicit cancel --------------> Cancelled
   +---- execution lost ---------------> Interrupted
```

Waiting is non-terminal. Completed, Failed, Cancelled, and Interrupted are terminal. Task, Todo, and Goal do not add lifecycle states to Run Status.

The first release has no maximum number of Model Steps, Token quota, total Run wall-clock limit, or idle timeout. A progressing Run continues until it emits Final Output, enters Waiting, encounters an owned failure, is cancelled, or loses execution. Waiting releases its execution slot. Individual Provider, Tool, Sandbox, and external I/O operations must remain technically bounded by their owner so one hung call cannot retain the Runner indefinitely; exact timeouts, error classification, retry, and presentation are implementation decisions rather than another Run limit.

Agent Runner serializes related-input commit with both Waiting and terminal outcome commit. Waiting or Completed cannot commit from a model decision while an already-committed related input remains absent from its Model Step; Agent Loop processes that input before deciding again. If Waiting commits first, a later related input resumes the Run through the ordinary resume operation. If Completed, Failed, Cancelled, or Interrupted commits first, a later input cannot change the terminal status. Explicit cancellation and unrecoverable failure may terminate a Run even when unconsumed inputs remain.

A Subagent uses Waiting for missing human or product input and preserves its Run History. Agent Runner commits the Child Waiting fact and its correlated Child Need Input to the responsible Main Run in one Parent-first transaction without completing the Child. If Parent is Running, the input remains ordered for its next Model Step; if Parent is Waiting, the same transaction resumes it. If Parent is already terminal, the transaction cancels Child instead of leaving it Waiting. Main may answer immediately or enter its own Waiting state while obtaining human input.

Agent Runner is the only Run Status writer. It records structured Agent Loop events and explicit cancellation or execution-loss facts. Ordinary Tool errors return Tool Results to Agent Loop and do not directly fail the Run.

### Run History

Agent Runner uniquely owns Run History:

```text
Run Input
model-visible messages
Tool Calls
Tool Results
Waiting requests and related input
Run outcome
```

Delegated work descriptions appear in the parent Task Tool Call and Child Run Input. Task Tool acceptance appears as the immediate Tool Result. Later Subagent outcomes remain in Child Run History and enter Parent Run History as correlated Child Inputs whether Parent is Running or Waiting. Task and Todo do not create separate execution histories.

Agent Loop submits execution events; Agent Runner writes them. Context reads the exact Run History it is assembling. Product capabilities and delegated-work UI views may reference or project Run outcomes but do not become alternative Run History writers.

Run History is an execution and audit record, not a Checkpoint of process, coroutine, network connection, model request, or in-flight Tool implementation state.

### Persistence shape and upgrade contract

The logical execution authority is `Run` plus append-only `Run History`. The initial physical design uses `agent_runs` for the current lifecycle aggregate, `agent_run_snapshots` for one immutable start snapshot, `agent_run_history` for ordered execution facts, and `run_context_projections` for replaceable Context compaction state. Only `agent_runs` and `agent_run_history` own execution lifecycle facts. Snapshot has no transitions, and Context Projection may be deleted and rebuilt.

`agent_runs` remains a narrow frequently locked row containing Tenant, Agent, optional same-Agent Parent, Status, initiating owner and stable source identity, latest History sequence, active Waiting reference, and lifecycle timestamps. `agent_run_snapshots` contains the immutable secret-free Agent Identity and Soul, resolved authorization and Workspace sources, complete Available Tool Set with versioned executor bindings, resolved non-Secret executor configuration and authorized connection descriptors, Model Policy and Context Profile, product input reference and cutoff, schema version, and content hash. `agent_run_history` assigns one per-Run sequence to each initial or related input, normalized model output, Tool Result, Waiting request, and terminal outcome. `run_context_projections` contains only a derived compaction base, coverage cursor, and rebuild metadata.

The database enforces the Run aggregate's structural invariants. `agent_runs` has globally unique `id`, unique `(tenant_id, id)` and `(tenant_id, agent_id, id)` keys, and a unique start identity `(tenant_id, initiator_kind, initiator_owner_id, source_key)`. All four source-identity fields are non-null. A Child Run references its Parent through `(tenant_id, agent_id, parent_run_id)`, so Parent and Child cannot cross Tenant or Agent; `parent_run_id` is either null or different from the Child identity. Main-only Task dispatch remains enforced by Agent Runner and Task Tool, the only Run writers, rather than a recursive database Trigger.

`agent_run_snapshots` uses `run_id` as its primary key, giving each Run exactly one Snapshot. `agent_run_history` uses `(run_id, sequence)` as its primary key with positive sequence values. Initial and related input rows carry a non-null source kind, owner identity, and source key; a partial unique index on `(run_id, source_kind, source_owner_id, source_key)` for those input kinds makes their submission idempotent. `run_context_projections` uses `run_id` as its primary key because a Run has at most one replaceable projection. Tenant-bearing child tables use `(tenant_id, run_id)` foreign keys to `agent_runs`; database relationships never infer Tenant equality from application filtering.

Run Status is a closed database constraint containing only `Running`, `Waiting`, `Completed`, `Failed`, `Cancelled`, and `Interrupted`. `Waiting` requires one active Waiting reference, every other Status forbids it, terminal Status requires `finished_at`, and Running or Waiting forbids `finished_at`. These checks protect row shape but do not create another transition owner.

Every structured Snapshot and History payload records an explicit kind and schema version and is decoded through a closed typed contract. The stored raw authoritative payload remains available after upgrade. New target releases must either retain a decoder that preserves the existing semantics or perform a verified lossless migration; an unknown or unsupported stored version blocks upgrade or startup rather than being skipped, emptied, defaulted, or reinterpreted. Committed Run data remains readable after upgrade. If the upgrade stops the Runner, the first-release interruption rule terminates both Running and Waiting Runs; retained data does not imply executable continuation. Projection and cache formats may be invalidated and rebuilt because they are not authority.

`start` atomically inserts one Running Run, one immutable Snapshot, and the initial History input. Related input locks the non-terminal Run, deduplicates the owner-issued source identity, appends the next History sequence, and changes Waiting to Running in the same transaction. Waiting, cancellation, and terminal outcomes likewise append their History fact and update Status atomically. A Model Step records the History sequence it read. The pending-input check and Waiting or Completed transition share one short transaction under the same Run-row lock used by input acceptance; neither transition may ignore related input committed after that read. No such lock spans Model or Tool execution.

History sequencing uses a short row lock on the target `agent_runs` row: the transaction reads `latest_history_sequence`, inserts the next value, and updates the aggregate before commit. It does not use a Tenant-wide or global lock. Different Runs therefore append independently, while concurrent writes to one Run serialize in commit order without duplicate or skipped committed sequence values. Duplicate start or related-input submissions resolve through the unique indexes and return the existing accepted result.

The target has no Run Command, command claim or execution retry, LangGraph Checkpoint, Runtime Event projection, generic Run Relation, Run Output, or execution-to-product reconciliation table. Product owners store Session, Group, A2A, Trigger, Heartbeat, and delivery relations and results. Model System separately owns any required temporary Provider continuation state.

Run Snapshot is an internal execution record, not a model request. It may retain fixed non-Secret Provider routes, Credential references, and Tool execution settings for their owning executors. Context selects an explicit model-visible view from the existing sources; it does not serialize the whole Snapshot into a prompt or create another authoritative configuration copy. Product-managed Secrets remain in Credential and are obtained only at external execution. No additional snapshot service, configuration platform, or generic filtering framework is required.

### Durable owner handoff

Run terminal settlement and a same-database initiating owner's result record use one transaction through an in-process owner Outcome Consumer. The owner writes its own Session execution result, Group result, Trigger execution result, Heartbeat result, or equivalent fact without becoming a Run Status or History writer. Under the [unified user-message outlet](2026-09-09-user-messages-and-run-completion.md), this result is distinct from user-visible messages: Final does not create an automatic chat reply, and message publication does not alter Run Status. If owner result recording fails, the terminal transaction rolls back; Agent Runner may retry terminal settlement from the already-produced Final Output without calling Model or Tool again. External Channel delivery occurs after commit under the Channel owner's independent status and retry contract.

Outside service-wide interruption, Child terminal outcome and its correlated Parent Input commit atomically. Agent Runner locks Parent before Child, appends Child outcome, changes Child Status, appends the exact Parent Input, and resumes a Waiting Parent in one transaction. Ordinary Parent terminal settlement locks Parent and its bounded active Children in a deterministic order and commits Parent outcome plus Child cancellation outcomes together. The service-wide cleanup rule below interrupts the whole non-terminal family instead of delivering a wakeup. A process cannot leave a terminal Parent with a permanently active Child or a Completed Child whose result was never accepted by its non-terminal Parent.

A2A target is independent and therefore uses an A2A-owned durable request/result handoff. Target terminal outcome and A2A result record commit together. A2A then submits the exact result to the source Run idempotently and records delivered, or records `source_terminal` when the source can no longer accept input. Pending delivery survives process loss and may be retried without replaying either Run. This is capability-owned result delivery, not a generic Runtime reconciliation or event bus.

Product input acceptance may precede Run admission. Each product owner persists whether its input has no Run, started Run, explicit admission failure, or retryable pending admission according to that product's contract. It uses the same owner-issued source identity on retry and never presents an unstarted input as Running.

### Isolation and explicit Context access

Every Run has isolated history. Context receives an explicit Run identity and authorized source set; it never queries a global current Run or implicitly merges concurrent histories.

A Subagent Run receives its delegated work description, its own history, and the complete resolved authorization and Workspace access of its parent Main Run. Inheriting authorization does not copy model-visible context: the Subagent Run does not automatically read its parent or sibling Run History.

The parent Main Run receives a Child outcome only as a correlated Child Result Input. An A2A source Main Run receives target output only as a correlated A2A Result Input owned by A2A. Another Run's private history becomes visible only through an explicit Run Result, Session fact, Workspace file, or another authorized source.

The initiating Session supplies the fixed history cutoff defined by [Direct Session Input, History, and Concurrency](2026-08-27-direct-session-input-history-and-concurrency.md). Context may rebuild from that cutoff but cannot enlarge it implicitly.

### Waiting, parent termination, and interruption

Waiting pauses only one Run and releases its execution resources. A Main Run may wait for one or more Subagent Run outcomes without blocking Session or unrelated Runs.

When a Subagent Run completes, Agent Runner uses its parent-child relation to commit the Child outcome and one Child Result Input containing the Child Run reference and outcome to the responsible Main Run atomically. It resumes a Waiting Main Run or leaves the input ordered in a Running Main Run's History for the next Model Step. The originating Task Tool Call was already settled by acceptance; no Task record routes or stores the outcome.

When a Child Need Input event occurs, Agent Runner locks Parent before Child and atomically appends the Child Waiting request, changes Child to Waiting, and appends one idempotent Parent Input keyed by Child Run and Waiting reference. A later Task Tool resume operation supplies the answer to the exact Child Run, which continues with its existing History. The committed Child Waiting fact cannot exist without its Parent notification. Ordinary per-Run Parent termination cancels the Child in the same transaction; service-wide cleanup follows the all-Interrupted rule below.

During ordinary per-Run settlement, if a parent Main Run becomes Completed, Failed, Cancelled, or Interrupted, Agent Runner atomically cancels its still-active Subagent Runs in the Parent terminal transaction. Service-wide shutdown and startup cleanup instead interrupt every non-terminal Main and Child together under the specific rule below. Completed does not wait for Child completion and adds no completion gate; if Main finishes prematurely, that execution error is accepted and its abandoned Child work is cancelled. A late Child outcome may remain recorded but cannot revive the parent or settle new work.

Authorization is resolved before execution under [Login-Session Authorization](2026-09-06-login-session-authorization.md). Runner does not poll permission changes or cancel Runs because an authorization record changed. It retains explicit cancellation and parent-child cancellation; missing execution resources produce owned errors rather than a separate revocation workflow.

An A2A target Main Run is not a Child Run of its source. Source Main Run failure, cancellation, interruption, or completion therefore does not cancel the target. A2A may submit a correlated result only to the exact non-terminal source Main Run; Agent Runner resumes it if Waiting or appends the result for its next Model Step if Running. Agent Runner rejects attempts to resume a terminal source Run.

Failed means Agent Runner received a structured unrecoverable execution error while the execution boundary remained alive. Interrupted means execution was lost or terminated by service-wide shutdown/startup cleanup; this includes Waiting Runs in the first release. Agent Runner does not replay or reconstruct lost model requests, Tool Calls, process state, or code execution points.

A structured Model Error reporting missing or uncommitted required Provider continuation metadata makes the Run Failed. If execution disappears before a Model Step and its required metadata are committed, the Run becomes Interrupted. Agent Runner does not reconstruct either case from Run History because Provider execution metadata is owned by Model System and is not a replayable Checkpoint.

### No durable execution layer

Agent Runner does not implement a Durable Coordinator, cross-Worker takeover, arbitrary-execution-point Checkpoint recovery, Lease ownership, or generic side-effect reconciliation. A future Run may use committed facts from prior work without reviving that execution.

### Initial single-Runner execution

The first-release shutdown/restart choice is recorded in [Service-wide Run interruption](2026-09-07-service-wide-run-interruption.md); it supersedes the earlier Waiting-preservation proposal without changing normal in-service resume.

The first target release runs exactly one Agent Runner instance with one bounded in-memory admission queue, one bounded asynchronous execution-slot pool, one in-memory active-Run registry, and one in-memory Tenant-to-Agent-to-Run fair execution scheduler. It has no Worker table, execution owner or fencing field, Worker heartbeat, distributed claim, durable execution queue, advisory lock, or `SKIP LOCKED` scheduling protocol. Fifty occupied execution slots are concurrent asynchronous execution quanta rather than fifty Worker processes. Blocking or CPU-heavy Tool and Sandbox work leaves the Runner event loop through bounded execution venues.

Admission and execution scheduling are separate. The admission queue reserves bounded capacity before a new Run is created and carries that accepted Run to its first execution. The execution scheduler selects the next quantum for already admitted Running Runs. A Run does not reserve admission capacity again when it yields, and admission saturation cannot reject its scheduler re-entry. Scheduler membership is ephemeral process state bounded by admitted active Runs; it adds no Run Status, Run History fact, Checkpoint, replay position, or durable queue.

One execution quantum is one Model Step or one bounded Tool batch. After either quantum settles, Agent Runner releases the scarce execution slot. A Run that remains Running and has another operation ready joins the tail of its in-memory scheduler rotation before it may receive another slot. Completed, Failed, Cancelled, Interrupted, and Waiting Runs do not re-enter. Waiting for a scheduler turn does not change `Running` to `Waiting` and is not persisted.

The scheduler selects round-robin first among runnable Tenants, then among runnable Agents in the selected Tenant, then among runnable Runs for the selected Agent. Each runnable identity occupies at most one position in its parent rotation and returns to the tail only while it still has ready descendants. With `T` continuously runnable Tenants, each Tenant receives a dispatch within at most `T` scheduler allocations; within a selected Tenant, a continuously runnable Agent receives a turn within at most its runnable-Agent count; within a selected Agent, a continuously runnable Run receives a turn within at most its runnable-Run count. Run count under another Tenant therefore cannot increase the first bound. The measurable qualification case and dispatch observation are owned by [Capacity, Performance, and Responsiveness](2026-08-28-capacity-performance-and-responsiveness.md).

Agent Runner reserves admission capacity before creating a Run. If the bounded admission queue is full, it rejects admission without creating the Run; the initiating product fact remains committed and may retry the same stable source identity later. Database failure releases the reservation without creating a Run. After capacity is reserved, Runner atomically creates Run, Snapshot, and initial History, enqueues the Run for first execution after commit, and returns its reference. If the process remains alive but initial enqueue fails after commit, Runner first commits Interrupted with its terminal History outcome and only then releases the admitted Run's capacity. A failed terminal commit retains that capacity while settlement is retried, without repeating Model or Tool execution. A process loss after database commit but before or during execution leaves a Running Run that the startup sweep marks Interrupted rather than replaying.

Before the single Runner becomes ready after process start, it marks every pre-existing non-terminal Run (Running or Waiting, Main or Subagent) Interrupted and appends its terminal History outcome. Cleanup locks Parent before Child and settles each affected family atomically; existing terminal outcomes remain unchanged. It does not wake a Waiting Parent or enqueue any inherited execution. The deployment must prevent overlapping Runner instances and use stop-then-start replacement; readiness remains false until the startup sweep completes. The first release deliberately adds no code- or database-enforced singleton lock or fencing. Runner replicas must equal one, Runner must not start in multiple Uvicorn workers, and deployment validation must reject rolling overlap. Starting overlapping Runner processes is an unsupported deployment state that may cause duplicate or uncertain external side effects. A later need for multiple Runner instances, rolling overlap, or execution high availability requires a new execution-ownership and fencing decision rather than silently adding claims to this design.

Cancellation, terminal settlement, and scheduler re-entry serialize against the current Run Status. Explicit cancellation removes a ready Run from the scheduler, signals any in-flight bounded operation, commits Cancelled with its existing Child cancellation contract, and prevents a late completion callback from re-entering the Run. A structured unrecoverable Model, Tool-boundary, or scheduler error while Agent Runner remains alive commits Failed and does not re-enter; ordinary Tool errors remain Tool Results. Graceful Runner shutdown closes new admission and input execution acceptance, stops dispatch, signals and drains in-flight bounded operations, marks every remaining Running or Waiting Main/Subagent Run Interrupted with its terminal History outcome, and discards scheduler entries. Service-wide settlement does not use ordinary Child-to-Parent wakeup or Parent-to-Child Cancelled propagation: all remaining non-terminal members receive Interrupted. Abrupt process loss leaves the same settlement to the next startup sweep before readiness. Committed History, Snapshot and Context source facts remain available for inspection or explicit future work, but restart does not resume any old Run or replay a quantum. These lifecycle hooks impose no Run duration or idle timeout while the service remains operating. Later operational policies may preserve selected Waiting Runs or drain work, but no strategy framework, takeover or checkpoint recovery is introduced now.

## Alternatives considered

### Let product capabilities or Tool Executors invoke Agent Loop directly

They would duplicate Run creation, waiting, cancellation, status, and history protocols. All execution enters through Agent Runner.

### Let Task own Subagent Run lifecycle

Task is only a Run-scoped work description. Main Agent decides delegation and parent-requirement completion through ordinary model behavior; Agent Runner owns every Run lifecycle.

### Put Run lifecycle inside Agent Loop

This would couple the model-and-Tool loop to scheduling, persistence, product ownership, and Run management.

### Give Main and Subagent Runs separate history stores

This would split one execution contract and make Context depend on role-specific persistence. Both use one Run History contract.

### Preserve Waiting Runs across service restarts

Deferred by the first-release operational decision. Shutdown and restart cleanup interrupt all non-terminal Runs uniformly, avoiding special delayed Child-result wakeup during service replacement. Preserving Waiting can be added later through an explicit lifecycle policy change; existing committed data remains retained.

### Use a conventional durable Agent Runtime

That would reintroduce takeover, arbitrary Checkpoints, Leases, and recovery policy excluded by the accepted failure model.

### Let one Running Run retain an execution slot until Waiting or termination

A nonterminating Agent Loop could retain scarce execution capacity across unlimited Model and Tool rounds and starve unrelated Tenants. Cooperative Model-Step and Tool-batch quanta preserve unlimited Run progress while returning every still-runnable Run to the fair in-memory scheduler.

## Acceptance criteria

- Product capabilities initiate Main Runs, Task Tool Executor requests Subagent Runs on behalf of Main Runs, and Agent Runner is the only Run creator.
- External and product inputs enter only Main Runs; Subagent Runs start only through Task Tool.
- Only Main Runs may invoke Task Tool; Subagent Runs are leaf executions and cannot create descendant Runs recursively.
- Delegated Task descriptions are carried by Tool Calls and Child Run Input and do not create another ID, persistent record, execution history, result object, or lifecycle state machine.
- A Subagent Run inherits its parent Main Run's complete resolved authorization and Workspace access without inheriting parent or sibling Run History.
- Main Runs and Subagent Runs use the same Agent Runner, Agent Loop, Status, and History contracts.
- Each Subagent Run uses the same Agent as its Parent Main Run; another Agent is invoked only through an independent A2A Main Run.
- Agent Runner exposes conceptual start, resume, and cancel operations.
- Start is non-blocking and returns a Run reference before execution completes.
- Start is idempotent for one initiator-owned source identity and returns the existing Run reference on duplicate submission.
- Resume atomically records related input for any non-terminal Run; Waiting becomes Running, while Running keeps its status and consumes the input in a later Model Step.
- Related input is idempotent per target Run and owner-issued source identity; duplicates do not append History or schedule execution again.
- Concurrent related inputs retain Agent Runner commit order in Run History without a separate pending-input queue.
- Waiting and Completed cannot commit over already-recorded related input that was absent from their producing Model Step; input committed after Waiting resumes it, while a terminal Run cannot be revived.
- Related-input acceptance does not wait for Model or Tool execution; consumption uses the next safe Model-call boundary and incremental History reads. Repeated wake notifications never create concurrent execution loops for one Run.
- Run Status is limited to Running, Waiting, Completed, Failed, Cancelled, and Interrupted until a real execution consumer requires another state.
- Run has no maximum Model Step count, Token quota, total wall-clock limit, or idle timeout; per-operation technical timeout belongs to Provider, Tool, Sandbox, or external I/O implementation and does not become a hidden Run limit.
- Agent Runner is the only Run Status and Run History writer.
- Run and append-only Run History are the only execution lifecycle authorities; immutable Run Snapshot and replaceable Context Projection add no lifecycle owner.
- Composite foreign keys prevent cross-Tenant and cross-Agent Run relationships; one Snapshot per Run, ordered History identity, closed Status shape, start idempotency, and related-input idempotency are database-enforced.
- Concurrent History writes lock only their target Run aggregate; different Runs proceed independently and repeated source identities do not create duplicate Runs or History facts.
- Start, related input, Waiting, cancellation, and terminal outcome commit Status and their ordered History facts atomically without Run Command, Checkpoint, Runtime Event, generic Relation, Run Output, or execution-to-product reconciliation tables.
- Same-database product outcome recording, Child-to-Parent result delivery, and Parent-to-Child terminal cancellation use the defined atomic handoff; independent A2A uses its own durable idempotent pending-result delivery without replaying execution.
- Child Waiting and its correlated Parent Need Input commit atomically; a terminal Parent cancels the Child instead of leaving an unreported Waiting Run.
- Authoritative Snapshot and History payloads are explicitly versioned, remain losslessly readable across target-version upgrades, and never disappear through unsupported-version fallback or projection invalidation.
- Run Histories are isolated and Context receives an explicit Run identity and authorized source set.
- Task Tool Calls settle immediately with acceptance; later Child outcomes become correlated Child Inputs for Parent Main Runs without a Task object or another lifecycle owner.
- Subagent Need Input is a non-terminal correlated Child event; Main may wait for human input and later resume the exact same Waiting Child through Task Tool.
- Ordinary per-Run Parent terminal settlement, including Completed, cancels active Child Runs without a completion gate, replay, or revival. Service-wide cleanup instead interrupts all non-terminal family members without waking Parents.
- Runner consumes pre-resolved authorization; permission edits neither expand an existing Run nor trigger a revocation cancellation sweep.
- Agent Runner contains no Goal or Task state machine, Durable Coordinator, cross-Worker takeover, arbitrary Checkpoint recovery, Lease, or generic reconciliation protocol.
- The first release has exactly one non-overlapping Agent Runner instance with bounded in-memory admission and execution; startup converts every inherited Running or Waiting Main/Subagent Run to Interrupted before readiness; terminal facts and committed context remain inspectable without automatic resume.
- Admission controls new Run creation, while the ephemeral Tenant-to-Agent-to-Run scheduler controls execution quanta for admitted Running Runs without adding a persisted state, Checkpoint, replay position, or durable queue.
- After every Model Step or bounded Tool batch, a still-runnable Run releases its slot and returns to the tail of the fair scheduler; another Tenant's Run count cannot enlarge a continuously runnable Tenant's scheduler-allocation bound.
- Cancellation and terminal settlement remove scheduler eligibility, graceful shutdown interrupts all remaining Running and Waiting Main/Subagent Runs, and late operation completion cannot re-enter a terminal Run.
- Single Runner is enforced only by first-release deployment configuration and validation; overlapping Runner processes are unsupported and no advisory lock, epoch, or fencing is implemented.
- Admission saturation rejects a new Run before creation, and process loss never causes an accepted Run to be replayed by the restarted Runner.
- Database or enqueue failure releases reserved capacity; a committed Run that cannot enter the live queue becomes Interrupted rather than remaining an orphaned Running Run.
- Agent Runner does not own Session, parent-requirement judgment, Goal continuation policy, Context, Model, Tool, Workspace, or product-delivery facts.

## Risks and open questions

G005 implements and verifies this input handoff: input arriving before a wait decision commits, input arriving after Waiting commits, arrival during Model or Tool execution, repeated wake notifications, and independent Run progress. Exact scheduler coordination and concurrency-test mechanics remain implementation work; this Note records the agreed behavior rather than completed runtime evidence.

Exact column types, bounded payload schemas, parent-child correlation fields, Task Tool settlement, cancellation propagation, streaming subscription, and admission limits remain implementation decisions within the fixed persistence ownership, upgrade contract, and initial single-Runner boundary.

Concurrent Main Runs intentionally observe fixed Session cutoffs supplied by their initiating inputs. Context must preserve those cutoffs and source attribution without merging private Run histories implicitly.
