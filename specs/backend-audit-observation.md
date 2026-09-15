# Audit observation implementation contract

Status: user-approved architectural boundary and independently reviewed implementation contract; runtime implementation remains unverified.

## Authority and scope

This amendment applies only to the `audit` owner. It replaces the coupled-write requirement in [Backend Foundation](backend-foundation.md#Audit) with the agreed [asynchronous Audit boundary](../.agents/notes/proposed/architecture/2026-09-06-asynchronous-audit-observation.md). The foundation artifact and initial approval receipt remain immutable historical evidence. Existing Audit table ownership, Tenant isolation, actor union and metadata validation remain unchanged.

## Public interface

Business operations depend on an injected `AuditSink` protocol whose synchronous `emit(observation) -> None` submits a bounded immutable description without waiting for persistence. An observation identifies Tenant, actor, action, target, outcome, occurrence time and supported metadata version. No SQLAlchemy session, TransactionContext, repository or persistence result crosses this interface. Callers emit a successful outcome only after committing the authoritative operation. They never query Audit to determine permission, completion, replay, installation or Run state.

`emit` cannot propagate Audit validation, delivery or storage failures into business execution. It records bounded diagnostics or counters instead. The sink must not perform network or database I/O in the calling operation. There is no default no-op implementation selected silently; composition must provide its chosen sink explicitly. The Audit read service retains administrator-only, Tenant-filtered bounded listing and returns typed copied views.

## Minimal asynchronous implementation

One application-owned consumer drains a bounded in-memory queue with non-blocking submission. Queue capacity and shutdown drain timeout are explicit constructor inputs. The consumer persists each accepted observation in its own short transaction through the Audit-private repository. It does not borrow the producing operation's transaction or hold a transaction while waiting for more work. The worker has one lifecycle owner, is started once by composition and is stopped before its database resources are disposed.

For this functional release, invalid observations, a full/closed queue and failed writes are dropped with bounded non-Secret diagnostics and counters. There is no automatic business retry, generic event bus, outbox, durable queue, additional table or exactly-once claim. Audit delivery can be lost on process termination. A single invalid event or failed write does not stop processing later events. Shutdown drains only within its configured bound, then cancels and closes remaining work without leaking tasks or database connections.

## Integration and verification

Remove the public coupled `append` mutation port rather than preserving a compatibility path. Audit writes remain available only inside the consumer. Business database operations keep the existing shared TransactionContext for their own facts; Run History and installation bindings are not Audit events.

Tests cover non-blocking emission while storage is slow, queue-full and closed-sink behavior, invalid or cross-Tenant actor references, metadata bounds and unknown versions, storage failure followed by a valid event, successful real PostgreSQL persistence and scoped reads, bounded shutdown, task cancellation and connection release. Enqueue detaches nested metadata: later caller mutation cannot alter the observation, and rejection diagnostics contain no arbitrary input values. A committed business mutation remains committed when Audit rejects or cannot persist its observation. No test may use an Audit row as evidence of the business mutation itself.

Application composition owns an explicit sink configuration; no business HTTP or Runtime route is introduced by this amendment. Focused tests, configured Ruff/Pyright, independent review and cumulative foundation gates precede completion. Hosted delivery reliability and lossless Audit retention are not claimed.
