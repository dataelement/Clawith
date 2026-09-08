# Agent Note: Product Input, Main Run, and Output Boundaries

Status: proposed — the shared product-to-Run boundary and source-specific ownership model is agreed but not implemented

## Problem

Direct Session, including its lightweight Goal mode, and Group, Heartbeat, Trigger, and A2A all need to start or resume Agent work without moving their product facts into Agent Runner or a generic event bus. Each source has different input, result, and delivery semantics, while every target Agent should execute through the same Main Run boundary.

## Proposal

### Shared execution boundary

Every product capability records its authoritative input with one stable source identity, chooses the target Agent, decides start or resume, builds bounded Product Input, and calls Agent Runner. Retrying the same source identity returns the already-created Run or already-accepted related input rather than duplicating execution. Agent Runner creates or resumes the Main Run and returns the recorded Run Output to the initiating capability.

```text
Product capability
  - record authoritative input
  - choose target Agent
  - build Product Input
  - decide start or resume
          |
          v
      Agent Runner
          |
          v
        Main Run
          |
          v
       Run Output
          |
          v
Product capability
  - record product result
  - publish or deliver when required
```

Agent Runner receives only common execution facts and does not interpret Session, Group, Heartbeat, Trigger, A2A, Goal, Channel, or UI fields. Agent Loop never delivers product messages directly.

Input acceptance, user-message recording, Run execution, product result recording, external delivery, and delivery failure remain separate outcomes. The [unified user-message outlet](2026-09-09-user-messages-and-run-completion.md) owns visible communication; Final settles execution without an automatic second message.

Separate outcomes do not require separate commits when the owner shares PostgreSQL with Agent Runner. Run terminal Status and History plus the initiating owner's durable result record commit in one transaction through an in-process Outcome Consumer; failure rolls the terminal settlement back without re-executing Model or Tool. External transport delivery remains post-commit and separately retryable.

An accepted product input may have no Run when admission has not succeeded. The owner records and exposes its own pending, explicit admission failure, started Run relation, or retry behavior and reuses the stable source identity. It never silently drops the accepted fact or presents it as Running. Trigger, Heartbeat, A2A, Group, and Session use their own result and admission records rather than one universal handoff table.

### Source ownership

```text
Direct Session
  Human Input -> Session -> Main Run -> Session execution result
                             └─────> Session messages through the common outlet

Group
  Group event -> Group selects target Agent -> Main Run -> Group Reply or result

Heartbeat
  Agent heartbeat due -> Heartbeat -> Main Run -> Heartbeat execution record

Trigger
  Trigger occurrence -> Trigger -> Main Run -> Trigger execution result

A2A
  Agent Tool Call -> A2A request -> target Agent Main Run
  target Run Output -> correlated A2A Result Input -> source Main Run History

Goal mode
  Session Goal configuration -> same Main Agent in a new Main Run -> disposition -> Session Goal configuration

```

### Direct Session

Only authenticated human input creates direct Session Input. Session start, resume, fixed history cutoff, concurrent Reply, and Channel delivery semantics follow [Direct Session Input, History, and Concurrency](2026-08-27-direct-session-input-history-and-concurrency.md).

### Group

Group owns Group messages, events, target selection, public result recording, and Group delivery. One Group event may create independent Main Runs for explicitly selected or mentioned Agents. Those Runs use Agent and Group Workspace authorization and never import member User Workspaces automatically.

### Heartbeat and Trigger

Heartbeat is an Agent-level periodic autonomous check owned by Heartbeat configuration and scheduling. It is not a Trigger object and does not automatically create a Trigger. One due Heartbeat creates one new Main Run and records its result independently.

Trigger is an explicitly created future wake condition. Each time, interval, webhook, poll, message, or other accepted occurrence creates one new Main Run and one Trigger execution result. Trigger does not revive an earlier completed or interrupted Run.

Heartbeat and Trigger use Agent and Tenant connections by default. An authenticated Membership may explicitly bind selected Membership-Agent-Tool connection references to one Heartbeat or Trigger configuration. The product owner persists that exact delegation, supplies it to the resulting Main Run, and removes it on revocation; ownership alone never imports every Credential of the configuring Membership.

### A2A

A2A starts the receiving Agent's independent Main Run. It never creates a Subagent Run and never implicitly transfers the sender's authorization, Credential, or Context. An authenticated Membership may explicitly delegate selected Membership-Agent-Tool connection references to the target Agent for this A2A Request only; A2A persists those bounded references and the target cannot retain or reuse them. Every A2A Tool Call returns acceptance immediately. `notify` is one-way and does not resume the source Run. For `consult` and `task_delegate`, A2A owns the request relation, persists the target Run Output in the target terminal transaction, and holds one idempotent pending delivery until a correlated A2A Result Input is accepted by the exact non-terminal source Main Run. Waiting resumes; Running records the input for its next Model Step. Source termination changes the handoff to `source_terminal`, does not cancel the independent target Run, and a late result cannot revive the source. Detailed Tool behavior follows [Tool Registry, Execution, and Exposure](2026-08-27-tool-registry-execution-and-exposure.md).

### Goal mode

Goal mode is owned by direct Session rather than an independent product capability. A Session stores at most one active lightweight Goal configuration: enabled state, original objective, committed progress, current wait condition, and relation to the existing `/goal` Session Input. It uses existing Session persistence and adds no Goal table, ID, domain object, status state machine, Agent role, Run type, Reply type, projection type, or history.

Every iteration executes the Session's same Main Agent through a new ordinary Main Run related to the original `/goal` input and cutoff. Product Input carries a bounded snapshot of the objective, committed progress, preceding disposition or execution outcome, and satisfied wake condition; it never inherits the previous Run History implicitly. Session consumes terminal iteration outputs `continue` and `wait` internally: `continue` starts the next Run immediately, while `wait` stores a future condition and starts the next Run only after it is satisfied. Goal has no `require_user` disposition: ordinary Need Input leaves the current Run Waiting and a related human reply resumes it. Goal result messages use the common outlet and original `/goal` input relation; terminal dispositions do not automatically send a reply.

A failed or interrupted iteration remains terminal. The [failure and crash decision](2026-09-09-goal-failure-and-crash-boundary.md) stops automatic Goal continuation after Failed and does not recover interrupted work after restart. Session retains committed progress without creating another iteration to bypass Model retry exhaustion. Achieved or user cancellation disables Goal mode; cancellation also cancels its active Main Run and descendants.

### No shared product event bus

The product capabilities share Agent Runner's start, resume, cancel, status, and outcome contracts but do not share one generic Product Event model. Each capability keeps its own authoritative input, result, projection, and delivery semantics and supplies only bounded Product Input to Context.

## Alternatives considered

### Route every input through Session

Most product events are not human-authored direct conversation. This would turn Session into a shared lifecycle and delivery bus.

### Let Agent Runner interpret every product source

Agent Runner would accumulate Channel, Group, Trigger, Heartbeat, A2A, and UI policy and stop being a generic execution boundary.

### Create one universal product event schema

The sources do not share the same actors, acceptance, result, retry, projection, or delivery semantics. A universal envelope would become a broad optional-field protocol and a second owner for source facts.

### Let Agent Loop deliver outputs directly

Agent Loop does not know whether one output is a Session Reply, Group message, Heartbeat record, Trigger result, A2A response, or no external delivery. The initiating product capability owns that decision.

## Acceptance criteria

- Every product capability records its authoritative input before requesting execution.
- Every product source supplies stable identity so Agent Runner start and related-input submission are idempotent under transport or callback retry.
- Product capabilities use one Agent Runner start or resume boundary and all external or autonomous inputs enter Main Runs.
- Agent Runner and Agent Loop do not interpret product-specific fields or deliver product messages.
- Run Output returns first to the initiating product capability.
- Input acceptance, Run completion, product recording, and external delivery remain separate outcomes.
- Run terminal settlement and same-database product result recording commit atomically; external delivery remains independent, and product owners durably represent input admission that has not produced a Run.
- Direct Session owns Goal-mode continuation; Group, Heartbeat, Trigger, and A2A retain their own input, result, projection, and delivery ownership.
- Group, Heartbeat, Trigger, A2A, and automatic Goal continuation do not create direct Session Input; the human `/goal` command does.
- Heartbeat remains independent from Trigger and does not automatically create one.
- Each Trigger occurrence creates a new Main Run rather than reviving a terminal Run.
- A2A creates an independent target Main Run and transfers only explicit Input content.
- A2A Tool Calls settle immediately; `consult` and `task_delegate` submit correlated A2A Result Input to the exact non-terminal source Main Run rather than holding an open Tool Call.
- A2A target outcome and result record commit together, and A2A owner retries only pending idempotent source delivery without replaying either Run.
- Source termination does not cancel the independent A2A target Run, and late output cannot revive a terminal source Run.
- Session stores at most one lightweight Goal-mode configuration without adding a Goal table, ID, domain object, or status state machine.
- Goal mode applies Main Agent disposition across new ordinary Main Runs using the original `/goal` Session relation and committed continuation facts rather than creating per-iteration Session Input, recovering terminal Runs, or inheriting earlier Run History.
- Goal `wait` completes the current Run and delays a new Run until its condition is satisfied; ordinary Need Input uses Run Status Waiting and resumes the same Run.
- Goal mode adds no Need Input, Reply, or projection type; it reuses ordinary Waiting/Resume and Agent Reply semantics.
- No generic Product Event bus becomes a second authority for product facts.

## Risks and open questions

Exact Product Input and result fields, target-selection APIs, schedule and occurrence formats, delivery adapters, retry rules, and UI projections remain implementation decisions owned by each product capability.
