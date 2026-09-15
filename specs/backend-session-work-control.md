# G006 Session input and work-control contract

Status: user-confirmed design; implementation preflight, owner-ledger binding and product acceptance are pending. This document does not approve all G006 product services or declare them implemented.

## Scope and authority

This slice extends direct Session input handling with conversational discovery, supplementation and cancellation of existing work. The [Session owning Note](../.agents/notes/proposed/architecture/2026-08-27-direct-session-input-history-and-concurrency.md#conversational-work-control) defines the behavior and rationale. [Core Runtime](backend-core-runtime.md) retains Run lifecycle, input ordering, Snapshot, parent-child and transaction ownership. G003–G005 approval artifacts and receipts remain historical bindings and are not rewritten by this design confirmation.

Ordinary input starts a new Main Run of the same Main Agent. An explicit reply to a Waiting Main Run still resumes that exact Run. The newly started Main may act on the new requirement or explicitly address existing same-Session work; the target's original Main remains responsible for its Children and final result. The user interacts through conversation, without a required task-creation or Run-selection workflow.

## Implementation boundary

Session owns human input and execution associations, scoped work discovery and authorization of work-control requests. Tool owns Main-only exposure and execution contracts; application adapters inject explicit Session services. Run alone appends related input and applies lifecycle transitions. Context consumes recorded, source-labelled observations. No cross-owner private ORM access, generic service locator, new Task object, cross-Run recovery, Child reassignment or implicit authorization expansion is introduced.

The implementation must define typed operation results, bounded discovery/detail shapes, source correlation, failure semantics and real API/Tool entry paths before wiring consumers. Exact names, fields and bounds may be selected within the owning behavior. Further changes to start/resume, authorization or parent-child semantics require their own reviewed decision.

## Required acceptance scenarios

- Independent messages create independent Main Runs in one Session without waiting for previous work; explicit Waiting replies still resume the named Run.
- A new Main receives bounded, attributed work information, selects an active target and submits a supplement through the actual Tool and Session service. The original Main consumes it without transferring Child ownership or restarting the work.
- An accepted supplement acknowledges its commit before execution finishes. Duplicate delivery creates no second input or side effect. Running and Waiting targets follow their existing distinct consumption paths.
- A supplement racing completion either commits under Run's unseen-input protection or reports the terminal target; it never silently disappears or revives a terminal Run.
- Cross-Tenant, cross-Session, wrong-Agent and Subagent callers cannot inspect or control work through this surface. A guessed Run ID does not bypass Session association checks.
- Cancelling the selected Main settles its active Children and leaves unrelated work intact. Repeated cancellation and concurrent completion return truthful outcomes without duplicate terminal facts or external-effect rollback claims.
- The addressing Main can finish after acknowledgement while the independent target continues. Each visible Session reply retains its originating input relation; Final does not automatically send another reply.
- Ambiguous-reference conversation tests ask for clarification; completed-target tests continue from authorized committed artifacts rather than reopening old execution. Real-model task evaluations remain separate from deterministic service tests.
- Existing early-Main-completion and service-wide interruption behavior remains unchanged: no Child-completion gate, new durable scheduler or automatic recovery is added.

## Evidence and handoff

Design references support the components rather than certify this exact combination: [OpenAI manager orchestration](https://openai.github.io/openai-agents-python/multi_agent/), [Temporal execution message passing](https://docs.temporal.io/develop/python/workflows/message-passing) and [parent-close policy](https://docs.temporal.io/parent-close-policy). The inspected local Grok Bot 0.18 reconstruction supplies a conversational background-control example; its turn and Subagent identities are not Clawith Run contracts, and its implementation is not an official upstream guarantee.

The implementing session must perform preflight review and bind any affected owner-contract amendments with the existing receipt workflow before claiming approval of implementation contracts. Passing document or ledger-structure checks does not establish feature execution. G006 still requires its real product-input API/WebSocket and cumulative gates; this slice does not settle the remaining Group, Trigger, Heartbeat, A2A or Channel product details.

## User-message and completion amendment

The user-confirmed [message/completion decision](../.agents/notes/proposed/architecture/2026-09-09-user-messages-and-run-completion.md) uses a Main-only message Tool and keeps Final as execution settlement only. It supersedes automatic Final-to-Session-Reply behavior while retaining atomic Run/owner result settlement. Need Input commits question, Waiting and reply association together before publication. G006 implementation must cover multiple messages per Run, idempotent acceptance, no duplicate final reply, source attribution, message/terminal crash ordering and independent Channel delivery outcomes before claiming this extension complete.

## Login and failure policy

Product login follows the [fixed 24-hour policy](../.agents/notes/proposed/architecture/2026-09-06-login-session-authorization.md): no sliding expiry or automatic renewal, expiry closes human access/WebSockets without cancelling Runs, and a newly authenticated authorized user may resume an existing Waiting Run. Autonomous Agent operation does not require an online human.

Goal follows [failure and crash boundaries](../.agents/notes/proposed/architecture/2026-09-09-goal-failure-and-crash-boundary.md). Model retry exhaustion stops automatic Goal continuation; no new iteration bypasses the retry limit. Interrupted work is not automatically recovered after platform restart. This decision does not authorize work on the deferred G005 performance driver.

## First implementation slice

Implement Session persistence and direct Main message/wait/result association before work-control Tools, Goal or other input owners. Human input and a pending admission relation commit before startup. A narrow Run-owned start consumer records the Session-to-Run association inside the same transaction as Run, Snapshot and initial History; only committed startup is scheduled. A fast Model or message Tool cannot execute before that association exists. Stable Session source identity deduplicates admission retries without creating a second Run.

If the process stops after input/pending admission commits but before a Run exists, the input remains visibly unstarted, not Running. Startup does not automatically execute that old pending input. An explicit retry uses the same source correlation. Capacity rejection records an explicit admission failure without deleting the input; tests must cover rejection, the input/start crash window and same-source retry independently from interruption of an existing Run.

A Run-owned waiting consumer records the Session question and wait reference in the same transaction as the Main's actual Waiting transition and Tool settlement. It is invoked only for a committed Main human-question wait, not Child waits, task-result waits, duplicate waiting records or a wait rejected because unseen input is available. Consumer failure rolls back the whole settlement without replaying the produced Tool operation. Waiting is not a terminal OutcomeConsumer event.

Session message acceptance stores the source Run, Model step, Tool Call and originating human input relation with a unique source correlation in existing Session persistence. A retry returns the existing accepted message without rewriting its body. Message commit precedes its success result; a crash between message commit and Run Tool Result leaves the message intact and the old Run is interrupted on startup, without invented Tool Results or repeated sends. Final persists only the Session-owned execution outcome through the existing terminal consumer.

Run-to-Session callbacks use the caller's transaction and never open a nested transaction or perform Channel I/O. Operations that need both locks acquire Run before Session; input recording does not hold a Session lock while starting or resuming Run. Model execution and external delivery remain outside these short transactions. Persistent Session message positions support reconnect reads; in-process notifications are only an acceleration.

Before service implementation, bind reviewed Session and Run amendments for the typed ports and Session source-correlation constraints. Verify real PostgreSQL/Runtime paths for fast startup, duplicate messages, message-commit/Tool-result crash separation, waiting rollback, unseen-input suppression and Final without another reply. Then add Auth HTTP/WebSocket entry, fixed login expiry and bounded reconnection. This slice does not claim completed work-control, Goal, Channel delivery or full G006 acceptance.
