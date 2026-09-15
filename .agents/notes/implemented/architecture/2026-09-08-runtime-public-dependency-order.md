# Agent Note: Runtime public dependency order

Status: implemented — the dependency ledger and its checks match the Run/Context public imports.

## Problem

The earlier dependency graph placed Context after Run because History flows from Run into the model view. The implemented Context service consumes explicit sourced values and Model-owned types; Run calls Context. Treating data flow as import order would declare the opposite dependency and invite a service cycle.

## Decision

Context's public dependency is Model. Run depends on Context for sourced assembly and validated projection reuse while retaining ownership of execution History and input consumption. Context does not import Run services, resolve permissions or read another owner's private tables. Schema foreign keys remain in the existing S1 wave and do not determine public import order.

The owner DAG, ordered owner ledger, declared approval order, receipt-verification command order and CI wrapper follow this dependency. Owner identities, contract hashes, approved artifacts and original receipts are unchanged; no approval is replayed or fabricated. Positive and negative import tests reject a Context-to-Run public dependency.

## Alternatives considered

Adding a reverse service call solely to match the old graph would create coupling without a consumer. Weakening the exact governance checks would conceal the mismatch. The declarations and tests are updated together instead.

## Consequences

The source-data direction remains Run History → Context view. This is not a transfer of History authority or another Runtime owner. Future Context capabilities must continue to consume explicit sources rather than rediscovering Run state.

## Verification

The governance, Goal, owner-contract and inventory group passed 159 checks; the CI wrapper, Goal and owner-contract group passed 111. New public-import tests cover supported imports and rejected reverse edges. Existing approval receipts remain valid.
