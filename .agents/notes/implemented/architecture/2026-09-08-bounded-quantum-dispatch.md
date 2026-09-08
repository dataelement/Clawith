# Agent Note: Bounded quantum dispatch

Status: implemented — process-local admission and quantum dispatch mechanics are available.

## Problem

Holding an execution slot throughout a Run prevents fair re-entry and wastes capacity while waiting for input. Releasing admission before a terminal commit can instead admit work beyond the configured bound or lose track of unsettled execution.

## Decision

The Run-owned dispatcher separates reservations, fair ready positions and active quantum tasks. It executes at most the configured slot count, re-enters eligible work at the tail after a quantum and never overlaps two operations for the same Run. A Waiting Run retains its lightweight reservation without occupying a slot.

The owning lifecycle operation confirms rollback or terminal persistence before release. A failed settlement retains its reservation and blocks automatic execution replay; explicit settlement retry belongs to the Run owner. Shutdown stops new dispatch, cancels and awaits active operations, and leaves reservations until the owner confirms interruption. Repeated cancellation does not abandon the owned drain task.

## Alternatives considered

Whole-Run slots cannot provide quantum fairness. Automatically releasing on an exception would confuse failed persistence with committed termination. A persistent queue or execution lease would introduce recovery semantics excluded from this first release.

## Consequences

These mechanics do not write Run status, manage product outcomes, create Task objects or restore execution after process loss. The Run owner supplies the quantum and failure-settlement callbacks and can inspect bounded reservation inventory during confirmed shutdown cleanup.

## Verification

Seventeen dispatcher tests cover reservation bounds, duplicates, independent Tenant progress, slot release, settlement failures, cancellation and repeated shutdown cancellation. Runtime integration additionally tests draining a cancelled Model operation before removing continuation. Independent code and architecture review approved the bounded mechanism; standalone tests are not platform load qualification.
