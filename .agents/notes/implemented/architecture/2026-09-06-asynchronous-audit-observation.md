# Agent Note: Asynchronous Audit observation

Status: implemented — Audit uses a non-blocking observation interface and an application-owned asynchronous consumer with independent persistence.

## Problem

Coupling Audit persistence to business success makes an observational record a prerequisite for an authoritative mutation. For Workspace content stored outside PostgreSQL, it also creates an unnecessary file-and-Audit commit requirement. Business decisions must use the owning facts rather than infer success or progress from the presence of Audit records.

## Decision

Audit records operations asynchronously and remains outside the business operation's success boundary. The business owner determines its outcome from its authoritative state. It does not wait for Audit persistence, query Audit to decide whether work succeeded, or use Audit records for authorization, deduplication, continuation, recovery or completion decisions.

Audit emission, persistence failure and backpressure are contained within the Audit path. They must not roll back a business mutation, turn a committed success into failure, cause the operation to be replayed, or stop a Run. Audit reports committed outcomes only after the business commit; an attempted action is not recorded as a committed success. Audit remains append-only, Tenant-scoped and explicit about actor attribution, with bounded, versioned, Secret-free metadata.

This decision applies to Audit across the platform, not only to Workspace. Run History, current Workspace content and revision, authorization and Credential bindings, Skill installation state and other authoritative product records remain business facts with their own durability and consistency requirements. They are not moved into the asynchronous Audit path.

Business producers receive the `AuditSink.emit` interface, which performs no database or network I/O. Accepted metadata is validated, detached and serialized before entering an in-memory queue. One application-owned `AsyncAuditSink` consumes each observation through the Audit-private repository in its own transaction. The application supplies capacity 256 and a two-second drain bound, uses execution-pool sessions without borrowing a business transaction, and closes the consumer before disposing database resources.

Invalid, full-queue, closed-sink and failed-write observations are dropped and counted without logging arbitrary input values. A failed write does not stop later observations. Close stops admission, drains within its bound, cancels remaining work and releases connections; concurrent or cancelled close callers share the same cleanup. There is no generic event bus, outbox, business replay or new persistent queue. Process termination may lose Audit observations; neither delayed nor missing records change an authoritative business outcome.

## Alternatives considered

**Commit every required Audit record with its business mutation.** The [earlier coupling decision](../../archived/architecture/2026-09-06-atomic-audit-records.md) was replaced because Audit availability should not govern the main flow, and applying it to external file storage would require stronger coordination than the functional release needs.

**Use Audit records to infer operation completion or authorize replay.** Rejected because delayed or missing observation cannot establish whether the authoritative operation happened.

## Consequences

The [reviewed amendment](../../../../specs/backend-audit-observation.md) is the active Audit implementation contract. Initial foundation approval and G003 receipts remain immutable historical evidence; the owner ledger carries an appended amendment receipt. The public coupled `AuditService.append` port is removed. `AuditService.list` remains a bounded Tenant-administrator read surface, not a business decision input. No new product HTTP or Runtime route is introduced.

Existing Audit foreign keys still restrict physical deletion of referenced identities and Runs. Current public identity/Agent operations disable or archive those records; they do not physically delete them. A future deletion contract must address historical Audit references before enabling deletion. This implementation does not claim complete physical-deletion lifecycle independence.

## Verification

Real PostgreSQL tests verify business commit survival after Audit failure, valid/invalid Tenant and Agent/Run attribution, continued consumption after a failed write, copied metadata, bounded queue behavior, slow-write cancellation and released connections. Application lifecycle tests verify that the consumer stops before database disposal, including exceptional application exit and failed initialization. Hosted delivery reliability, lossless retention and later product workflow coverage are not claimed.
