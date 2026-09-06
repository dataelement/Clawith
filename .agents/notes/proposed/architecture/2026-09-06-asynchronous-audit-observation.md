# Agent Note: Asynchronous Audit observation

Status: proposed — the asynchronous, non-blocking Audit boundary is agreed; the G003 service and its original approval artifacts still implement coupled transactions.

## Problem

Coupling Audit persistence to business success makes an observational record a prerequisite for an authoritative mutation. For Workspace content stored outside PostgreSQL, it also creates an unnecessary file-and-Audit commit requirement. Business decisions must use the owning facts rather than infer success or progress from the presence of Audit records.

## Proposal

Audit records operations asynchronously and remains outside the business operation's success boundary. The business owner determines its outcome from its authoritative state. It does not wait for Audit persistence, query Audit to decide whether work succeeded, or use Audit records for authorization, deduplication, continuation, recovery or completion decisions.

Audit emission, persistence failure and backpressure are contained within the Audit path. They must not roll back a business mutation, turn a committed success into failure, cause the operation to be replayed, or stop a Run. Audit reports committed outcomes only after the business commit; an attempted action is not recorded as a committed success. Audit remains append-only, Tenant-scoped and explicit about actor attribution, with bounded, versioned, Secret-free metadata.

This decision applies to Audit across the platform, not only to Workspace. Run History, current Workspace content and revision, authorization and Credential bindings, Skill installation state and other authoritative product records remain business facts with their own durability and consistency requirements. They are not moved into the asynchronous Audit path.

The implementation must define bounded delivery and cleanup ownership without adding a generic product event bus or a business state machine. Queue choice, capacity, overflow, shutdown, retries, deduplication and process-loss behavior remain implementation decisions. This decision does not promise lossless or exactly-once Audit delivery; neither delayed nor missing Audit records may change an authoritative business outcome.

## Alternatives considered

**Commit every required Audit record with its business mutation.** This is the current [G003 implementation](../../implemented/architecture/2026-09-06-atomic-audit-records.md). It was reconsidered because Audit availability should not govern the main flow, and applying it to external file storage would require stronger coordination than the functional release needs.

**Use Audit records to infer operation completion or authorize replay.** Rejected because delayed or missing observation cannot establish whether the authoritative operation happened.

## Implementation handoff and acceptance

The current [foundation contract](../../../../specs/backend-foundation.md), owner approvals, receipts and G003 verification remain evidence of the earlier implementation. They are not rewritten or rebound by this documentation decision. Before changing Audit code, review and bind the amended contract, update the owning service and callers, replace the coupled-rollback test with failure-isolation coverage, and rerun the affected cumulative gates.

Tests must show that Audit persistence failure, unavailable delivery and backpressure do not alter business results or block progress; business control paths do not query Audit to recover their authoritative state; successful observation still respects Tenant, actor and Secret boundaries. No such implementation or runtime verification is claimed here.
