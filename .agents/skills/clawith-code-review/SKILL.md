---
name: clawith-code-review
description: Review Clawith changes for correctness, security, ownership, contract-chain completeness, test sufficiency, and maintainability. Use for code review, pull-request review, merge readiness, or after a non-trivial implementation is complete.
---

# Clawith Code Review

Review read-only unless the user separately authorizes fixes. Inspect the complete change against its verified Base, not only the last commit or largest file.

## Load the contract

Read the root `AGENTS.md`, every path-specific `AGENTS.md` governing the changed files, [the testing policy](../../../docs/testing.md), and [the Agent Note rules](../../notes/README.md). Identify the product or architecture contract the change claims to implement.

## Trace the change

Group the diff by behavioral intent. For each intent, trace the authoritative owner, producers, mutations, persistence, API/Event/Tool/worker boundary, consumers, Frontend/model/external representation, errors, compatibility behavior, tests, documentation, and owning Agent Note.

Reject a broader test suite as compensation for an incomplete chain. Flag duplicated facts, parallel lifecycle state machines, authorization enforced only in UI/Prompt/wrappers, state published before its commit point, and local Tool or Provider failures that block unrelated work without an explicit contract.

## Review lanes

Always review from both a code/security/quality perspective and an architecture/devil's-advocate perspective. Keep the findings separate before synthesis. Use independent reviewers when available, and require them for security-, Runtime-, permission-, persistence-, migration-, or cross-layer high-risk changes.

The code lane checks correctness, security, tenant and permission enforcement, error contracts, data access, performance, dead code, tests, and maintainability. The architecture lane checks fact ownership, boundary placement, state machines, public contracts, long-term coupling, and the strongest counterargument to approval.

## Evidence and severity

Every finding cites a current file and line, the violated contract, concrete impact, and a bounded repair. Rate findings `CRITICAL`, `HIGH`, `MEDIUM`, or `LOW`; rate architecture `CLEAR`, `WATCH`, or `BLOCK`.

Return `REQUEST CHANGES` for any CRITICAL/HIGH correctness or security finding, architecture `BLOCK`, an incomplete contract chain, a missing required Agent Note, or unavailable independent review on a high-risk change. Return `COMMENT` for architecture `WATCH`, non-blocking improvements, or unavailable independent review on a lower-risk change. Return `APPROVE` only when no blocker remains, verification evidence matches the claims, and the required review perspectives were completed.

## Report

Lead with the verdict. List blocking findings first, then non-blocking findings, verification reviewed, unverified surfaces, Agent Note alignment, and the final code-review/architecture synthesis. Do not praise, summarize the implementation, or invent issues to fill categories.
