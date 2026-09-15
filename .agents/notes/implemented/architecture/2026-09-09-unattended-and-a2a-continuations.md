# Agent Note: Resolve unattended delivery and A2A continuation boundaries

Status: implemented — product owners and application entry paths enforce the confirmed continuation boundaries. Formal mixed-load and live-provider qualification remain unverified.

## Problem

Unattended Runs could enter a human wait without an answer destination. A2A delivery was tied to one disposable source Run, and its receiver could copy private input into shared Agent files. These gaps concern input, delivery and file ownership rather than a new execution engine.

## Decision

The [continuation amendment](../../../../specs/backend-product-input-continuations.md) is the complete implementation contract. Trigger and Heartbeat are one-way work with explicit destinations or query-only results. A2A reuses Run Waiting and permits explicit authorized same-origin takeover without reviving old execution. Receiver-generated files remain request-owned temporary files until they are returned and saved through the sender's actual output Workspace.

Private origin is preserved separately from output location: an Agent's own execution scope does not make every incoming message or personal-account result public. This distinction governs result queries, shared-file mutations and destination checks without granting access to another Workspace.

## Alternatives considered

Automatically selecting the creation Session would invent a destination when none was configured. Keeping a Tool call open would consume capacity while awaiting independent work. Restarting a terminal source Run would contradict the first-release execution boundary. Saving receiver results into its shared Workspace would publish private content to other viewers. These alternatives are excluded by the confirmed amendment.

## Consequences

Unattended execution cannot ask a human question without an answer outlet; its Child may still ask its Parent. A2A replies append authorized input to the existing Waiting target, including explicitly delegated attachments, without rewriting the original request. Returned temporary-file revisions remain available until an authorized source saves and confirms them. Destination-free scheduled results support authorized terminal-output pagination rather than only a preview.

## Verification

Independent preflight is recorded in [the review evidence](../../../../backend/artifacts/rewrite/G006/continuations-contract-review.md). Owner amendment receipts preserve previous approved artifacts. Actual Tool/API tests cover Waiting, same-origin takeover, attachment delegation, temporary-file return/save, source-based result visibility, explicit scheduled destinations and complete result fragments. Independent reviewers verified the A2A attachment and temporary-document paths and scheduled result authorization. Cumulative evidence and its qualification limits are recorded in [the G006 report](../../../../backend/artifacts/rewrite/G006/product-input-e2e.txt). Valid manifests alone do not establish phase acceptance.
