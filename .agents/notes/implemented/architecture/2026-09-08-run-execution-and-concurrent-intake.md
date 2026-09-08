# Agent Note: Run execution and concurrent intake

Status: implemented — the native Run owner and application execution path are present; formal platform performance qualification remains separate.

## Problem

Lifecycle writes, model-visible observations and process-local dispatch must agree about committed work without introducing a Task state machine or replaying uncertain external operations. A global admission lock around database I/O also makes unrelated starts wait for each other: the 50-request baseline attributed approximately 98% of startup time to this lock.

## Decision

`RunService` owns short caller-transaction operations. Run, immutable Snapshot and initial History commit together. Existing source identity returns its existing Run. Parent-before-Child row locks serialize Child creation, related input, Waiting, completion and family termination; they do not serialize unrelated Runs. A successful Model Step's recorded read boundary determines whether related inputs remain unseen. Main termination cancels unfinished Children; only Main's business consumer receives its outcome, while normal Child results enter Parent History atomically.

Ordinary new Main startup uses one transaction and four SQL statements: source lookup, Run insertion with RETURNING, and the initial Snapshot/History inserts. Only the insertion winner enters private initialization. The initial cursor is one, and the returned Run row supplies the response without a second read or update. A synchronous admission callback runs only after deduplication and before insertion; Runtime tracks whether this call actually reserved capacity before reconciling failures. Child startup and general existing-record operations retain their locks and validation. Snapshot preparation shares the existing pure validator rather than using a skip-check flag.

`RunRuntime` owns bounded process-local admission, per-Run caches, pending produced results and the single execution loop. Different source identities start concurrently with independent database sessions. Same-source callers share an in-progress result identified by Tenant and source identity, with Agent and Parent scope checks. The in-progress map is bounded and removed on settlement; it is neither a durable queue nor a business object. A duplicate waiter cannot cancel the leader. Database uniqueness remains the final authority for idempotency.

Capacity is reserved before creation and released only after confirmed creation rollback or terminal commit. Creation errors that can occur after database commit require readback and, for this request's committed Run, interruption before release. Shutdown rejects new intake and stops dispatch before draining registered creation and execution operations, then interrupts all committed unfinished Runs. No execution resumes automatically after restart.

Each quantum performs one Model operation or one bounded Tool batch. Summary generation uses its own quantum under the same execution capacity. Model input and adopted Context bases are recorded before their corresponding request. Only valid, persistable results enter pending settlement; database or business-consumer retries repeat persistence, not Model or Tool execution. Truncated, filtered and protocol-invalid results cannot become normal successful completion. Run-scoped presentation callbacks are bounded and isolated from authoritative results.

Waiting releases heavy in-memory Context while retaining admission. Projection reuse requires the hash recorded in ModelInput v2 plus matching coverage/cursor relationships. ModelInput v1 remains readable and reconstructs from History without trusting an unbound cache. Missing or mismatching projections rebuild from the exact observed base and History tail. Actual terminal operations drain before Model continuation cleanup, preventing late writes from recreating state after cleanup.

The public import DAG is Model → Context → Run. Run supplies sourced values to Context, not the other way around; Run History remains authoritative. Context's foreign key to Run belongs to schema ordering, not a reverse public-service dependency.

## Alternatives considered

A global asynchronous lock still serializes unrelated requests if held across database awaits. A distributed lock adds another coordination dependency without solving that granularity problem in the approved single-Runner deployment. Returning success before commit weakens durability. Trusting a structurally valid projection without a History-bound hash permits invented model input. Allocating Task identities, persisted schedulers or recovery leases duplicates responsibilities excluded from the first release.

The concurrency scope follows patterns inspected in [Codex thread registration](https://github.com/openai/codex/blob/main/codex-rs/core/src/thread_manager.rs), [OpenCode session runners](https://github.com/anomalyco/opencode/blob/dev/packages/opencode/src/session/run-state.ts), [Prefect database idempotency](https://github.com/PrefectHQ/prefect/blob/main/src/prefect/server/models/flow_runs.py) and [singleflight](https://github.com/golang/sync/blob/master/singleflight/singleflight.go). These references justify scoped coordination, not a latency guarantee or copying their business models.

## Consequences

The first release still requires one non-overlapping Runner. Production source modules supply preauthorized inputs; G006 product APIs are not implied by the application fixture. Run startup acknowledgment, dispatch latency and Provider first output are distinct measurements. Snapshot validation, database atomicity and persisted-read validation remain intact during optimization.

## Verification

Real PostgreSQL tests cover source races, scope isolation, rollback, Child waits/resume, unseen-input completion guards, late cancellation, post-commit scheduling failure, retained-result retries, malformed result rejection and stop-with-pending-creation. The application fixture exercises actual Runtime, Model HTTP adapters and Workspace Tools with a controlled Provider and transactional product-owner output. Independent code and architecture review cover the corresponding ownership and concurrency boundaries.

Earlier global-lock removal measurements are retained in `backend/artifacts/performance/start-latency-comparison.json`. These are controlled local startup measurements, not full-platform qualification. The earlier 18-minute load remains diagnostic: it used a different source snapshot, a smaller Docker memory envelope and incomplete product workloads.

A subsequent same-fixture transaction-consolidation comparison measured 532.95/350.50/302.20 ms before and 737.85/177.18/176.79 ms after. The warm rounds improved while the cold-connection round worsened. Each fresh Main now uses four SQL statements, one transaction/commit and one connection checkout; duplicates still coalesce to one lookup. Independent Engine, lifecycle, Snapshot and dispatcher checks passed 139 tests for this slice. These startup results do not close the separate cumulative G005 review findings.
