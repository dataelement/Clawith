# G006 continuation contract preflight

Reviewed contract: `specs/backend-product-input-continuations.md`.

The user confirmed unattended one-way Trigger delivery with explicit destinations or query-only results, A2A result waiting and explicit same-origin takeover, and temporary receiver files returned to the sender's actual output Workspace. No implementation or formal qualification is certified by this preflight.

## Independent review

The code/security reviewer (`g005_final_code_audit`) returned APPROVE after the contract explicitly froze returned file revisions, hashes and sizes and required rejection of subsequent rewrites/deletes. The final private-origin paragraph was reviewed: queries and delivery must not expose one user's input merely because another user can see the Agent.

The architecture reviewer (`g004_tool_implementation`) returned CLEAR. Run remains the wait/lifecycle owner; request routing and file metadata remain A2A-owned; no new Agent, Workspace type, Task/Goal state machine, Artifact owner or Sandbox is introduced.

## Required implementation checks

- Serialize result readiness, wait and unseen-input handling; an already available result cannot leave an empty wait.
- Bind delivery and acknowledgement to the current recipient Run and observed result identity. A stale recipient cannot acknowledge a replacement recipient's delivery, and a non-terminal recipient cannot be displaced.
- Include reserved and unconfirmed temporary publications in resource bounds. Serialize return selection, save confirmation and cleanup with revision checks.
- Keep unattended Main human waiting disabled while permitting Child questions to their Parent. A source without an authorized persistent conversation must not acquire a fabricated recipient or arbitrary takeover authority.
- Verify private result visibility and destination restrictions, including personal Credential provenance and old-version owner metadata; do not silently discard or expose old records after upgrade.

The contract extends existing approvals through new amendment receipts. Prior specifications, evidence and receipts remain unchanged. Implementation review, application E2E, cumulative regression and separately reported performance evidence remain outstanding.
