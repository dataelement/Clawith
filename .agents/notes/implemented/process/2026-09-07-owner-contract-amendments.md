# Agent Note: Preserve approval history across contract amendments

Status: implemented — S0–S2 owner amendments append verified receipts without rewriting the initial approval.

## Problem

The owner ledger supported first approval and exact replay but rejected a reviewed change to an already approved contract. Replacing the original artifact or receipt would destroy the evidence used by an earlier checkpoint.

## Decision

`check_owner_contracts.py amend` updates the existing authoritative owner row and appends a new receipt path. The receipt contains the reviewed replacement binding and the preceding receipt's path and hash. Checks follow the chain from the canonical initial approval, validate every historical artifact and review hash, and require the final binding to match the current row. No second readiness ledger or product state machine is added.

Amendment uses the same manifest lock as approval and build. Exact replay is a no-op. If the ledger write succeeded but final receipt creation was interrupted, only the matching request can recover that missing receipt; validation itself never repairs state. Identity, phase, wave and approval state cannot change through an amendment. Output paths cannot overwrite authoritative inputs or another receipt. Build preserves and validates the chain. S3 amendment remains unavailable until its product and owner ledgers have a jointly reviewed update operation.

## Alternatives considered

**Overwrite the original contract or receipt.** Rejected because older checkpoints would lose their bound evidence.

**Bypass approval checks after a user decision.** Rejected because subsequent code still needs an exact reviewed contract and verifiable current binding.

## Consequences and verification

Reviewed replacement contracts and evidence use new stable paths; original G003 bindings remain inspectable. Focused tests cover chain validation, altered historic content, owner changes, duplicate/cyclic paths, output collisions, concurrent replay and interrupted receipt recovery. This is repository-governance verification, not database migration or runtime behavior.
