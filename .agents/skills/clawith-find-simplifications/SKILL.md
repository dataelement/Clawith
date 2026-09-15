---
name: clawith-find-simplifications
description: Find and optionally apply evidence-backed Clawith simplifications through deletion, reuse, and ownership-boundary repair. Use for cleanup, refactoring, dead-code removal, duplicate-state removal, or requests to reduce complexity without changing approved behavior.
---

# Clawith Find Simplifications

Require an explicit scope. Review read-only unless the user authorizes edits. Preserve verified behavior and unrelated working-tree changes.

## Find candidates

Look for code, configuration, tests, compatibility paths, abstractions, state machines, caches, wrappers, services, and documentation with no current contract or production consumer. Also look for duplicated facts, duplicated lifecycle control, per-item queries, repeated full materialization, scattered configuration resolution, raw transport behavior in components, and behavior placed outside its authoritative owner.

Prefer this order:

```text
Delete obsolete behavior
→ Reuse the existing owner or utility
→ Move misplaced behavior back to its owner and remove bypasses
→ Introduce a new abstraction only for an independently changing responsibility with a current consumer
```

## Prove removal is safe

Search direct and dynamic imports, configuration, registries, routes, workers, background entrypoints, Tool and model schemas, persistence, migrations, API/Event/Wire contracts, Frontend consumers, external integrations, tests, and documentation. A text search with no callers is not sufficient proof when loading or consumption is dynamic.

Classify each candidate as `delete`, `reuse`, `move-to-owner`, `keep`, or `defer`. State the current owner, consumer evidence, behavior preserved, and checks required.

## Apply authorized changes

Lock behavior with the narrowest regression test when existing coverage does not protect it. Make one intent-focused simplification at a time. Delete obsolete implementation, configuration, tests that only preserve deleted behavior, compatibility paths, and stale documentation together.

A non-trivial simplification adds or updates its owning Agent Note. A complete feature removal preserves why the feature existed, why it no longer justified its surface, what capability is lost, and what conditions would justify reintroduction.

## Verify and report

Use [the testing policy](../../../docs/testing.md) and pre-push workflow to select evidence. Report inspected scope, deletions, reuse, boundary repairs, deliberate keeps, deferred candidates, exact checks, and remaining risk. Never measure success by lines deleted alone.
