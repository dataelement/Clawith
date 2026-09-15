# Agent Note: Session, Main Agent, Task, and Agent Loop Model

Status: proposed — the conceptual model is agreed as the basis for implementation planning but is not implemented

## Problem

The target architecture must keep the human conversation responsive while independent delegated work proceeds concurrently. It needs one execution model for Main Agents and Subagents, a Task Tool that lets the Main Agent delegate without turning Task into another state machine, and clear ownership outside Agent Loop for product behavior, Context, models, Tools, Memory, and Workspace.

The conceptual model must not use Session, Run, Task, Goal, or an implementation checkpoint identity for the same concept. Persistence mechanisms, schemas, APIs, and migration remain implementation concerns until the boundaries are complete.

## Proposal

### Session and Main Run

A direct Session belongs to one User and one Main Agent. User means one Tenant Membership under [Account, Membership, Tenant, and Principal](2026-08-31-account-membership-tenant-principal.md), not a global Account. A User may have multiple Sessions. Session is the human-facing conversation containing human inputs, Main Agent replies, visible delegated-work progress, and completed results. Its detailed input, history-cutoff, concurrency, reply, and delivery boundaries are defined by [Direct Session Input, History, and Concurrency](2026-08-27-direct-session-input-history-and-concurrency.md).

Each Session Input may create a Main Run. One Main Agent may have multiple concurrent Main Runs in one Session. A running or waiting Main Run does not hold an exclusive Main Agent or Session lock, so later input can start another Main Run without waiting.

### Task Tool and Subagent Runs

A Main Run owns the human conversation, intent understanding, direct execution, delegation, result synthesis, requests for human input, and completion judgment. It may perform work directly or submit zero or more delegated work descriptions through Task Tool. Whether the current Product Input specifies a work method and whether unspecified work is complex enough to delegate are model decisions expressed through Main-role guidance and Task Tool Description. The architecture does not define that Prompt interpretation or complexity threshold, and Agent Loop does not force Task Tool use.

Task Tool is part of the Main Run's directly exposed core Tool set. Subagent Runs are leaf executions: they do not receive or discover Task Tool and cannot recursively create Tasks or Subagent Runs. A Subagent that needs further decomposition reports that need to the responsible Main Run.

Subagent Runs own concrete delegated execution. They receive a Run-scoped Todo Tool for planning and tracking their own steps, use the authorized work Tools and Workspaces, verify outputs, and return a complete Run Result. Main Runs do not receive Todo Tool; their delegated-work view is derived from Task Tool Calls, Child Run facts, and correlated Child Results.

Task Tool always creates Child Runs for the same Agent that executes the responsible Main Run. It accepts delegated work descriptions but no target Agent selection. The Child therefore keeps the same Agent Identity, Soul, and Agent Workspace while inheriting the Parent Run's resolved authorization. Calling another Agent is A2A: the target Agent receives an independent Main Run, resolves its own authorization and Workspace, and may use its own Task Tool to create same-Agent Child Runs.

Task is a delegated work description submitted by Main Agent through Task Tool in the current Main Run. It is analogous to a Run-scoped Todo item but has the side effect of starting a Child Run. Task has no independent table, ID, persistent record, planner, controller, status, transition loop, Workspace, completion judge, cross-Run lifecycle, or result object.

One Task Tool Call submits one or more delegated work descriptions and starts one Subagent Run for each accepted description. Child creation is idempotent from the existing Parent Run, Tool Call, and assignment correlation, without a Task ID. The Tool Call returns acceptance and Child Run references without waiting for execution. Each work description becomes its Child Run Input. A later Task Tool Call appends new delegated work and starts new Child Runs; it does not update or reopen an earlier Task object because no such object exists.

```text
Main Run
  |
  | Task Tool(one or more delegated work descriptions)
  v
accepted + Child Run references
  |
  v
Main Run Running or Waiting
  |
  +---- Child Result / Need Input ----> append correlated Child Input
                                             |
                                             +---- enough result ---------> synthesize Main output
                                             |
                                             +---- more work needed -------> another Task Tool Call
```

The responsible Main Run alone decides whether the user or product requirement is satisfied and whether another Subagent Run is needed. No Task object makes that decision or produces a separate result.

Different delegated work descriptions may proceed independently. Their Child Runs and Results do not block one another.

Delegation may proceed through multiple Task Tool Calls in one Main Run. For example, one call may start two Subagents that gather independent source material. After their Results arrive, Main may make another Task Tool Call whose new work description explicitly includes the earlier Results and asks a new Subagent to compare or synthesize them. The calls share Main Run context but do not form a persistent multi-wave Task.

#### Task A execution example

Suppose the delegated requirement is: research the United States and China markets, compare them, and produce one report.

```text
Main Run
  |
  | Task Tool Call 1
  |-- A1: research the United States market
  `-- A2: research the China market
        |
        +---- accepted + A1/A2 Run references

A1 Result ---- Child Result Input ----> Main Run
A2 Result ---- Child Result Input ----> Main Run
                                  |
                                  | Task Tool Call 2
                                  `-- A3 Input: compare A1 and A2 and write the report
                                        |
A3 Result -------- Child Result Input --------> Main Run
                                          |
                                          `-- judge requirement satisfied and produce Main output
```

A1 and A2 may run concurrently. Their Results enter Main Run History as ordered correlated Child Inputs and resume Main only when it is Waiting. Main does not perform the remaining comparison itself. Once the required source Results are available, Main explicitly passes them in a new Task Tool Call that starts A3. A3 performs the comparison and report writing. Main then judges whether the overall requirement is satisfied; if not, it may submit another work description that starts A4. The continuity comes from Main Run History, not a Task record.

### One Agent Loop

Every Agent uses the same basic model-and-Tool loop. Main Agents and Subagents differ by their role in the current execution, Run Input, Context, available Tools, and result destination. They do not use separate execution engines.

```text
Main Run
  Run Input from product capability
    |
    v
  Context -> Model -> Tool System -> Context
                    |
                    +---- Task Tool ----> Subagent Run
    |
    +---- Need Input ----> Waiting
    |
    +---- Final Output --> Run Completed
```

```text
Subagent Run
  Task description as Run Input
    |
    v
  Context -> Model -> Tool System -> Context
    |
    +---- Need Input ----> Waiting ---- correlated event ----> responsible Main Run
    |
    +---- Final Output --> Run Completed ----> Run Result ----> responsible Main Run
```

Run Input states what one Run must do. Context states what the model needs to know for the current call. Agent Loop produces Run Output, and Agent Runner records it. Main Run Output returns to its initiating product capability; Subagent Run Output reaches the responsible Main Run as a correlated Child Result Input because the originating Task Tool Call has already settled with acceptance. Agent Loop does not deliver product messages or independently judge whether the parent requirement is complete.

### Context and compaction

Context is an independent module called from Agent Loop. Its macro source categories, ownership, and assembly boundaries are defined by [Context Source and Assembly Model](2026-08-28-context-source-and-assembly-model.md). Context owns the resulting model input and source attribution; it does not own source facts.

Compaction is an internal Context operation. It changes the next model view without deleting original Run History, Session facts, Task Tool Calls, Child Run facts, Memory, Skills, or Files.

### Product, Model, Tool, and Workspace boundaries

Model System, Tool System, Context, and Workspace remain outside Agent Loop.

Model System owns model selection, Provider integration, credentials, resolved request parameters, and model-level execution policy. Agent Loop submits model input and consumes model output.

Tool System owns Tool registration, authorization, exposure, scheduling, and execution. The detailed target contract is defined by [Tool Registry, Execution, and Exposure](2026-08-27-tool-registry-execution-and-exposure.md).

User, Agent, and Group Workspace ownership and progressive Memory and Skill loading are defined by [User, Agent, and Group Workspaces](2026-08-27-user-agent-group-workspaces.md). Task has no Workspace. A Subagent Run inherits the complete resolved authorization and Workspace access of its parent Main Run. This grants the same ability to search, read, write, and use Tools without copying the parent Run History into Subagent Context.

Human messages and Goal continuation enter through Session; Group events, Heartbeats, Triggers, A2A requests, and other product inputs enter through their respective owners. All create or resume only Main Runs. A Subagent Run can start only through Task Tool from a Main Run. No external or product input enters a Subagent Run directly.

A2A is a separate trust boundary. The receiving Agent executes an independent Main Run with its own resolved authorization and Workspace plus only the text, file, Artifact, or other content explicitly carried by the A2A Input. It does not inherit the sender's User or Group Workspace, Agent Workspace, Tools, credentials, Run History, or implicit Context.

An Agent-calling Tool Call returns acceptance immediately. `notify` is one-way. For `consult` and `task_delegate`, the A2A capability submits a correlated A2A Result Input after the target Main Run completes. The input resumes the exact source Main Run when Waiting or enters its next Model Step when Running. The target is independent: source termination does not cancel it, and its late result cannot revive a terminal source Run.

### Agent Runner

One lightweight Agent Runner is the shared execution boundary for Main Runs and Subagent Runs. Product capabilities initiate Main Runs. The Task Tool Executor requests Subagent Runs on behalf of the responsible Main Run. Agent Runner remains the only Run creator, owns Run Status and isolated Run History, and routes each Child outcome to the responsible Main Run as a correlated Child Result Input.

The detailed lifecycle contract is defined by [Agent Runner Lifecycle and Run History](2026-08-27-agent-runner-lifecycle-and-history.md). Agent Runner does not interpret Task descriptions, judge completion, or own Session, Context, Model, Tool, Workspace, or product facts.

### User-visible messages

The [unified user-message outlet](2026-09-09-user-messages-and-run-completion.md) uses a Main-only message Tool and separates all Main-authored chat messages from Final settlement. Main may communicate repeatedly during execution; Need Input commits its question and Waiting relation together. Final records the execution result without automatically sending another reply. Subagent Final remains a result for Parent, not a direct user message.

### Waiting, completion, and cancellation

Waiting pauses only the corresponding Run and releases its current execution resources. A Main Run waiting for Subagent Run Results does not block Session, another Main Run, or unrelated delegated work.

A Subagent missing human or product input enters Waiting and preserves its Run History. Agent Runner commits that Waiting transition and one correlated Child Need Input to the responsible Main Run atomically. The input identifies the exact Child Run and requested information but does not complete the Child or judge the parent requirement. It resumes Main if Waiting or remains ordered for its next Model Step if Running. If Main is already terminal, the same transaction cancels Child rather than leaving an unreachable Waiting Run.

The Main Agent first decides whether its own Context can answer. If so, it uses Task Tool to resume the same Waiting Child Run with the answer. Otherwise the Main Run requests input through its owning product capability and enters Waiting. Explicit human input resumes the Main Run, which then resumes the exact Child through Task Tool. Task Tool returns acceptance immediately, Main waits for the next Child Input, and the Child continues with its original Run Input and preserved History.

Run Completion means one Agent Loop execution ended. Main Agent's judgment that delegated work satisfies the parent requirement is model behavior, not Task completion state or another platform verification stage.

Todo is a Subagent Run planning aid, not another lifecycle owner or completion gate. It creates no Task or Run, grants no permission, survives only within the current Subagent Run, and does not prevent Final Output. If Todo items remain unresolved, the Subagent reports them in Run Result for Main Agent judgment.

During ordinary per-Run settlement, if a responsible Main Run becomes Completed, Failed, Cancelled, or Interrupted, Agent Runner cancels the still-active Subagent Runs created by that Main Run. Service-wide shutdown/startup cleanup instead marks every non-terminal Main/Subagent Run Interrupted, including Waiting, without waking a Parent. Main Completion does not wait for Child completion; a premature Final Output is accepted as an execution mistake rather than introducing a completion gate. A late Subagent outcome may remain in Run History but cannot revive the parent Run or continue the ended work.

The Agent Loop has no mandatory generic Verify stage. Agents verify through ordinary test, inspection, review, query, or other Tools before Final Output. Trust-boundary validation remains with its owning module.

### Goal mode

`/goal` enables a cross-Run continuation policy for the Session's existing Main Agent. The Session stores one lightweight Goal-mode configuration containing whether the mode is enabled, the original objective, committed progress, any current wait condition, and a relation to the existing `/goal` Session Input. A Session has at most one active Goal mode. This configuration is part of Session persistence; it does not create a Goal table, Goal ID, Goal domain object, Goal status state machine, Goal-specific Agent, Goal Run type, Runner, or Agent Loop.

```text
/goal objective
  |
  v
Main Run A ---- Task Tool ---- Subagent Runs ---- disposition
  |
  v
Main Run B ---- Task Tool ---- Subagent Runs ---- disposition
  |
  v
Main Run C ------------------------------ objective achieved
```

Before a Goal-mode Main Run finishes, the Main Agent explicitly declares one disposition: achieved, continue, or wait, together with any progress that should survive the Run. These dispositions are Run Output instructions rather than durable Goal statuses. Session applies them but does not plan work or judge completion independently: `continue` completes the current Run, updates committed progress, and starts a new ordinary Main Run without creating Session Input or Agent Reply; `wait` completes the current Run, stores committed progress and a future wake condition, and starts a new ordinary Main Run only after that condition is satisfied; `achieved` completes the current Run and disables Goal mode. Visible Goal result messages use the unified outlet; no disposition automatically creates a reply.

Goal mode has no `require_user` disposition. When the current Goal Main Run cannot proceed without human information or a user-only action, it uses the ordinary Agent Loop Need Input path and remains Waiting. An explicitly related human Session Input resumes the same Run with its existing History and Context snapshot. Prompt guidance should make Need Input a last resort after the Agent exhausts authorized Context, Tools, reasonable reversible choices, and alternative paths; that triggering policy is model behavior rather than another Goal lifecycle contract.

Goal `wait` is not Run Status Waiting. Need Input preserves and later resumes the same Run because required information is missing. Goal `wait` ends an otherwise complete iteration and discards its execution context after committed progress is captured; the future wake starts a new Run. Exact wake-condition representation and scheduling remain implementation decisions.

Each later Main Run relates to the original `/goal` Session Input and receives bounded Product Input containing the original objective, committed progress, the preceding disposition or execution outcome, and the satisfied wake condition when applicable. It reuses the original Session-history cutoff and does not resume or implicitly read the complete History of an earlier Main Run. Durable files, Memory, and other committed artifacts remain available through their normal Context sources.

A Failed or Interrupted Goal-mode Main Run remains terminal. Ordinary per-Run termination cancels its active Subagent Runs; service-wide cleanup interrupts all non-terminal members instead. The [failure and crash decision](2026-09-09-goal-failure-and-crash-boundary.md) stops automatic Goal continuation after Failed and does not recover interrupted work after restart. Session preserves committed progress and the failure outcome; any user-facing notification uses the unified outlet related to the original `/goal` input. A new iteration cannot bypass the Run's finite Model retry policy.

Cancelling Goal mode disables the Session configuration, stops further continuation, and cancels the active Main Run and its descendants. Goal continuation facts remain Session-owned product facts rather than Workspace Memory or Runner lifecycle state.

### Vocabulary

- **Session:** The human-facing direct conversation, its Inputs, Replies, and visible work projections.
- **Main Agent:** The Agent executing a root Main Run and coordinating its product input and delegated work.
- **Main Run:** A root Run initiated by the owner of human, Group, Heartbeat, Trigger, A2A, Goal-mode, or another product input.
- **Task:** One Run-scoped delegated work description submitted through Task Tool; not a persistent object, ID, status, state machine, or execution engine.
- **Task Tool:** The Main-only Tool that accepts one or more delegated work descriptions and starts one Child Run for each accepted description.
- **Subagent:** A Child Run in which the same Agent as the Parent Main Run executes delegated Task work.
- **Subagent Run:** A leaf Run created through Task Tool with the Task description as Run Input; it cannot invoke Task Tool recursively.
- **Todo Tool:** A Run-scoped Subagent planning Tool for recording execution steps; not a Task, state machine, permission owner, or completion gate.
- **Run Result:** The outcome emitted by one Subagent Run and delivered to the responsible Main Run as a correlated Child Result Input.
- **Goal mode:** One lightweight Session configuration and continuation policy that repeatedly starts ordinary Main Runs for the same Main Agent; it has no independent table, ID, domain object, status state machine, Agent, or Run type.
- **Agent Runner:** The lightweight execution boundary that creates Runs and owns Run lifecycle and isolated Run History.
- **Context:** The traceable model input assembled for one model call from authorized sources.
- **Agent Loop:** The shared Context, model, Tool, wait, and final-output loop used by every Agent.

Thread is not part of this product model. An implementation checkpoint identity must not become a synonym for Session, Task, Goal, or Run.

## Alternatives considered

### Force every user request through Task Tool

This adds delegation overhead to work Main Agent can answer or execute directly. Task Tool remains available for isolated execution, while Main-role guidance and its current Product Input determine whether the model uses it.

### Allow accepted Task work with no Subagent Run

Task Tool exists specifically to delegate work. If Main Agent will do the work itself, it does not submit a Task work description. Every accepted description therefore starts one Subagent Run.

### Give Task an independent lifecycle

Task is a Run-scoped work description, not another actor, record, or state machine. Main Run submits later work through additional Task Tool Calls, handles Child Results, and judges the parent requirement.

### Add a persistent Task object to group Child Runs

Main Run History already records Task Tool Calls, Child references, and Child Inputs in order. A separate Task ID, table, status, result, or multi-wave grouping would duplicate that execution view without another owner or consumer.

### Serialize every Main Run in Session

This prevents the Main Agent from responding to later human input while delegated work proceeds. Main Runs and delegated work remain independent.

### Add a mandatory Verify stage

One generic stage cannot own factual integrity, business completion, and product policy across unrelated capabilities. Agents verify through ordinary Tools, and the Main Agent judges its delegated work.

### Add durable execution, cross-Worker takeover, and generic reconciliation

These mechanisms are not required for responsive conversation or reconstructable Runs. A lost execution ends as Interrupted; later work starts a new Run from committed facts.

## Acceptance criteria

- One User may own multiple direct Sessions, and each direct Session belongs to one User and one Main Agent.
- One Main Agent may execute multiple concurrent Main Runs in one Session.
- External and product inputs create or resume only Main Runs; they never enter Subagent Runs directly.
- Main Agent may perform work directly or call Task Tool; Prompt interpretation and complexity judgment remain model behavior, and Agent Loop does not force Task Tool use.
- Task Tool is directly exposed to Main Runs and unavailable to Subagent Runs, including through Tool search.
- Main Agent handles conversation, direct execution, delegation, synthesis, and completion judgment.
- Todo Tool is directly exposed to Subagent Runs and unavailable to Main Runs.
- Todo state belongs only to its Subagent Run, creates no descendant work, and does not block Final Output.
- One Task Tool Call submits one or more work descriptions, starts one Child Run per accepted description, and returns acceptance with Child references without waiting for completion.
- Task Tool Calls append delegated work to Main Run History; later calls may use earlier Child Results but do not update or reopen a persistent Task.
- Child Result and Need Input become ordered correlated Child Inputs independently from the already-settled Task Tool Call; Waiting Main resumes and Running Main consumes them in later Model Steps.
- Main Run selects further work, consumes Child Results, and judges whether the parent requirement is satisfied without Task completion or Task Result objects.
- Task and Todo are Run-scoped model working views rather than independent planners or lifecycle controllers; Goal mode is lightweight Session configuration and policy rather than a Goal entity or state machine.
- Main Runs and Subagent Runs use the same Agent Runner and Agent Loop.
- Task Tool creates only same-Agent Child Runs and has no target-Agent argument; work for another Agent uses A2A and creates that Agent's independent Main Run.
- Task has no Workspace; every Subagent Run inherits its parent Main Run's complete resolved authorization and Workspace access without inheriting parent Run History.
- An A2A Main Run resolves the receiver's own authorization and receives only explicit A2A Input; it never inherits the sender's authorization or implicit Context.
- A2A Tool Calls settle immediately; correlated A2A Result Input enters the exact non-terminal source Main Run for `consult` and `task_delegate`, while `notify` never waits for output.
- An A2A target Main Run survives source termination, and its late result cannot revive a terminal source Run.
- A waiting Main Run does not block new Session input, other Main Runs, or unrelated delegated work.
- Ordinary per-Run Parent termination, including Completed, cancels its active Subagent Runs without a completion gate or revival. Service-wide shutdown/startup cleanup interrupts all non-terminal Main/Subagent Runs, including Waiting, without automatic resume.
- A Subagent missing required input atomically enters Waiting with a correlated Child Need Input to Main; Main answers or waits for human input and then resumes the exact same Child Run, while a terminal Main causes immediate Child cancellation.
- A Session has at most one active Goal mode and stores its objective, committed progress, wait condition, and original `/goal` Session Input relation without a separate Goal table or ID.
- Goal mode repeatedly invokes the same Main Agent through ordinary Main Runs and adds no Goal object, status state machine, Agent role, Run type, Runner, or Agent Loop.
- A new Goal-mode Main Run reuses the original Session relation and cutoff, receives bounded committed continuation facts rather than inheriting earlier Run History, and never resumes a failed or interrupted Run.
- Goal mode has no `require_user` disposition; ordinary Need Input leaves the current Goal Main Run Waiting and an explicitly related human reply resumes it.
- Goal `continue` and `wait` complete the current iteration and later create new ordinary Main Runs; `wait` delays creation until its wake condition is satisfied and is distinct from Run Status Waiting.
- Goal dispositions create no new Session Input and do not automatically send Agent Replies; user-visible result delivery uses the unified outlet.
- Context compaction changes only model view and does not delete source facts.

## Risks and open questions

Task Tool names, remaining arguments, derived delegated-work presentation, and concurrency bounds remain implementation decisions.

The concrete Session storage shape, failure bound, wake-condition representation, and user controls remain implementation decisions under the fixed constraint that Goal mode adds no separate table, ID, domain object, or state machine.
