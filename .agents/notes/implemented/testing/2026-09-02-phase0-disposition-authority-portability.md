# Agent Note: Phase 0 disposition authority portability

Status: implemented — tracked authority files and recursive hash validation make every Phase 0 disposition decision recoverable from Git

## Problem

Every Phase 0 coverage row cites `backend/rewrite/disposition-evidence/endpoint-lifecycle-dispositions.json`, but the original evidence document cited the ignored `.omx/plans/backend-capability-coverage-matrix.md` as decision authority. The row-level hash gate proved only that the evidence JSON had not changed. A fresh clone could not recover the ignored matrix, and the canonical coverage check did not validate the authority paths or hashes inside the evidence document.

## Decision

The accepted matrix content and decisions live at the tracked `backend/rewrite/backend-capability-coverage-matrix.md`. The disposition approval generator records only repository-relative authority files that exist, are not ignored, and are known to Git. The canonical coverage validator recursively checks every authority declared by the endpoint/lifecycle disposition evidence: each path remains inside the repository, exists as a file, is not ignored, is Git-tracked, and matches its recorded SHA-256.

The `.omx` matrix may remain as a non-authoritative local mirror. Generated disposition evidence cannot cite it, and it cannot satisfy the coverage gate.

## Alternatives considered

Keeping `.omx/plans/backend-capability-coverage-matrix.md` as authority was rejected because `.omx/` is ignored and absent from a fresh clone. Removing the matrix citation was rejected because it would discard accepted decision evidence rather than make that evidence portable. Validating only the outer disposition-evidence hash was rejected because it does not prove that nested authorities are recoverable or unchanged.

## Consequences

Coverage validation invokes Git while checking authority portability. Repeated row references are deduplicated, so the shared disposition document and its nested authorities are validated once per check. Updating an authority requires deterministic evidence regeneration; regeneration fails if coverage IDs, dispositions, target owners, or the owner roster drift.

All 401 rows remain `disposition_approved`. Their shared evidence is bound to the tracked source-disposition Note and capability matrix. Authority refreshes update hashes only and do not approve an owner contract, advance a row, or change an accepted disposition.

## Verification

`backend/rewrite/backend-capability-coverage-matrix.md` is Git-tracked, and the recursive authority gate validates its current recorded SHA-256 rather than relying on a duplicated prose literal. The canonical coverage check reports `unreviewed=0`, `disposition_missing=0`, and `nonterminal=401`.

`backend/tests/architecture/test_rewrite_inventory.py` covers matching tracked authorities and rejects ignored, untracked, escaping, missing, malformed, and hash-drifted authorities. `backend/tests/architecture/test_rewrite_disposition_approval.py` verifies generator-side portability and decision-preserving evidence refresh. The focused inventory and disposition suite passed.
