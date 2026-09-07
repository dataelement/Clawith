# Agent Note: Versioned Run History records

Status: implemented — closed version-1 History codecs are available; Snapshot, persistence and lifecycle writers remain G005 work.

## Problem

Execution records must remain inspectable across upgrades without interpreting arbitrary JSON as a stable protocol. Model Steps also need to retain the exact History read boundary so disposable Context summaries cannot decide whether an input was consumed.

## Decision

Run owns closed payload forms for initial/related input, Model Step, Tool Result, Waiting and terminal outcome. Encoding returns detached JSON with an explicit kind and version. Decoding rejects unknown kinds/versions, extra fields, invalid primitives, duplicate Model-call identities and non-finite or structurally oversized content; diagnostics never embed raw source values.

Input content is normalized text plus bounded opaque references. References do not grant access or determine how a product or Child input is admitted. Tenant, Run and owner-issued source identity remain separate History columns. No Task ID or lifecycle is added.

Model records retain every normalized result field, including Tool Calls, optional usage counters, interaction identity and required-continuation markers. Opaque continuation itself remains Model-owned. Embedded Model arguments and Tool Result JSON strings round-trip without rewriting. Waiting permits an empty question when the Run waits for Child results rather than human information.

The full encoded record is bounded to 16 MiB; input content to 256 KiB with 64 references. Structural validation bounds nesting to 32 and total nodes to 100,000, including pending traversal work before expanding it. Numbers, literals, punctuation and escaped UTF-8 strings count toward the byte budget before whole-record serialization. Reference and Model Call cardinality are checked before conversion. These are record-operation limits, not Run step or Token quotas.

## Alternatives considered

Unversioned dictionaries or permissive decoding would discard unsupported fields after an upgrade. Using Context projection coverage as the input cursor would change execution semantics when a projection is rebuilt. Re-encoding embedded Tool JSON would alter observed source content unnecessarily.

## Consequences

The codec defines representation, not lifecycle transitions. The future writer must enforce Tenant/Run relationships, source deduplication, valid sequence boundaries, Parent-first transactions and terminal rules before committing. Pure codec success is not persistence or E2E evidence.

## Verification

Tests exercise exact Model/Tool round trips, zero versus missing counters, invalid/unknown envelopes, secret-safe diagnostics, Unicode and byte boundaries, references, structural node/depth limits and detached output. G005-only package guards keep Foundation schema approval insufficient for these implementation files and do not unlock G006 services.

The focused codec suite passed 46 tests; codec plus package/import guards passed 156. Ruff and configured Pyright passed. Independent code and architecture reviews approved this codec slice, not the subsequent persistence or execution behavior.
