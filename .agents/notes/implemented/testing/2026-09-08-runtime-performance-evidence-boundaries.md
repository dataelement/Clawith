# Agent Note: Runtime performance evidence boundaries

Status: implemented — repeatable core and intake diagnostics are available; the reference environment was explicitly deferred.

## Problem

Passing functional tests or a reduced local benchmark cannot establish the platform's 50-Agent target. Startup optimization also needs a like-for-like measurement rather than attributing all delay to PostgreSQL or physical memory without evidence.

## Decision

The core load entry reads the unchanged reference profile, uses disposable PostgreSQL and actual native Runtime/Model-adapter/Workspace paths, and keeps the declared 180-second warmup and 900-second measurement. Ordinary test runs skip that opt-in long test. Short smoke checks verify only the driver. Reports disclose actual hardware, topology, missing workloads and qualification; unmeasured API surfaces are not populated with invented zero values.

Intake diagnostics use real PostgreSQL with independent control/execution pools of 20, fifty concurrent starts and three rounds. Snapshot source preparation occurs before timing and dispatch wake is replaced by a recording callback, so this measures durable startup acceptance rather than Model execution or first-token latency. Source duplicates, SQL calls, Snapshot encoding, capacity cleanup and first/warm rounds are observed separately. Async SQL and connection timing include event-loop scheduling and do not isolate PostgreSQL server time.

Latency statistics use bounded histograms for the long test. Same-source retries must still return the original Run and consume no extra execution identity. Optimization cannot pass by returning success before commit or disabling read validation.

## Alternatives considered

Shortening the frozen profile and reporting qualification would misstate evidence. Allocating a 16 GiB VM on this 16 GiB host would not provide a credible isolated reference environment. The user explicitly declined that environment change; local diagnostic results remain useful without claiming formal qualification.

## Consequences

The recorded earlier core load is diagnostic only: its source was still under development, storage and memory differ from the reference profile, and mixed product/slow/CPU Tool workloads are incomplete. The final startup improvement must not be presented as full-platform or frontend responsiveness acceptance.

## Verification

The startup fixture passed on both implementations. Warm-start P95 fell from 787.5/722.9 ms to 267.2/287.8 ms; cold-burst P95 changed from 680.8 to 579.9 ms. The comparison artifact states the exact scope. Hostile Runtime fairness tests cover 1 and 50 execution slots. The full working-tree backend suite passed 2681 tests with the opt-in long test skipped; existing deprecation warnings remain separate from failures.
