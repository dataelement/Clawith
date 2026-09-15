# Agent Note: Foundation Contract Preflight

Status: implemented — the preparation tools express minimal Auth in G003 and verify approval evidence; foundation domain implementation is a separate gate.

## Problem

The accepted foundation scope moved minimal Auth before product APIs and replaced live revocation tracking with login-scoped human authorization. The existing gate roster still placed Auth in S3. Approval mutations lacked complete receipt checks, and later product contracts depended on ignored local paths. These gaps could block legitimate cumulative checks or make a local approval unrecoverable from Git.

## Decision

The existing `auth` owner registers in S1 and implements its minimal verifier/login-session contract in G003. Its later registration/recovery/product workflows retain the separate G007 Auth product-contract check; they do not require another Auth owner or replay of the earlier owner approval. The owner roster remains 34.

Owner approval and ledger rebuild share one manifest lock. Approval records the exact contract and review evidence hashes and an owner-row receipt. Exact replay is a no-op; when an approved row exactly matches the request but receipt publication was interrupted, repeating that request may recover only the matching receipt. Checks never repair missing receipts. Declared cumulative receipt verification must continue to work after later owners are approved and reject tampered, duplicate, missing or misattributed evidence.

Product contracts use stable tracked `specs/backend-products/<module>.md` paths. The [implementation contract rules](../../../../specs/README.md) distinguish semantic review from file/hash checks. The foundation artifact may be approved before domain code exists, but only the schema, service and integration tests in G003 can prove the implementation. Old Goal evidence stays attached to its tested source rather than being relabeled as current.

The source-disposition matrix remains an endpoint/lifecycle inventory. Detailed Tool behavior and functional acceptance are developed in their owning module phases. The immutable reference fixture currently proves application import only, not a product boot, database connectivity or a business black-box workflow. The preparation change does not claim G003 completion or any E2E capability.

## Alternatives considered

### Treat a matching hash as complete architectural review

Rejected because a hash establishes content identity, not that ownership, failure behavior or product intent is correct. Independent review remains a separate requirement.

### Put later Auth workflows under another owner

Rejected because the accepted change advances only the minimum Auth dependency; login and its later workflows still share one owner.

### Keep approval artifacts under ignored execution state

Rejected because a fresh checkout or CI could not recover those approvals. Local execution plans may mirror tracked contracts but do not replace them.

## Consequences and verification

The preparation can be completed while domain packages remain empty. Focused governance tests exercise receipt replay/recovery, build/approve serialization, cumulative carry-forward, Auth phase separation and tracked product artifacts. Full Backend and cumulative G000-G002 checks remain required before handing off the prepared branch. G003 schema/service tests, live providers, production startup and E2E are not evidence of this preparation.
