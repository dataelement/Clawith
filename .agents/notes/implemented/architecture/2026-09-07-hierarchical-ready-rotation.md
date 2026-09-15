# Agent Note: Hierarchical ready rotation

Status: implemented — G005 has a bounded in-memory ready-queue primitive; Runner admission, dispatch and lifecycle integration remain incomplete.

## Problem

A flat FIFO of Runs lets one Tenant's Run count dominate execution opportunities. Admission fairness alone cannot prevent a long-running Agent Loop from repeatedly acquiring execution capacity.

## Decision

Run-owned `FairReadyQueue` uses ordered Tenant, Agent and Run rotations. One selection removes the first ready Run and moves surviving Agent/Tenant positions to their rotation tails. Re-entry is explicit and happens only after Runner decides that a completed Model Step or Tool batch remains eligible. A duplicate wake retains the original position; reusing a Run identity under another Tenant or Agent fails.

The queue has an explicit finite capacity. Overflow does not remove or replace existing work. A Run index supports constant-time removal; empty Agent and Tenant branches are removed immediately. Clearing the queue drops only transient ready positions, not Run facts.

## Alternatives considered

A flat Run queue would let one Tenant enlarge another Tenant's allocation bound. Persisted scheduling state, per-Run waiting Tasks and an independent scheduler service are unnecessary for the approved single-Runner design. Keeping empty branches or cancellation tombstones would allow transient state to grow after work ended.

## Consequences

This primitive does not own admission permits, execution slots, Run Status, History, task cancellation or dispatcher readiness. Runner must prevent in-flight/terminal work from being re-enqueued incorrectly. Waiting and restart behavior remain authoritative Run-service decisions. The queue's capacity is an invariant within the admitted-work bound, not a Run step, time or Token limit.

## Verification

Seventeen tests cover each rotation level, per-Agent ordering, duplicate ownership, capacity boundaries, removal and cleanup. The hostile selection test keeps 50 Tenant-A Runs re-entering and verifies that Tenant B receives a selection by the second allocation after becoming ready. This proves the queue's allocation order, not actual Model dispatch, control-plane latency, integrated G005 fairness or 50-Agent performance.
