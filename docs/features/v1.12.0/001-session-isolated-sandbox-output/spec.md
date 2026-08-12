# Session-Isolated Sandbox Output Specification

## 1. Status

- Feature: Session-isolated local code execution output
- Track: Full SDD
- Target release: v1.12.0
- Status: Draft for user confirmation
- Constitution: [`docs/constitution.md`](../../../constitution.md)

## 2. Problem Statement

Clawith Runtime Workers already claim durable Agent commands from PostgreSQL and execute Tool steps in the claiming process. Local `execute_code` creates a fresh sandbox backend and a fresh bubblewrap process for each invocation.

Because no bubblewrap process survives between calls, routing later commands for the same Session back to the same Runtime Worker does not preserve useful Sandbox state. The state that must survive is file content, and that state belongs in the durable Agent Workspace.

The requested behavior is therefore:

1. any eligible Runtime Worker may execute a Session command through the existing Command Inbox;
2. concurrent `execute_code` calls for the same Session are prevented with a Redis execution lease;
3. every local invocation starts a fresh bubblewrap process;
4. `isolated_output` exposes the Workspace as read-only except for a fixed Session output directory;
5. output files are conditionally published to durable Workspace storage and can be rematerialized by any later Worker;
6. no new Runtime Worker affinity or owner-specific queue is introduced.

## 3. Goals

### G1. Preserve the existing Runtime Worker model

Runtime Workers MUST continue claiming commands through the existing PostgreSQL Command Inbox. The feature MUST NOT introduce Session-to-Worker routing, Worker-specific command queues, or a second command scheduler.

### G2. Redis execution ownership

Redis MUST store a short-lived, tenant-scoped execution lease for an exact `(tenant_id, agent_id, session_id)` while local `execute_code` is active. The lease prevents overlapping code executions for one Session; it does not own the Agent Run or Session lifecycle.

### G3. Fresh bubblewrap per invocation

Every local `execute_code` invocation MUST start a fresh bubblewrap process. The platform does not promise preservation of interpreter memory, background processes, environment mutations, `/tmp`, or namespaces between calls.

### G4. Fixed writable Session output

In `isolated_output` mode, code MUST see this logical permission boundary. The
existing Sandbox mount keeps the Agent root at `/workspace`, so the durable
Workspace subtree remains `/workspace/workspace`:

```text
/workspace/                                      read-only
/workspace/workspace/output/{session_id}/         read-write
```

Files in the fixed directory MUST be conditionally published to the matching Agent Workspace path and MUST be available to later executions regardless of which Runtime Worker claims them.

### G5. Preserve current merge behavior

The existing temporary Workspace materialization, conditional writes, conflict detection, and per-invocation settlement remain the basis of `merge` mode.

### G6. Durable facts stay durable

PostgreSQL Command Inbox, LangGraph checkpoints, `AgentToolExecution` receipts, and Workspace storage remain authoritative. Redis loss MUST NOT erase accepted commands, execution outcomes, or files.

## 4. Non-Goals

This version does not introduce:

- Runtime Worker affinity;
- Worker registration or heartbeat for Sandbox routing;
- Worker-specific Redis queues;
- a persistent bubblewrap daemon or long-lived namespace;
- preservation or migration of in-memory interpreter state;
- a standalone Sandbox Control Plane;
- a general Tool Worker architecture;
- migration of Command, checkpoint, or Tool receipt facts into Redis;
- automatic migration of local temporary files between Workers;
- strict multi-file atomic publication beyond the existing conditional-write contract;
- automatic seven-day physical deletion unless a suitable durable retention owner already exists.

## 5. Identity and Terminology

| Term | Meaning |
|---|---|
| Runtime Worker | Existing process that claims durable commands and drives LangGraph model/tool execution. |
| Execution scope | Exact tuple `(tenant_id, agent_id, session_id)`. |
| Execution lease | Expiring Redis mutex that authorizes one local `execute_code` invocation for the scope. |
| Lease token | Unguessable value used for compare-and-renew and compare-and-delete. |
| Sandbox invocation | One fresh backend execution and bubblewrap child process. |
| Session output | Durable Workspace path `output/{session_id}`. |

The execution scope MUST be derived from trusted Runtime context. Model or client input MUST NOT select Redis keys, lease tokens, or arbitrary output prefixes.

## 6. Functional Requirements

### FR1. Runtime command execution

1. Runtime Workers MUST continue using the existing database claim algorithm, Thread lock, scheduling lane, checkpoint driver, and Tool ledger.
2. Commands for the same Session MAY be claimed by different Runtime Worker processes at different times.
3. No Session ownership record is needed outside an active local code invocation.
4. Runs with no exact Session identity remain unchanged.

### FR2. Execution lease acquisition

1. Before starting local `execute_code` for an exact Session, the executing Runtime Worker MUST atomically acquire a Redis lease.
2. The Redis key MUST be explicitly tenant-scoped as required by Constitution C2.
3. The value MUST include an unguessable `lease_token` and process-unique executor identity for diagnostics.
4. Acquisition MUST not replace an unexpired lease.
5. Lease renewal MUST compare the current token before extending expiry.
6. Lease release MUST compare the current token before deletion.
7. The lease expiry MUST exceed the code execution deadline plus bounded publication cleanup, or the lease MUST be safely renewed while work remains active.
8. Failure to acquire the lease MUST return a stable retryable busy outcome without starting code.

### FR3. Lease loss and Redis outage

1. If Redis ownership cannot be established before execution, code MUST not start.
2. If lease renewal becomes unverifiable during execution, no unguarded Workspace publication may occur.
3. If code may have run but publication safety cannot be proven, the existing Tool receipt MUST settle as `unknown`, not automatically retry the side effect.
4. Redis recovery permits later invocations after the lease expires; no durable execution fact is reconstructed from Redis.
5. Non-code Tools and non-Session Runs do not acquire this lease and retain their existing behavior.

### FR4. Executor Code Workspace modes

Executor Code configuration MUST expose:

```text
workspace_mode = merge | isolated_output
```

#### `merge`

1. Preserve current temporary Workspace materialization.
2. The local Sandbox may modify the materialized copy according to the existing Sandbox contract.
3. At invocation settlement, calculate and conditionally write changed files back to Workspace storage.
4. Existing version/hash checks remain authoritative.
5. Publication completes before the Tool step is reported as successfully settled.

#### `isolated_output`

1. An exact canonical `session_id` is mandatory.
2. Materialize the Workspace so code can read the current durable contents.
3. Only `/workspace/workspace/output/{session_id}` is writable inside bubblewrap.
4. The output directory MUST be included in materialization for every later invocation in that Session.
5. At invocation settlement, collect changes only under `output/{session_id}`.
6. Conditionally write those changes to the corresponding durable Agent Workspace path.
7. Never publish modifications outside the Session output prefix.
8. A later invocation on any Runtime Worker can rematerialize, read, edit, and republish those files.
9. Missing or invalid Session identity MUST fail closed rather than use a shared Agent output directory.

### FR5. Bubblewrap mount contract

In `isolated_output` mode, the local backend MUST enforce the equivalent of:

```text
ro-bind <materialized-agent-root> /workspace
bind    <session-output-staging> /workspace/workspace/output/{session_id}
```

The implementation MAY use a safe equivalent mount topology, but tests MUST demonstrate that writes outside the fixed prefix fail and writes inside it succeed.

The virtual environment and platform runtime paths remain governed by the existing Sandbox backend contract and are not Session output.

### FR6. Publication semantics

1. Workspace publication MUST happen during each `execute_code` Tool settlement, before releasing the execution lease.
2. WebSocket disconnect, Agent Run completion, Session idle, and bubblewrap process exit MUST NOT independently trigger a second implicit publication.
3. Successful and failed code invocations MAY both leave files under `isolated_output`.
4. Code status and publication status MUST remain distinguishable.
5. If code exits non-zero but output publication succeeds, the Tool remains a code-execution failure while reporting any published artifact references.
6. If publication conflicts or becomes unprovable after code ran, use existing conflict/unknown Tool semantics and do not silently overwrite.
7. Temporary execution scripts, `.tmp`, pip proxy files, virtual environments, caches, and platform internals MUST not be published.

### FR7. Execution serialization boundary

1. The execution lease serializes `execute_code` for one exact Session across Runtime Worker processes.
2. Different Sessions for the same Agent use different lease keys and MAY execute concurrently.
3. `merge` and `isolated_output` invocations for the same Session share the same execution lease to prevent mixed-mode overlap.
4. Existing Workspace path locks and conditional writes remain required; the execution lease does not replace them.

### FR8. Output retention

1. The intended default retention policy for `output/{session_id}` is seven days after the latest qualifying output activity.
2. Retention metadata, when implemented, MUST have a durable owner and MUST not exist only in Redis.
3. Cleanup MUST not delete files while the Session execution lease is held.
4. If current Workspace storage has no suitable durable retention record, V1 MUST leave physical deletion disabled rather than implement an unsafe Worker-local timer.
5. Adding a durable retention model and cleanup daemon requires an explicit design decision and migration in `design.md`/`tasks.md`.

### FR9. Rollout and compatibility

1. `workspace_mode` MUST default so existing Agents preserve their current `merge` behavior.
2. Existing active LangGraph checkpoints require no rewrite.
3. Existing `AgentToolExecution` rows and durable Tool recovery leases remain compatible.
4. Remote Sandbox backends MUST not be forced into a local bubblewrap mount contract they cannot enforce.
5. The feature MUST explicitly define which Sandbox backend types support `isolated_output`; unsupported combinations fail configuration validation rather than silently behaving as `merge`.

## 7. Failure and Recovery Requirements

| Failure | Required behavior |
|---|---|
| Runtime Worker exits before code starts | Durable Tool/Command recovery applies; execution lease eventually expires. |
| Runtime Worker exits while code runs | Process-local bwrap dies with its parent where supported; lease expires; durable Tool receipt governs recovery. |
| Redis unavailable before acquisition | Code does not start. |
| Redis unavailable after code starts | Publication is blocked unless lease ownership can be safely revalidated; uncertain outcome uses `unknown`. |
| Duplicate Tool attempt | Existing `AgentToolExecution` reservation prevents unsafe duplicate execution; Redis lease is only an additional concurrency guard. |
| Another Worker attempts same Session | It cannot start code while the first execution lease is valid. |
| Workspace file changes after materialization | Conditional write reports conflict/unknown; newer durable file is not silently overwritten. |
| Later invocation lands on another Worker | It rematerializes Session output from durable Workspace and does not need prior Worker state. |

## 8. Security and Tenant Isolation

1. Redis lease keys MUST begin with or contain explicit `tenant:{tenant_id}:` scope.
2. `agent_id` and `session_id` MUST be derived from trusted Runtime context and validated before key/path construction.
3. Output paths MUST be normalized and proven beneath both the Agent Workspace root and exact `output/{session_id}` prefix.
4. Session IDs used as path components MUST be canonical identifiers without path separators or traversal.
5. Symlinks and bind-mount targets MUST not escape the materialized Workspace or Session output staging root.
6. Redis values MUST not contain source code, Workspace contents, credentials, model messages, or Tool results.
7. Logs MUST not expose lease tokens; a non-reversible short hash MAY be used for diagnostics.

## 9. Observability Requirements

Structured logs and metrics MUST expose:

- execution lease acquisition, contention, renewal failure, expiry, and release;
- executor process identity and Session scope without leaking secret tokens;
- selected Workspace mode and Sandbox backend;
- bubblewrap invocation start/finish and timeout;
- Session output publication updated/deleted/conflicted/skipped counts;
- publication success, failure, and unknown outcomes;
- rejected unsupported `workspace_mode`/backend combinations.

## 10. Acceptance Criteria

### AC1. Runtime Worker affinity is absent

Given two eligible Runtime Workers, consecutive commands for one Session may be claimed by different Workers through the existing database algorithm; no Worker-specific queue or Session owner lease is created.

### AC2. Same-Session code serialization

Given two concurrent `execute_code` attempts for the same tenant, Agent, and Session, only the Redis execution-lease holder starts code. The other returns a stable retryable busy outcome.

### AC3. Different Sessions execute independently

Given two Sessions belonging to one Agent, they use different tenant-scoped lease keys and may run concurrently.

### AC4. Owner-only renewal and release

Given a stale or foreign lease token, it cannot renew or delete the active execution lease.

### AC5. Redis outage fails closed for Session code

Given Redis is unavailable before execution, local Session `execute_code` does not start and durable Runtime state remains recoverable.

### AC6. Fresh bwrap invocation

Given two calls in one Session, each starts a distinct bubblewrap child process. State continuity is promised only for files published to durable Workspace.

### AC7. Isolated output permissions

Given `workspace_mode=isolated_output`, code can read materialized Workspace files, can create and modify files beneath `/workspace/workspace/output/{session_id}`, and cannot write elsewhere.

### AC8. Output survives Worker change

Given Worker A publishes `output/{session_id}/report.csv`, a later invocation on Worker B rematerializes, reads, updates, and republishes that file.

### AC9. No cross-Session output writes

Given Session A, code cannot publish into Session B's output directory or a shared `output` root.

### AC10. Merge regression

Given `workspace_mode=merge`, existing materialization, sync-back, version conflict, and Tool outcome behavior remains unchanged apart from the execution lease around local Session code.

### AC11. Failed execution can publish isolated artifacts

Given code writes a diagnostic file under its Session output and then exits non-zero, the diagnostic file may be conditionally published and referenced while the Tool result remains failed.

### AC12. Conflict does not overwrite

Given a durable output file changes after materialization, sync-back does not silently overwrite it and settles through the existing conflict/unknown contract.

### AC13. Unsupported backend fails explicitly

Given a backend that cannot enforce `isolated_output`, configuration or execution returns a stable unsupported-mode error and does not silently grant broader writes.

### AC14. Checkpoint compatibility

Given a pre-feature active checkpoint, it resumes without checkpoint schema rewriting because the execution lease and Workspace mode are outside mutable LangGraph lifecycle state.

## 11. Required Verification Scope

Implementation verification MUST include:

- Redis lease acquire, contention, renew, expiry, and owner-only release tests;
- two logical Runtime Worker identities contending for one Session execution;
- Redis unavailable fail-closed tests;
- fresh bubblewrap process tests;
- `isolated_output` read/write mount-boundary tests;
- path traversal, symlink, and cross-Session isolation tests;
- materialize/publish/rematerialize tests across different logical Workers;
- failed execution with successfully published diagnostic output;
- Workspace conflict and unknown-outcome tests;
- current `merge` regression tests;
- existing Agent Runtime Tool ledger and command recovery tests;
- `scripts/arch-guard.sh`.

## 12. Open Design Decisions

The following are deferred to `design.md`:

1. Exact Redis key/value layout and Lua scripts.
2. Lease TTL and renewal interval relative to configured code timeout.
3. How `workspace_mode` is added to the existing Executor Code configuration schema.
4. The smallest safe change to current temporary Workspace materialization and bubblewrap mount construction.
5. Exact typed busy/unsupported/publication error codes.
6. Whether V1 adds durable seven-day retention metadata or explicitly defers physical cleanup.
