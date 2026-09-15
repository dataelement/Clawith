# G005 core Runtime implementation contract

Status: user-approved architecture and shutdown decision; independently reviewed implementation contract, not completed G005 behavior.

## Scope and authority

G005 implements Run persistence/lifecycle, the narrow Runner and Agent Loop, Context assembly/projection, Task/Todo execution adapters and the first core Runtime E2E. It builds on [Foundation](backend-foundation.md) and [Execution Dependencies](backend-execution-dependencies.md), retaining their approved ownership unless explicitly amended here. Product Session/A2A/Group/Trigger/Heartbeat/Channel services remain G006. GitHub/ClawHub importing and frontend prototypes remain outside this implementation scope.

The reviewed Audit and G004 amendments continue to supersede the corresponding original Foundation clauses. This contract does not restore coupled Audit writes or earlier capability-selection behavior.

Controlling architecture is [Runner](../.agents/notes/proposed/architecture/2026-08-27-agent-runner-lifecycle-and-history.md), [Context](../.agents/notes/proposed/architecture/2026-08-28-context-source-and-assembly-model.md) and [capacity](../.agents/notes/proposed/architecture/2026-08-28-capacity-performance-and-responsiveness.md). One application, one Base, owner-private persistence and explicit public contracts remain mandatory. No new Task/Goal identity, lifecycle table, checkpoint, lease, event bus, durable scheduler or execution replay is added.

## First-release service interruption amendment

The user confirmed that service-wide shutdown terminates all unfinished work. Every remaining Running or Waiting Main/Subagent Run becomes Interrupted. Already terminal Runs retain their existing outcome. Graceful shutdown rejects new execution intake, stops dispatch, signals and drains bounded operations, and commits interruption facts before resource disposal. If the process dies before committing them, startup performs the same cleanup before readiness.

Interruption is Parent-first and atomic within each affected family. Unlike ordinary per-Run terminal settlement, service-wide cleanup does not cancel Children with Cancelled and does not deliver a Child result that wakes Parent: all non-terminal members receive Interrupted. Committed Snapshot, History and Context source facts remain readable, but old execution is never resumed automatically. This explicitly supersedes the former promise to preserve Waiting execution across restart or upgrade; data preservation is unchanged.

Ordinary operation is unchanged: explicit cancellation becomes Cancelled; unrecoverable execution failure becomes Failed; Main completion and other per-Run terminal outcomes cancel active Children. A Child result or Need Input normally enters the non-terminal Parent and wakes it atomically. Future operational preservation/drain policies require a new scoped decision; no strategy framework is built in advance. Distributed takeover or restoring in-flight execution would require a separate architecture decision.

## Durable Run facts and input consumption

The existing Run, Snapshot and History tables remain the authoritative execution store. Run is the sole lifecycle and History writer. Snapshot is immutable and secret-free, with one canonical content hash and explicit schema version. It preserves private non-Secret Model/Tool bindings for execution while Context selects only a model-visible view.

Initial/related input, Model Step, Tool exchange, Waiting and outcome payloads have closed versioned envelopes with bounded data. Their exact typed fields are defined before the corresponding first write and tested against stored malformed/unsupported forms. Raw authoritative payloads are never silently dropped or defaulted. Only Context projections can be discarded and rebuilt.

Each successful Model Step records its step identity and `read_through_sequence` in Run History. That read boundary is the durable evidence of which inputs influenced the model. Context Projection's `coverage_sequence` is only summary coverage and cannot replace this fact.

Related input appends under the target Run-row lock, using the owner-issued source identity for deduplication. Waiting and Completed decisions use the same lock and check specifically for unseen related-input facts after their producing Model Step's read boundary. Newer Model/Tool History alone is not unseen input. If such input exists, the decision does not settle and the Loop processes it at the next safe model-call boundary.

Start is idempotent and non-blocking with respect to execution. Run, Snapshot and initial input commit together. Duplicate starts consume no new admission capacity; duplicate related inputs append no new History or scheduling action. Terminal Runs cannot be revived. Snapshot, History and status reads are explicitly Tenant/Run scoped and bounded.

## Parent/Child and owner handoff

Child creation, Child resume, Need Input, result delivery and Parent termination use one consistent Parent-before-Child lock order. Main-only Task dispatch is enforced by Runner as well as Tool exposure. A Child uses the same Agent and inherited authorization, with Subagent role eligibility; it does not inherit Parent/sibling History or acquire newly granted access after Parent capture.

Task is a work description, not an object. Its source correlation uses the Parent Run and originating Model-Step/Tool-Call identity. Task acceptance settles immediately; later Child outcomes are related inputs, not completion of a long-blocking Task Tool Call. Missing input preserves a Child in Waiting during normal service operation; Main may answer or ask a human and resume that exact Child. Subagents cannot dispatch further Children.

Same-database owner outcome recording uses a typed `OutcomeConsumer` with the caller's TransactionContext. The consumer writes only its own facts. Failure rolls back settlement; retrying an already-produced outcome never calls Model or Tool again. External delivery remains outside the transaction and outside Runner ownership.

Indexes follow actual operations: source-identity uniqueness, ordered History reads, active Child lookup by Parent and the startup non-terminal sweep. Add only indexes required by implemented queries, not speculative JSON indexes or new coordination tables.

## Admission and fair execution

The first implementation uses one non-overlapping Runner. New-Run admission and ready execution rotation remain separate. Default execution slots are 50 and the initial admission queue is 100, following the frozen reference profile. A configurable technical bound on admitted non-terminal identities defaults to 150; it is not a Run step, Token, duration or idle limit.

New starts reserve capacity before creation. Waiting releases execution slots and heavy Context state but retains lightweight admission accounting, so an existing Run's resume never competes for new admission. Permits survive quantum re-entry and release exactly once after terminal commit, including affected Children. Failed creation releases its reservation; transaction rollback never releases committed capacity early. Startup cleanup leaves no inherited non-terminal execution to re-admit or replay.

The bounded in-memory queue selects Tenant, Agent and Run round-robin. One quantum is one Model Step or one bounded Tool batch. Eligible Running work re-enters at the tail after its quantum; duplicate wake hints never start concurrent loops for the same Run. Queue overflow on an already admitted re-entry is an implementation invariant failure, not ordinary new-Run overload policy.

Control-plane intake/status/cancellation uses isolated capacity. Per-operation Provider, Tool and storage bounds do not become global Run limits. Scheduler and admission state are process-local bookkeeping, not authoritative persisted progress.

## Context and Loop integration

Context consumes fixed, source-labelled Platform Instructions, Agent/Soul, product/delegated input, authorized Workspace indexes, Tool exposure and Model Context Profile. It reads only the selected Run's History after its current view position. Child Snapshot derivation preserves the authorization captured by Parent, while applying leaf-only role eligibility without live grant expansion.

Stable prefix content is deterministic and does not refresh on every Model Step. Files and full Skill/Memory contents enter only through explicit references or Tool results. Optional current time belongs to the volatile tail at minute precision, never a universal stable-prefix timestamp.

Compaction preserves complete Tool Call/Result units and source facts. It first clears dispensable high-volume Tool output in the model view, then may create a derived structured summary with a recent complete tail. Projection deletion or version invalidation never changes durable input consumption. Todo is a Run-scoped planning view derived from its Tool facts and restored after compaction, not a completion gate or additional workflow.

Model receives logical messages and owns Provider encoding and exact opaque continuation. Runner records the successful Model result only after Model's required continuation commit. Terminal cleanup uses Model's public port and cannot reverse a terminal outcome. Run-scoped Tool adapters receive explicit services and scope, not a generic service locator.

## Verification sequence

1. Keep the pure ready-queue slice independently testable; it is not integrated fairness evidence.
2. Implement typed payloads and Run repository transitions with real PostgreSQL: identity deduplication, sequence races, unknown versions, immutable Snapshot and Parent-first atomicity.
3. Implement Context view/projection and Model-Step read boundaries; exercise concurrent input before/after model decisions and projection rebuild.
4. Wire Runner/Loop, Task/Todo, owner outcomes, startup/shutdown and application-owned resource draining. Verify cancellation and post-commit enqueue failure without replay.
5. Run the declared G005 real-entry fixture and hostile scheduler test, then the exact core load profile. Report fixture/unit, core E2E, hosted services, product E2E and load separately.

The existing `goal-gates.json` remains the cumulative verification contract. This artifact does not declare G005 complete. Exact Python symbols, bounded payload sizes and query-supported indexes may be refined within these semantics with owning tests; a change to lifecycle or input acceptance requires explicit review rather than changed test expectations alone.
