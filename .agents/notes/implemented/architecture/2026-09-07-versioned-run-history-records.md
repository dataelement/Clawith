# Agent Note: Versioned Run History records

Status: implemented — version-1 History codecs and private transactional persistence are available; Snapshot and lifecycle writers remain G005 work.

## Problem

Execution records must remain inspectable across upgrades without interpreting arbitrary JSON as a stable protocol. Model Steps also need to retain the exact History read boundary so disposable Context summaries cannot decide whether an input was consumed.

## Decision

Run owns closed payload forms for initial/related input, Model Step, Tool Result, Waiting and terminal outcome. Encoding returns detached JSON with an explicit kind and version. Decoding rejects unknown kinds/versions, extra fields, invalid primitives, duplicate Model-call identities and non-finite or structurally oversized content; diagnostics never embed raw source values.

Input content is normalized text plus bounded opaque references. References do not grant access or determine how a product or Child input is admitted. Tenant, Run and owner-issued source identity remain separate History columns. No Task ID or lifecycle is added.

Model records retain every normalized result field, including Tool Calls, optional usage counters, interaction identity and required-continuation markers. Opaque continuation itself remains Model-owned. Embedded Model arguments and Tool Result JSON strings round-trip without rewriting. Waiting permits an empty question when the Run waits for Child results rather than human information.

The full encoded record is bounded to 16 MiB; input content to 256 KiB with 64 references. Structural validation bounds nesting to 32 and total nodes to 100,000, including pending traversal work before expanding it. Numbers, literals, punctuation and escaped UTF-8 strings count toward the byte budget before whole-record serialization. Reference and Model Call cardinality are checked before conversion. These are record-operation limits, not Run step or Token quotas.

The private History repository appends within the caller's TransactionContext. A Tenant-scoped Run row lock serializes sequence allocation, source lookup and the History insert; rollback restores both the row and its sequence cursor. Repeated owner-issued source identity returns the original stored entry without changing its payload or sequence. Initial and related inputs require source identity; execution facts may use it for commit retries. Only related-input records satisfy the unseen-input predicate.

History pages freeze an upper sequence boundary and contain at most 100 entries within a 32 MiB operation budget. A bounded metadata query measures uncompressed PostgreSQL JSON text before payload loading, reserves envelope and source metadata bytes, and selects only the fitting prefix. A first entry that cannot fit fails explicitly. Payload fetching also enforces the measured size, so an enlarged row cannot bypass the budget between queries. PostgreSQL JSONB formatting receives a bounded whitespace allowance before the exact codec validation; compressed storage size is not a safe read bound. Missing sequences and unsupported persisted payloads fail instead of returning incomplete authoritative History.

## Alternatives considered

Unversioned dictionaries or permissive decoding would discard unsupported fields after an upgrade. Using Context projection coverage as the input cursor would change execution semantics when a projection is rebuilt. Re-encoding embedded Tool JSON would alter observed source content unnecessarily.

## Consequences

The codec defines representation and the repository owns transactional History persistence, not lifecycle transitions. The lifecycle service must enforce admission, Snapshot creation, Parent-first transactions, valid Model read boundaries and terminal rules. It must hold the relevant Run locks while testing for unseen related input and deciding Waiting or completion. Repository append results are uncommitted until the caller's transaction succeeds; they must not trigger external publication before that commit.

## Verification

Tests exercise exact Model/Tool round trips, zero versus missing counters, invalid/unknown envelopes, secret-safe diagnostics, Unicode and byte boundaries, references, structural node/depth limits and detached output. G005-only package guards keep Foundation schema approval insufficient for these implementation files and do not unlock G006 services.

Real PostgreSQL repository tests exercise source deduplication, concurrent contiguous sequence allocation, independent Run progress, cancellation, transaction rollback, Tenant isolation, fixed-cutoff pagination, prefetch byte rejection, corrupt records and the related-input predicate. These fixtures seed Run rows directly and do not prove Run admission, Snapshot atomicity, lifecycle execution or G005 E2E.

The combined Run and package/import guard suite passed 172 tests, including 16 repository tests. Scoped Ruff and Pyright passed. Independent code and architecture review approved the private persistence slice.
