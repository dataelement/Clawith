# Audit owner

This module is the sole owner of append-only Audit records and their actor attribution.

The [Audit observation contract](../../../../specs/backend-audit-observation.md) defines delivery, loss and lifecycle guarantees.

- Other owners emit through `AuditSink` and read through `AuditService` in `public.py`; models and repositories are private. No public coupled append operation exists.
- Emit successful observations only after business commit. Audit never shares the producing transaction, supplies authoritative business facts or propagates observation failures into business results.
- Composition starts one bounded `AsyncAuditSink` consumer and closes it before disposing its database sessions. Queue capacity and shutdown timeout are explicit; full, invalid, closed and failed observations are dropped with fixed counters, without logging input values or adding a retry bus.
- Every record names one explicit Tenant and exactly one Membership, Platform Account, Agent, or System actor. Agent actors may identify a corresponding same-Tenant Run.
- Metadata is versioned, JSON-only, byte-bounded, structurally Secret-free, and does not duplicate product payloads or Run History.
- Queries require a captured Tenant administrator Principal, apply that Principal's Tenant scope, and enforce bounded pagination.
- Audit exposes no update, delete, direct HTTP write, Runtime, or authorization-generation behavior.
