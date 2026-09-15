# Agent Note: Stop Goal continuation after execution failure

Status: proposed — user-confirmed G006 behavior; Session implementation and verification remain pending.

## Problem

Starting another Goal iteration after Model retries are exhausted would bypass the finite Run retry policy. Platform process loss is a different failure and must not expand the first release into a recovery system.

## Proposal

While the platform remains operational, transient Model and network errors use Run's same-model retry policy: three attempts including the initial call, then Failed. Keep retry count and backoff in one Run-owned policy location so later tuning does not change architecture. Authentication and other unrecoverable errors remain non-retryable. No Model fallback or work-Tool replay is introduced.

Session stops automatic Goal continuation when its current Main Run becomes Failed. It preserves committed progress and the failure outcome and does not start another iteration to retry. Main termination retains the existing active-Child cancellation rule. Any user-facing failure notification uses the common message outlet and identifies platform failure rather than fabricating a model answer.

Platform crash or service shutdown does not trigger automatic recovery of interrupted work. Shutdown or next-start cleanup marks unfinished Runs Interrupted and retains committed data; Goal does not automatically resume that interrupted work. There is no missed-occurrence catch-up or new recovery scheduler. Ordinary future scheduling of enabled Trigger/Heartbeat configurations remains separate from recovery of old work.

This narrows the earlier permission to start a new Goal iteration after Failed or Interrupted in the Main/Task and Product Input proposals. Ordinary successful `continue`, future-condition `wait`, achieved, cancellation and Need Input retain their existing meanings. Human login expiry is not an execution failure.

## Alternatives considered

Automatically creating a new Main after retry exhaustion was rejected because it would defeat the retry limit. Cross-restart recovery was explicitly deferred by the user. Increasing the initial retry count remains possible later; three attempts is the selected initial policy, not an architectural limit.

## Verification required

Verify that retry exhaustion records Failed and stops Goal continuation, that no extra Main is created, and that terminal settlement retry does not repeat work. Verify that Interrupted Goal work is not restarted during startup, while committed progress remains readable. No implementation or formal load acceptance is claimed here.
