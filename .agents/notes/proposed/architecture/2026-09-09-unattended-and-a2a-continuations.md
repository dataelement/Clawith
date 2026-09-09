# Agent Note: Resolve unattended delivery and A2A continuation boundaries

Status: proposed — the user has confirmed the behavior; G006 implementation and cumulative verification are in progress.

## Problem

Unattended Runs could enter a human wait without an answer destination. A2A delivery was tied to one disposable source Run, and its receiver could copy private input into shared Agent files. These gaps concern input, delivery and file ownership rather than a new execution engine.

## Proposal

The [continuation amendment](../../../../specs/backend-product-input-continuations.md) is the complete implementation contract. Trigger and Heartbeat are one-way work with explicit destinations or query-only results. A2A reuses Run Waiting and permits explicit authorized same-origin takeover without reviving old execution. Receiver-generated files remain request-owned temporary files until they are returned and saved through the sender's actual output Workspace.

The implementation must preserve private origin separately from output location: an Agent's own execution scope does not make every incoming message or personal-account result public. This distinction governs result queries, shared-file mutations and destination checks without granting access to another Workspace.

## Alternatives considered

Automatically selecting the creation Session would invent a destination when none was configured. Keeping a Tool call open would consume capacity while awaiting independent work. Restarting a terminal source Run would contradict the first-release execution boundary. Saving receiver results into its shared Workspace would publish private content to other viewers. These alternatives are excluded by the confirmed amendment.

## Acceptance

Independent preflight is recorded in [the review evidence](../../../../backend/artifacts/rewrite/G006/continuations-contract-review.md). The new owner amendment receipts preserve previous approved artifacts. Preflight and valid manifests are not implementation acceptance. Actual Tool/API paths, race and privacy tests, resource cleanup, independent implementation review and cumulative Backend regression must complete before G006 is reported complete. Formal external-provider and platform-performance qualification remain separately stated.
