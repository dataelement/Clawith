# Agent Note: Bound transient Model retries without suspending the Run family

Status: implemented — Run retries transient Model failures at most three times including the initial attempt.

## Problem

Model distinguished transient Provider failures from unrecoverable failures, but Run converted both into immediate failure. A single temporary network error could therefore terminate Main and cancel all its active Children.

## Decision

Model classifies transport failures, rate limits and Provider unavailability as transient. Run owns the retry policy: at most three attempts including the first, with asynchronous delays of 0.25 and 0.5 seconds before attempts two and three. Retry requires both a recognized transient code and `unrecoverable=False`. Authentication, configuration, malformed input, invalid protocols and unrecoverable continuation failures do not retry.

One private Run policy tuple holds the delays for the additional attempts. Both preparation and execution consume it, and the total attempt limit is one plus its length. Later tuning changes this single policy rather than scattered count and delay literals; it is not a model-selected option or a new configuration table.

Each execution retry uses the same captured Model policy, Credential reference, prepared request, step identity and input-consumption boundary. It never selects a fallback Model or replays an already settled Tool. Additional input arriving during a failed request remains unconsumed by that request. Failed streaming attempts are marked discarded through the presentation envelope; only the successful complete result can become an authoritative Model Step.

Summary and Token-count preparation failures use the same finite transient retry policy. A successfully generated summary is retained when a subsequent count fails, so preparation retry does not generate it again. Metadata Token counting is bounded preparation I/O, not a second content-generating execution step.

Exhaustion settles the affected Run as Failed. Main failure cancels its active Children through the existing family transaction. A Child failure returns through the existing Parent-result path. No new Waiting reason, paused family or explicit provider-recovery protocol is introduced. Database settlement retry remains separate and never repeats external Model or Tool execution.

## Alternatives considered

Pausing Main after retry exhaustion while preserving Children would require a recovery entry, clear handling of results arriving during suspension and additional operational policy. The user chose finite retry followed by the existing terminal behavior for the first release. Immediate failure was rejected because it discarded the Model owner's transient classification. Model fallback remains forbidden.

## Consequences

Transient recovery adds up to two attempts and their bounded backoff. Provider requests can still consume Tokens or incur charges even when a response is lost; local retries cannot promise exactly-once Provider execution. Tool side effects are not repeated as part of this retry. Service shutdown still interrupts unfinished work rather than resuming retries after restart.

## Verification

Engine tests cover transient recovery, exhaustion with Child termination, non-retryable failure, fixed requests and read boundaries, discarded streaming attempts and absence of Tool replay. Controlled HTTP tests cover status and structured-error classification. These tests do not establish live Provider availability or billing behavior.
