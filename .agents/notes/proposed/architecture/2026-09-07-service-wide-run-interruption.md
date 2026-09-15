# Agent Note: First-release service-wide Run interruption

Status: proposed — the user approved this lifecycle rule; G005 Run services must implement and verify it.

## Problem

Preserving Waiting while interrupting its Running Child conflicts with immediate Child-result delivery: that delivery would wake the Main during shutdown. The first release needs one explicit operational outcome without introducing delayed-resume machinery.

## Proposal

Service-wide shutdown and startup cleanup mark every remaining Running or Waiting Main/Subagent Run Interrupted. Each affected family settles Parent-first without normal Child-result wakeup or Cancelled propagation. Already terminal outcomes remain unchanged. Abrupt termination leaves uncommitted interruption bookkeeping to the next startup sweep, which finishes before readiness.

Committed Snapshot, History and Context source data remain available. Restart does not resume old execution; future work uses a new Run and explicit committed context. Normal in-service Waiting/resume and ordinary per-Run Parent cancellation semantics do not change.

This supersedes only the restart-preservation clauses of the [Runner design](2026-08-27-agent-runner-lifecycle-and-history.md). The reviewed implementation boundary is [G005 Core Runtime](../../../../specs/backend-core-runtime.md).

## Alternatives considered

Preserving Waiting could reuse its committed history because no Model or Tool operation is active in that Run. However, a still-Running Child requires special handling at shutdown and restart. The user deferred this operational policy and chose uniform interruption for the first release.

Delayed Child-result wakeup could also preserve Main without replaying Child, but adds a lifecycle exception that is unnecessary under the chosen policy. A future preservation or drain policy can be added explicitly at Runner's lifecycle boundary; distributed takeover or in-flight checkpoint recovery remains a separate architecture decision.

## Verification required

Exercise graceful stop and abrupt-loss startup with Main/Child combinations of Running, Waiting and terminal states. Verify Parent-first atomic interruption, retained History/Snapshot, no wakeup or replay, drained operations, released capacity and readiness only after cleanup. No such implementation or test completion is claimed by this decision record.
