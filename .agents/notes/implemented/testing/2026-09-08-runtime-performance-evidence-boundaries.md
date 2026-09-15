# Agent Note: Runtime performance evidence boundaries

Status: implemented — repeatable core and intake diagnostics are available; the reference environment was explicitly deferred.

## Problem

Passing functional tests or a reduced local benchmark cannot establish the platform's 50-Agent target. Startup optimization also needs a like-for-like measurement rather than attributing all delay to PostgreSQL or physical memory without evidence.

## Decision

Fairness tests distinguish committed admission release from physical execution cleanup. A controlled barrier holds the real continuation cleanup after terminal outcomes commit; the test then releases it and waits for every Run's cleanup and zero active execution tasks. An empty admission set alone is not proof of resource termination. This verifies the existing two-phase lifecycle without delaying durable completion or changing Runtime behavior.

The core load entry reads the unchanged reference profile, uses disposable PostgreSQL and actual native Runtime/Model-adapter/Workspace paths, and keeps the declared 180-second warmup and 900-second measurement. Ordinary test runs skip that opt-in long test. Short smoke checks verify only the driver. Reports disclose actual hardware, topology, missing workloads and qualification; unmeasured API surfaces are not populated with invented zero values.

Core qualification is computed from observations, not hardcoded. It requires 50 Agents, 50 actually created concurrent client lanes, and an observed peak of exactly 50 active execution slots during measurement. Warmup and drain observations do not establish that peak. The reference hardware and service topology, fixed capacities and payload targets must match; actual phase durations cannot be shorter than 180/900 seconds and may overrun by at most one polling interval of one second. Redis may be explicitly reported as unused by the core scenario.

Every required latency metric needs measured samples and must meet its profile P95 threshold. Core service calls `run_input_acceptance` and `run_control_read` use the profile's 300 ms input-acceptance and 500 ms non-model-control thresholds; these are not HTTP API measurements. Hot/cold Context assembly, bounded Workspace operations and Provider Delta forwarding retain their profile thresholds. Accepted and terminal Run counts must reconcile, the measured platform error rate must remain below 1%, accepted-event and stream-event loss must both be zero, and latency histograms must not overflow. Slow Tool latency/payload and CPU Tool execution/concurrency require observations rather than assumed zero values.

Unmeasured G006 Session APIs and mixed product entry points do not fail G005 core qualification. Hostile scheduler fairness remains a separate G005 test requirement; passing that test does not claim fairness was measured within the long load. Neither core qualification nor startup diagnostics qualify full-platform or frontend responsiveness.

Intake diagnostics use real PostgreSQL with independent control/execution pools of 20, fifty concurrent starts and three rounds. Snapshot source preparation occurs before timing and dispatch wake is replaced by a recording callback, so this measures durable startup acceptance rather than Model execution or first-token latency. Source duplicates, SQL calls, Snapshot encoding, capacity cleanup and first/warm rounds are observed separately. Async SQL and connection timing include event-loop scheduling and do not isolate PostgreSQL server time.

Latency statistics use bounded histograms for the long test. Same-source retries must still return the original Run and consume no extra execution identity. Optimization cannot pass by returning success before commit or disabling read validation.

## Alternatives considered

Shortening the frozen profile and reporting qualification would misstate evidence. Allocating a 16 GiB VM on this 16 GiB host would not provide a credible isolated reference environment. The user explicitly declined that environment change; local diagnostic results remain useful without claiming formal qualification.

An unconditional failure result cannot distinguish a qualifying run from an incomplete measurement. Treating missing metrics as zero or using unavailable G006 APIs to reject core execution would also misstate the measured scope. Qualification instead checks the core observations and reports missing product surfaces separately.

## Consequences

The recorded earlier core load remains `not_qualified`: its source was still under development, storage and memory differ from the reference profile, and slow/CPU Tool observations are incomplete. Mixed product workloads remain unmeasured but are not a core failure reason. No new 18-minute load was run to validate the qualification-policy change, and no deployment resources were reconfigured.

## Verification

The startup fixture passed on both implementations. Warm-start P95 fell from 787.5/722.9 ms to 267.2/287.8 ms; cold-burst P95 changed from 680.8 to 579.9 ms. The comparison artifact states the exact scope. Hostile Runtime fairness tests cover 1 and 50 execution slots. The full working-tree backend suite passed 2681 tests with the opt-in long test skipped; existing deprecation warnings remain separate from failures.

The qualification-policy and driver checks passed 81 tests with the opt-in long test skipped. Synthetic complete reports prove only that the policy can return `qualified`; they are not measured load evidence. Negative cases reject missing metrics, threshold violations, inconsistent outcomes, loss, incorrect environment or duration, absent client lanes, and observed execution peaks of 1, 49 or more than 50. Ruff and diff checks passed for that change.
