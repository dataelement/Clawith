# Run owner

Transient Model failures follow [bounded same-model retries](../../../../.agents/notes/implemented/architecture/2026-09-08-bounded-model-failure-retries.md): three attempts including the initial call, then Failed. Never retry Tool side effects or reinterpret Provider exhaustion as a new Waiting protocol.

Run uniquely owns execution identity, lifecycle, immutable startup Snapshot and append-only History. Cross-owner consumers use the typed `public.py` facade; `lifecycle.py`, `engine.py`, models, snapshots and repositories remain private. `contracts.py` owns versioned History encoding.

History preserves initial/related input, complete normalized Model Steps, Tool Results, Waiting and outcomes. Model-Step `read_through_sequence` is a durable input-consumption fact; Context summary coverage is not. Unknown authoritative kinds, versions, extra fields and malformed payloads fail explicitly without dropping data or exposing input values in diagnostics.

`RunHistoryRepository` uses the caller's TransactionContext, serializes appends on the Tenant-scoped Run row and never commits independently. Source identity deduplicates an append without replacing its original content. Reads freeze a sequence cutoff and bound both row count and uncompressed payload bytes before loading JSON. The related-input predicate is not a lifecycle transition: its caller must hold the appropriate Run locks when deciding Waiting or completion.

The [Core Runtime contract](../../../../specs/backend-core-runtime.md) owns lifecycle rules. `RunService` uses caller-owned transactions for atomic start, related input, Waiting and termination. Parent-first locks protect Child creation, replies, result notification and family termination. Main termination cancels unfinished Children without writing their results to Main's product consumer. `RunRuntime` applies scheduling and publication only after commit; its single Loop serves both roles. Task is a work description and Todo is derived from Tool facts, not independent persisted lifecycles.

Only validated, persistable Model/Tool results enter the pending-settlement retry path. Database retry never repeats an external operation. A terminal Run's in-flight operation must finish before Model continuation cleanup. Service-wide interruption ends all unfinished Main/Subagent Runs without waking a Parent, and startup never resumes them. Per-operation limits and bounded process-local admission are not Run step, Token or time quotas.
