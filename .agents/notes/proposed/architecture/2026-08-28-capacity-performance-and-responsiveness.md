# Agent Note: Capacity, Performance, and Responsiveness

Status: proposed — the 50-Agent capacity floor, control-plane isolation, Frontend responsiveness, and measurable performance contract are agreed but not implemented

## Problem

The target architecture must remain responsive when 50 Agents are simultaneously active across direct conversation, Group work, Subagent execution, Heartbeat, Trigger, and A2A. Model, Tool, file, code, browser, and Compaction work must not block Session intake, state queries, Streaming, or Frontend interaction.

“No lag” must be measurable. External Model Provider completion latency cannot be controlled by Clawith, but platform admission, Context assembly, Provider dispatch, Delta forwarding, state projection, API latency, and Frontend rendering must remain bounded.

## Proposal

### Capacity floor

The initial capacity target is 50 simultaneously active Agent executions, counting Main Runs and Subagent Runs. The reference load includes up to 50 concurrent Model requests or Streaming connections and a mixture of direct Session, Group, Subagent, Heartbeat, Trigger, and A2A work.

Waiting Runs do not retain execution slots. Work above the configured execution capacity waits in the in-memory execution scheduler, but scheduler pressure must not block control-plane APIs, new input acceptance, active Streaming, cancellation, or Frontend reads.

The first release reaches this target with exactly one Agent Runner instance, a bounded in-memory admission queue, a bounded asynchronous execution-slot pool, and the in-memory fair execution scheduler defined by [Agent Runner Lifecycle and Run History](2026-08-27-agent-runner-lifecycle-and-history.md). It does not use one process per Agent and does not add distributed Worker ownership. Admission capacity is reserved before Run creation; full admission rejects the new Run while leaving the initiating product input intact for an idempotent retry. An admitted Running Run instead yields its slot after each Model Step or bounded Tool batch and re-enters the execution scheduler without another admission reservation. Model and asynchronous Tool waits do not hold database connections or locks, and blocking or CPU-heavy work executes outside request and Runner event loops.

Workspace concurrency uses short resource-scoped commit locks only. Agent-Agent semantic conflict resolution happens outside the lock through Agent execution and bounded retry, so model latency never serializes unrelated Workspace access.

### Control and execution isolation

Control-plane work and execution-plane work use isolated concurrency and resource budgets.

```text
Control Plane
  - authentication and Tenant/RBAC
  - Frontend and query APIs
  - Session Input acceptance
  - Run registration, status, and cancellation
  - WebSocket and Streaming routing
  - Workspace metadata and lightweight reads

Execution Plane
  - Model requests
  - Tool execution
  - Subagent Runs
  - code, browser, and compute-heavy work
  - Context Compaction
  - background product Runs
```

Execution saturation must not consume control-plane event-loop, database, HTTP, worker, or connection-pool capacity. CPU- or memory-heavy Tools use bounded execution venues and cannot run inline on request or Streaming loops.

### Backend and Runtime responsiveness

On the agreed reference environment and load scenario, the initial p95 platform targets are:

| Surface | p95 target |
|---|---:|
| Non-Model query and mutation APIs | 500 ms |
| Session Input acceptance and Run reference | 300 ms |
| Hot Context assembly | 200 ms |
| Cold Context assembly | 500 ms |
| Workspace metadata, list, search, and bounded read | 500 ms |
| Provider Delta received to platform Stream event | 100 ms |

Platform-originated error rate remains below 1% during the capacity test, and accepted durable events and Stream events are not silently dropped.

Provider time to first token and completion is measured separately. Clawith must dispatch promptly, forward visible Delta promptly, contain Provider rate limits or failure to affected Runs, and keep unrelated Agents responsive.

### Frontend responsiveness

Frontend is part of the capacity contract, not a separate polish phase. With 50 active Agents and concurrent Streaming updates:

- route changes, navigation, input, cancellation, and primary controls acknowledge user interaction within 100 ms;
- ordinary data-backed views become usable within 1 second p95 after the application shell is loaded;
- the authenticated application shell becomes usable within 2 seconds p95 on the defined reference browser, network, and hardware profile;
- Backend Stream events update visible state within 100 ms p95 after browser receipt;
- one busy Agent, large transcript, Tool output, or Workspace listing does not cause unrelated pages or conversations to rerender or stall;
- lists and histories are bounded, paginated, windowed, or virtualized where necessary;
- Frontend subscriptions have one owner for ordering, deduplication, reconnect, cancellation, and cleanup;
- Streaming updates feed the same authoritative client data owner used by ordinary reads rather than a second unbounded state tree;
- expensive parsing, formatting, diffing, and binary preview work does not block the browser main thread.

The Frontend test must observe interaction and rendering behavior in a real browser. A successful bundle build or source-level reducer test is not responsiveness evidence.

### Context and Model efficiency

Context follows the immutable Snapshot, Compaction Base, and incremental Delta contract in [Context Source and Assembly Model](2026-08-28-context-source-and-assembly-model.md). Stable logical segments preserve Provider cache opportunities. Context does not rescan complete Workspace, Session, Tool Registry, Memory, Skills, or old Run History before every Model Step.

Model System records request, cache-read, cache-write, uncached, reasoning, and output usage when the Provider exposes them. Reconstructible prompt, KV, stateful conversation, and opaque Compaction caches remain optimizations rather than source-of-truth state. Provider metadata required for the next request follows the Model System continuation contract and is persisted separately before Model Step settlement.

### Bounded work and backpressure

Database queries, Workspace operations, Tool batches, Model requests, Streams, and product projections define cardinality, byte, token, time, and concurrency bounds. The architecture has no unbounded `gather`, `Promise.all`, result materialization, history load, file-tree scan, or subscriber fan-out. These operation bounds protect shared resources; they do not impose a maximum Model Step count, Token quota, whole-Run duration, or idle timeout.

Admission fairness governs new Runs competing for bounded pre-creation capacity. It does not prove continued progress after admission. Continued progress comes from the separate Tenant-to-Agent-to-Run execution scheduler: one Model Step or bounded Tool batch consumes one execution quantum, every still-runnable Run yields after that quantum, and each scheduler level uses round-robin turns. Group, Goal, Heartbeat, Trigger, A2A, and other initiator kinds add no priority lane; their Runs use the same hierarchy. Tool-class contention remains bounded by its owning Tool venue rather than becoming another Run scheduler.

For `T` continuously runnable Tenants, each Tenant receives an execution-slot allocation within at most `T` scheduler allocations, excluding quanta already allocated before it became runnable. The same bound applies recursively as `A` selected-Tenant turns for one of `A` runnable Agents and `R` selected-Agent turns for one of `R` runnable Runs. Cancellation removes queued eligibility, signals an in-flight bounded operation, and prevents terminal work from re-entering; Waiting and terminal settlement release the slot without losing the durable Run relation.

### Observability and load evidence

Performance claims require segmented evidence:

```text
API and browser interaction latency
Run admission and queue wait
execution scheduler ready, dispatch, quantum, yield, and cancellation order by Tenant, Agent, and Run
Context source reads and assembly duration
database pool wait and query latency
Provider dispatch and first visible Delta
Stream forwarding and browser render latency
Tool queue, execution, and cancellation
input, output, cache-read, and cache-write tokens
Compaction count, duration, cleared tokens, and coverage
Frontend render count, long tasks, memory, and subscription backlog
event loss, reconnect, error, and cleanup counts
```

The baseline mixed load scenario is:

```text
20 Direct Session Runs
10 Group Runs
10 Subagent Runs
5 Heartbeat or Trigger Runs
5 A2A Runs
= 50 active Agent executions
```

The test runs long enough to exercise Waiting, resume, cancellation, Streaming reconnect, Workspace reads and writes, Tool Results, and Context growth. Optimization decisions use measured bottlenecks rather than source repetition alone.

The fairness qualification isolates execution scheduling from admission. Fifty already admitted Tenant-A Runs remain non-terminating by producing another ready quantum after every controlled Model Step or Tool batch. One already admitted Run for one Agent in Tenant B then becomes ready for its next Model Step while both Tenants remain runnable. The observer records scheduler allocation sequence rather than waiting for Run completion. Tenant B's quantum must start no later than the second slot allocation after its ready event; allocations already committed or in flight before that event are excluded. The assertion fails if Tenant A receives two post-ready allocations before Tenant B, regardless of which of its 50 Runs receives them.

### Frozen Backend reference profile

Phase 0 freezes one comparable Backend qualification profile: 8 vCPU, 16 GiB RAM, local-container PostgreSQL, Redis, and object storage, a 180-second warm-up, a 900-second measurement window, deterministic Provider latency of 100 ms to first Delta and 500 ms to completion, ordinary I/O Tool latency of 50 ms, slow Tool latency of 2 seconds, Run pool 50 (50 execution slots), admission queue 100, isolated control and execution database pools of 20 connections each, I/O Tool concurrency 32, and CPU-heavy Tool concurrency 4. The mixed workload remains the 20/10/10/5/5 distribution above.

The synthetic fixtures use these exact payload sizes so repeated load results are comparable:

| Fixture surface | Bytes |
|---|---:|
| Session Input | 4,096 |
| Hot Context | 32,768 |
| Cold Context | 262,144 |
| Provider Delta | 1,024 |
| Provider completion | 16,384 |
| Ordinary Tool Result | 16,384 |
| Slow Tool Result | 65,536 |
| Workspace operation | 65,536 |

These payload sizes describe benchmark fixtures only. They do not define product payload, Context, Tool Result, Workspace, transport, or storage limits. Runtime configuration may be tuned with recorded evidence while preserving the capacity, isolation, fairness, error, event-loss, and latency contract; a tuned implementation value does not silently change the frozen `backend_50` reference profile or make results from a different profile comparable.

## Alternatives considered

### Treat Provider completion time as total platform performance

Provider latency can obscure platform admission, Context, queue, forwarding, and rendering regressions. Provider and platform segments remain separate.

### Share one unbounded worker and connection pool

Execution spikes would starve API, Streaming, and cancellation paths. Control and execution capacity remain isolated and bounded.

### Validate only Backend throughput

Users experience browser input, rendering, data loading, and Stream updates. Frontend responsiveness is a first-class acceptance surface.

### Run every heavy Tool immediately

Fifty CPU-heavy operations cannot all consume one node without affecting control responsiveness. Heavy Tools queue in bounded execution venues while the control plane stays responsive.

### Let an admitted Run hold its slot across the complete Agent Loop

A Run with unlimited Model and Tool rounds could monopolize execution capacity without violating any Run limit. Cooperative quanta and hierarchical fair re-entry bound dispatch opportunities without adding a maximum Model Step count, Token quota, whole-Run timeout, or idle timeout.

### Optimize before instrumentation

Caching and concurrency can move or hide latency while introducing stale state and resource pressure. The architecture requires segmented telemetry and reproducible load evidence first.

## Acceptance criteria

- The platform supports at least 50 simultaneously active Main and Subagent executions under the mixed load scenario.
- Control-plane APIs, Streaming, cancellation, and Frontend reads remain responsive under execution saturation.
- Waiting Runs release execution resources.
- Backend, Context, Workspace, Stream-forwarding, and Frontend p95 targets are measured on a declared reference environment.
- Frontend interaction, data usability, and Stream rendering meet their targets during the same 50-Agent load.
- Provider latency and platform-added latency are reported separately.
- Heavy Tools use bounded execution venues and cannot block request, Streaming, or browser event loops.
- Queries, histories, file operations, Tool batches, and fan-out are bounded.
- Admission fairness for new Runs and execution fairness for admitted Running Runs are separate measured surfaces.
- Every still-runnable Run yields after one Model Step or bounded Tool batch and re-enters the in-memory Tenant-to-Agent-to-Run scheduler without another admission reservation or persisted scheduling state.
- With 50 non-terminating Tenant-A Runs and one ready Tenant-B Run, Tenant B starts its next quantum by the second post-ready scheduler allocation; Run completion is not used as fairness evidence.
- One non-overlapping Agent Runner instance sustains the initial 50-execution load with bounded in-memory admission and execution, while its startup interruption sweep completes before readiness.
- Workspace locks cover only revision validation and atomic commit; Agent merge and retry happen outside the lock and remain bounded.
- Cancellation removes scheduler eligibility, signals bounded in-flight work, and prevents a terminal Run from re-entering; Runner shutdown interrupts rather than replays all remaining Running and Waiting Main/Subagent Runs.
- No accepted durable or Stream event is silently lost.
- Performance optimization is supported by segmented Backend, Runtime, Provider, Workspace, and Frontend evidence.

## Risks and open questions

The reference browser and network profile, Frontend qualification environment, live Provider quotas, and p99 targets remain unresolved. The Phase 0 Backend hardware, local-container services, duration, deterministic Provider and Tool behavior, fixture payloads, pool sizes, queue limits, and concurrency budgets are frozen qualification inputs rather than production sizing promises. The first-release single-Runner boundary, 50-Agent floor, and control/Frontend responsiveness are fixed requirements. Deployment validation must prove one non-overlapping Runner process; the first release intentionally has no runtime singleton lock or fencing and treats overlap as unsupported.
