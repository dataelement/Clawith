# Agent Note: Cumulative Goal Checkpoints for the Backend Rewrite

Status: proposed — the tracked contract and G003 foundation services exist; fresh checkpoint evidence and later E2E gates remain independently required

## Problem

The clean-break Backend rewrite cannot run a complete product E2E after every intermediate change because the real Runtime and product entry paths appear only in later phases. Phase-level prose lists tests, but it does not prevent a later Goal from dropping an earlier gate, treating test collection as E2E, or replaying an approval, ledger transition, or reference-removal command whose effect already occurred. This makes “test after each Goal” ambiguous and can hide the first point at which real E2E becomes available.

## Proposal

G003's current implementation contract is [Backend Foundation](../../../../specs/backend-foundation.md). Minimal Auth uses the existing `auth` owner in S1/phase 2 and follows Permission in dependency order. Full registration, recovery and later Auth product workflows still require their separate product contract in G007; an earlier minimal owner approval does not approve those workflows. The login-session authorization decision removes authorization generations, the Run dependency projection and revocation cancellation sweeps from schema and test requirements. Existing G001/G002 artifacts remain evidence of their named historical source commits, not automatic evidence for this preflight.

`backend/rewrite/goal-gates.json` is the tracked G000-G009 checkpoint contract, and `backend/scripts/validate_goal_gates.py` validates its structure. The manifest is governance data, not a Runtime state machine or execution ledger. It declares repeatable validation commands, expected evidence paths, separately listed mutations, and cumulative carry-forward. A Goal passes only with fresh evidence for its own validations and every earlier Goal gate. Canonical required paths must be tracked and recoverable from Git; ignored `.omx` files may mirror the contract for live execution but cannot supply required evidence.

The E2E boundary advances monotonically. G000 is the planning gate. G001 validates Phase 0 without implementation. G002 requires architecture, static, and complete Pytest collection disposition but does not call collection E2E. G003 and G004 require schema and integration evidence. G005 is the first real-entry core Runtime E2E through a test-only Product Input owner. G006 is the first real product-input API and WebSocket E2E. G007 reruns all implemented module E2E cumulatively. G008 runs the complete Backend E2E in a fresh environment before legacy-reference removal, records removal as a mutation receipt, and reruns the fresh-environment E2E afterward. G009 reruns the complete Backend suite, deployment, final load, cleanup, and target-only recovery gates.

G003 registers complete S0/S1 schema. Its dependency-ordered approval roster is `identity_tenant`, `credential`, Model, Agent, Permission, minimal Auth, Audit, Workspace, Tool, Capability Market, Run and Context. Workspace, Tool and Capability Market are contract-only transitive prerequisites; their schema and services remain G004. G003 implements the seven foundation owners including minimal Auth; Run and Context bring schema only and their services remain G005. G004 registers complete S2 schema and adds approvals for `session`, `a2a`, `group`, `trigger`, `heartbeat` and `channel`. Their services/APIs remain G006. Each new owner approval is a receipt-guarded mutation in dependency order. G003's authenticated foundation integration is distinct from G005 core Runtime E2E and G006 product-input E2E.

G001 uses repository commands to validate the actual 401-row disposition state with zero unreviewed or missing dispositions, governance, the owner DAG/wave roster, product roster and approved linkage, the strict load profile, and the immutable reference. Passing tests do not substitute for these current ledger, profile, and reference checks. Every validation ID has one exact command and artifact path. The immutable-reference command is `bash ../scripts/check-g001-reference.sh` from `backend/`; that tracked wrapper exclusively owns temporary checkout creation, the legacy virtual environment, distinct persistence inputs, black-box execution, and cleanup. Shell composition, unknown scripts, alternate whitespace, filesystem writes, Alembic upgrade, reference binding, and build/approval/transition/release commands are outside the repeatable validation language.

Drone and GitHub Actions invoke one tracked shell entry that carries G000 and G001 into G002 in manifest order, then adds the complete Backend test suite required by the current checkpoint. Both workflows fetch full history. The CI entry invokes the exact G001 wrapper declared by the manifest instead of duplicating its worktree command or persistence environment. The wrapper creates a temporary detached worktree at the fixed legacy commit, installs that checkout into its own `backend/.venv`, supplies explicit distinct reference and target persistence values, and passes that exact virtual-environment Python path to the immutable-reference validator. Its interpreter may be a normal venv symlink to a system executable; no other override path is accepted. This validation override never binds, releases, or rewrites the canonical manifest. Cleanup preserves the gate result, removes the temporary path, and prunes only to recover a failed worktree removal.

The tracked `backend/artifacts/rewrite/G001/` and `backend/artifacts/rewrite/G002/` files record the first completed cumulative checkpoints. They bind the exact commands, source commit, time, exit status, and bounded result summary. They are point-in-time evidence rather than permanent health claims; any later source change must rerun the affected cumulative gates and replace the evidence in a new commit instead of treating the old result as current.

G005 also requires an adversarial execution-scheduler test. After each bounded Model Step or bounded Tool batch, a still-runnable Run releases its scarce execution slot and re-enters the in-memory Tenant-then-Agent scheduler. With 50 continuously runnable, nonterminating Tenant A Runs occupying all initial slots, an eligible Tenant B Run obtains its next Model Step after at most one consecutive eligible-Tenant skip, while FIFO remains per Agent. Cancellation or failure removes the Run and releases capacity. The test does not use the initial admission queue as a substitute and does not introduce a persisted queue, checkpoint, durable scheduler state, or whole-Run limit.

Commands that build a ledger, approve a contract, transition coverage, or remove the immutable reference are mutations. Their manifest entries name a receipt and require `verify_receipt_before_execute`. Owner-contract approval receives that exact receipt path as an explicit command input and serializes the ledger mutation with one manifest lock. The receipt binds the owner, manifest, contract and evidence hashes, resulting state, and resulting owner-row hash. An exact replay verifies the receipt and current ledger before returning without another mutation. If the ledger write completed but receipt publication was interrupted, the same request may reconstruct only the matching receipt; a different contract, evidence set, owner row, or receipt fails closed. Approval also requires every public-DAG dependency to be approved first. Repeatable checks may be rerun freely. The validator never executes either class of command. G008 fixes the complete reference-removal mutation object: exact command, receipt, pre-removal E2E artifact, and post-removal E2E artifact. The validator binds those artifacts to the ordered before/after validation entries so removal cannot precede its fresh-environment precondition or replace the required post-removal rerun.

## Acceptance criteria

- The manifest contains exactly G000 through G009 in order and each Goal carries forward the complete earlier prefix.
- The Goal-to-phase crosswalk and E2E levels match the approved rewrite plan and never regress.
- Every canonical required authority is tracked; the validator rejects ignored `.omx` paths as required evidence.
- Validation entries contain no known build, approval, transition, or reference-removal command.
- Every mutation has a receipt path and the receipt-first replay policy.
- Every owner approval command explicitly receives its declared receipt, serializes concurrent ledger changes, enforces all owner-DAG dependencies, and accepts only an exact replay or exact missing-receipt recovery.
- G003 and G004 contain the complete S0/S1 and S2 schema rosters plus their dependency-ordered new contract-approval rosters without changing service/API implementation ownership.
- G001 contains separate actual checks for disposition state, governance, owner DAG/waves, product roster/linkage, strict load profile, and immutable reference.
- Every validation ID maps to one exact non-mutating command; shell composition, unknown commands, mutation commands, Alembic upgrade, alternate whitespace, and filesystem writes fail validation.
- G005 contains the exact hostile execution-scheduler fixture and excludes admission-queue and durable-state substitutes.
- G008 names fresh-environment E2E artifacts from both sides of the reference-removal receipt.
- Positive and negative architecture tests enforce the roster, carry-forward, command separation, E2E progression, fixture paths, approval rosters, fairness fixture, and phase crosswalk.

## Alternatives considered

### Keep the checkpoint rules only in ignored OMX plans

This would preserve execution guidance but leave no tracked authority for review or CI validation. It was rejected because the rewrite gates must survive local OMX state and be enforceable from the repository.

### Store completion state in the manifest

This would combine the gate definition with mutable execution state and duplicate Ultragoal or evidence-ledger ownership. It was rejected because the repository needs a stable contract and independently produced receipts, not another lifecycle controller.

### Rerun every listed command without classifying side effects

This is safe for validation commands but unsafe for contract approvals, coverage transitions, ledger builds, and reference removal. It was rejected because those operations change authority or filesystem state and may be invalid or destructive when repeated.

### Infer an approval receipt path inside the owner checker

This would hide a Goal-owned mutation fact inside generic ledger code and let the command differ from the reviewed receipt. It was rejected in favor of passing the manifest-declared path explicitly and verifying it as part of the exact mutation command.

## Risks and open evidence

G001 and G002 have tracked checkpoint evidence. G003 has S0/S1 schema and foundation service tests; its exact integration command includes all database tests and the seven implemented owner directories. G004-G009 still need their named fixtures and receipts. Presence in the contract is not evidence that a test ran or that E2E is currently available. The tracked validator proves only governance consistency. Each Goal still requires fresh command results bound to its verified source. The live `.omx` plans and Ultragoal files mirror this tracked authority for execution convenience but remain ignored, non-authoritative runtime artifacts.
