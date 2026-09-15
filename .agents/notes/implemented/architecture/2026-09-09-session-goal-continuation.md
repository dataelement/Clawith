# Agent Note: Session-owned Goal continuation

Status: implemented — Session configuration, transactional outcomes and application-owned continuation use ordinary Main Runs.

## Problem

A Goal needs progress and continuation across ordinary Main Runs without reopening terminated execution or creating a Task/Goal state machine. Continuation must preserve its original human input and history cutoff, and a malformed Model result must not create an infinite retry or iteration loop.

## Decision

Session's existing Goal fields hold the enabled flag, original input and a bounded version-1 configuration containing objective, committed progress, fixed history cutoff, current Session Run association, due time, scheduling time and stop reason. There is no independent Goal ID or table. Human operations require the Session's Membership and captured Agent scope. Goal is enabled before startup of its original input; an existing active Goal must be cancelled before replacement.

For an active Goal's current Main, OutcomeConsumer accepts a control object such as `{"goal":{"disposition":"continue","progress":"Sources reviewed","wake_at":null}}`. The other dispositions are `wait`, which requires an aware future ISO timestamp instead of null, and `achieved`. `continue` schedules another ordinary Main; `wait` schedules at its explicit future time; `achieved` disables continuation. Missing human information uses ordinary Need Input on the existing Run and does not consume a terminal Goal disposition.

Terminal Run settlement, Goal progress and the next pending association commit in one caller-owned transaction. The next association retains the original input and cutoff and uses an owner-generated source key derived from that input and the preceding Run. Complete Model output remains in Run History. Final does not send a message. Failed, Interrupted or Cancelled stops continuation. Invalid, oversized or unpersistable Goal dispositions explicitly disable the Goal as `malformed_goal_result` without changing a completed Run into an invented failure or blocking terminal settlement forever.

Goal scheduling times are canonical UTC strings. The due scan requires the application startup time as `not_before` and filters the actual Goal `scheduled_at`, never the Session's general update time. A later chat message therefore cannot reactivate an old pending iteration. Scans use bounded pages. A short Session-owned admission preparation verifies the expected current association and due time, then clears the due marker. Only after that commit may application composition start the new Run. Repeated preparation does not dispatch another copy; a crash after preparation does not automatically replay the pending association.

Admission failure marks the unstarted association failed and disables continuation rather than leaving an enabled unscheduled Goal. Domain and persistence failures after admission preparation both attempt this settlement; a continuing database outage can prevent that write and is reported rather than retried as execution recovery. A committed started Run is not overwritten by a late failure report. Cancellation disables future continuation and exposes the current Main for ordinary Run-family cancellation; callers keep the Run-before-Session lock order. If admission changes during cancellation, the operation reports conflict for a fresh retry rather than overlooking a newly started Run. Startup consumers reject cancelled or obsolete Goal associations.

Goal parsing applies only to the associated input. An unsupported or malformed Goal cannot block unrelated ordinary inputs, admissions or terminal outcomes in the same Session. Due scans report invalid Session identities and advance the bounded cursor, so one oversized or unsupported configuration does not prevent other Sessions from dispatching. Direct Goal reads still reject invalid persisted configuration; the scan does not repair or reinterpret it.

Autonomous dispatch reads the configured original input and explicit Membership/Agent relationship through a trusted Goal context port, not a fabricated online principal. Application composition resolves current Agent capabilities and the explicit Membership Workspace. Personal account delegation is not implicitly inherited from every user account.

## Alternatives considered

A new Goal or Task state machine would duplicate Session configuration and Run lifecycle. Continuing failed or interrupted execution would reintroduce recovery that the first release rejects. Using Session `updated_at` for restart eligibility would let unrelated messages restart old work. An unqualified `wait` with no wake condition would leave an enabled Goal suspended indefinitely; human questions use Need Input instead. Repeatedly retrying malformed terminal output cannot repair a result that has already been produced, so the Goal stops explicitly while the actual Run result remains intact.

## Consequences

The application owns a bounded cancellable polling task and supplies its startup boundary; Session owns eligibility and source correlation. Pending admission, active execution and Goal configuration remain distinct facts. Explicit retries and new Goals are possible, but no generic replay queue, lease, replacement Agent or automatic recovery is added. Product prompts must request the Goal Final structure and use the normal message Tool for user-visible replies.

## Verification

Real PostgreSQL tests cover atomic terminal/progress/next-association updates and rollback, preserved original input/cutoff, single admission preparation, future waits, startup-boundary exclusion despite unrelated messages, achieved/failure/interruption/cancellation stops, malformed and null-character results, failed admission, cancelled pending startup, client-key collision prevention and invalid-configuration isolation. Application tests exercise continue creating a new Main, achieved stopping it and three failed Provider attempts stopping continuation. These tests use controlled external Model responses; they do not establish live autonomous task success or full G006 acceptance.
