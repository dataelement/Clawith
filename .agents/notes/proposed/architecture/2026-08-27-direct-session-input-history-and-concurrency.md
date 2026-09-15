# Agent Note: Direct Session Input, History, and Concurrency

Status: proposed — the direct Session model and conversational work-control extension are user-confirmed; G006 product implementation and acceptance remain pending

## Problem

A direct Session must remain responsive while multiple Main Runs execute or wait concurrently. The architecture needs one authoritative definition of what creates Session Input, when input starts or resumes a Main Run, which committed history that Run may see, and how interleaved replies retain their origin.

Session must not become the global input bus for Group, Heartbeat, Trigger, A2A, Task, or other product events. Channel transport, Run History, Task internals, streaming, and delivery also need to remain outside Session ownership.

## Proposal

### Direct Session and Session Input

A direct Session is the human-facing conversation between one User and one Main Agent. User here means one Tenant Membership under [Account, Membership, Tenant, and Principal](2026-08-31-account-membership-tenant-principal.md), not a global Account. Session Input is an immutable, authenticated human input explicitly submitted to that Session for the Main Agent to process.

```text
Direct Web message --------+
Direct channel message ----+----> Session Input
Explicit reply to a wait --+
```

Session Input may contain human-authored text, ordinary uploaded message attachments, references to authorized Workspace files, and an explicit reply relation. Attachments need not enter Workspace before Agent Loop can use them. Session owns their input association and availability; an Agent explicitly saves them to an authorized Workspace only when the work requires it. Uploading a file without submitting a message does not create Session Input.

Web, Feishu, or another Channel Adapter authenticates and normalizes the external event and resolves its User and direct Session. The Adapter does not own Session Input or decide Agent behavior. A channel group message belongs to Group rather than direct Session.

The following facts do not create Session Input:

```text
Group event ----------> Group
Heartbeat ------------> Heartbeat
Trigger event --------> Trigger
A2A request ----------> A2A capability
Subagent Run Result --> correlated Child Result Input --> parent Main Run
Goal continuation ----> Session Goal mode
```

These product facts may initiate or resume Main Runs through their owner, but they do not create human-authored Session history. A human `/goal` command is ordinary Session Input and becomes the existing Session Input relation for its lightweight Goal mode. Automatic Goal continuation starts later ordinary Main Runs from that same relation without creating another Session Input.

### Session Input and Run Input

Session records the immutable Session Input before requesting execution. Session then supplies a Run Input that references the accepted Session Input and states what the Main Run must process.

```text
Human input
    |
    v
Session records Session Input
    |
    v
Session initiates Main Run with Run Input
```

Session Input remains valid even if Run creation or execution later fails. Run Input belongs to the execution request and does not replace, mutate, or become the authoritative user message.

### Deterministic start and resume

A human reply with an explicit relation to one Waiting Main Run resumes that exact Run through its Session-owned wait relation. Every other Session Input starts a new Main Run.

```text
explicit reply to Waiting Main Run ----> resume exact Main Run

all other Session Input ----------------> start new Main Run
```

Initial Session routing does not infer reply ownership from message text, the latest active Run, Tool names, or a global current Run. A new input therefore does not block on or silently enter another concurrent Main Run. After startup, the new Main Run may interpret that input and explicitly address an existing execution through the Session-owned work-control boundary below. Model-selected work association does not replace deterministic initial routing.

### Conversational work control

The user interacts through ordinary conversation, without creating a Task object or selecting a Run in a required UI workflow. A new ordinary message starts a new Main Run of the same Main Agent. That Run may do independent work, send a supplement or correction to an existing Main Run, request cancellation of existing work, or ask the user to clarify an ambiguous target. A message such as “add cost analysis to that report” does not require the new Run to produce another report.

Session owns the association between the accepted human message, the addressing Main Run and the target Main Run. It supplies bounded, authorized discovery of work in the same Session: originating request, current Run status and explicit execution reference, with bounded detail queries when needed. These are source-labelled observations recorded for the requesting Run, not an implicit import of another Run's full History or an expansion of its fixed Session-history cutoff. Observed status is not authorization or a guarantee that the target remains active.

Main-role Tools invoke Session's public work-control service through explicitly injected adapters. The model selects intent and target; the service validates the trusted caller, same Tenant, same Session, same Main Agent and the Session-to-Run relations before invoking Run's public input or cancellation contract. Subagents cannot use this surface. The tools do not grant access to other Sessions or Agents, write Run tables, bypass the target Main to direct its Children, or create a new generic coordinator, Task identity, Run relation owner or A2A request. Exact Tool names and parameter shapes remain implementation details.

A supplement preserves attribution to the originating human Session Input and the addressing Run/Tool call. Session supplies stable source correlation so retries cannot append the same accepted operation twice. Run commits the related input under its existing ordering and terminal-race rules. Running targets consume it at the next safe model-call boundary; Waiting targets resume. Acceptance means the input is committed, not that the current external operation was interrupted, the new requirement was completed, or an existing side effect was reversed. The original Main retains its Snapshot, coordinates its own Children and owns its final result. New input does not expand its captured authorization.

The addressing Main receives the operation result without waiting for the target's work to finish. It may acknowledge successful acceptance and end its own Run; this does not end the target Run, which is not its Child. Target output remains related to the target's original Session Input, while the addressing Run's reply relates to the new input. The accepted supplement is traceable between them and does not create another human Session Input.

For a stop request, the addressing Main identifies the intended work and asks Session to cancel its target Main Run. Run owns the Cancelled transition and cancellation of that target's active Children. Other Main Runs and their Children remain unaffected. A cancellation result reports the actual committed status; it never promises rollback of completed external effects. A repeated request cannot produce duplicate terminal transitions. A target that finished before cancellation is reported as already terminal rather than as newly cancelled.

If intent or target is ambiguous, Main asks through the conversation instead of guessing which work to mutate. If a supplement loses a race with terminal settlement, it is rejected for that target, the human input remains recorded, and the new Main can continue from authorized committed results or artifacts as new work. It cannot revive the old Run or silently claim the old execution adopted the change.

This extension preserves the existing parent-child lifecycle: a Main that still needs Child results stays Running or Waiting; Waiting releases execution capacity while Children continue. No platform completion gate prevents an early Final Output. Ordinary Main termination, including Completed, cancels its active Children; service-wide interruption retains its separate all-Interrupted rule. Conversational work control adds no cross-Run recovery or transfer of Child ownership.

### Fixed Session history cutoff

Every Session Input has an authoritative position in Session history. A Main Run started by that input receives a fixed Session-history cutoff containing the committed visible Session facts through that input.

```text
1. Human Input A
2. Agent Reply A
3. Human Input B  <---- Main Run B cutoff
4. Human Input C  <---- Main Run C cutoff
```

Main Run B may read facts 1 through 3; Main Run C may read facts 1 through 4. Context may rebuild a model view repeatedly from the same cutoff, but it cannot enlarge the cutoff merely because another Run later commits a reply, Child result, or other Session projection.

Later facts enter an existing Run only through an explicit owned path. A Subagent Run Result becomes a correlated Child Result Input after the Task Tool Call has already returned acceptance; it resumes a Waiting Main Run or enters the next Model Step of a Running Main Run. An explicit human reply to a Waiting Main Run enters as related input. Other new human input starts another Main Run, which may explicitly submit a Session-authorized supplement through conversational work control.

### Replies and execution results

Every Main Run initiated by Session remains related to an existing Session Input. An ordinary direct Main Run relates to its current human input. Automatic Goal-mode Main Runs relate to the original `/goal` Session Input without creating new inputs.

All visible Agent Replies use the [unified user-message outlet](2026-09-09-user-messages-and-run-completion.md) and retain their source Run and Session Input relation. One Main Run may send multiple replies without ending. Final commits Run terminal Status/History and the Session-owned execution result in one transaction; it does not automatically generate another reply. Result persistence failure rolls terminal settlement back without re-executing Model, message sending or work Tools.

Goal dispositions remain terminal iteration results consumed by Session. `continue` updates committed progress and starts the next ordinary Main Run; `wait` stores a condition before a later new Run; `achieved` disables Goal mode. None automatically generates a chat reply. User-facing Goal results use the common message outlet and original `/goal` input relation, without a Goal-specific Reply or projection type.

Goal mode has no separate `require_user` output. When a Goal Main Run produces ordinary Need Input, it remains Waiting. A human answer is an ordinary Session Input explicitly related to that wait and resumes the same Main Run under the standard Session rule. Other Session messages remain independent.

```text
Session Input A ----> Main Run A ----> Agent Replies A1, A2, ...
                                  └─> terminal execution result A
Session Input B ----> Main Run B ----> Agent Replies B1, B2, ...
                                  └─> terminal execution result B
```

Replies enter Session when their message records commit, independently of Run completion. Session does not delay Reply B merely because Main Run A started earlier and remains active.

```text
Input A
Input B
Reply B
Reply A
```

The explicit Input-Reply relation preserves ownership when completion order differs from input order. Task Tool Calls, Child Run progress, Child Results, and Run state may appear as Session projections derived from Run facts; they do not masquerade as Session Input, Agent Reply, or persistent Task objects.

### Delivery and ownership boundaries

Session owns accepted human Inputs, committed Main Agent Replies, their relations, authoritative history order, and visible delegated-work or Run projections.

Channel Adapter owns transport parsing and provider delivery. A committed Session Reply remains committed if channel delivery fails. Streaming deltas, delivery attempts, and provider acknowledgements are projections and do not become alternate Session or Run outcomes.

Session Input remains authoritative even when no Run was admitted. Session records whether the input has a started Run, explicit admission failure, or retryable pending start and never displays an unstarted input as Running. Retrying uses the same Session-owned source identity.

Session does not own Run History, Task Tool Calls, Child Run facts, Group events, Heartbeat, Trigger, A2A, Workspace content, or Channel delivery state. It references facts produced by those owners when they need to become visible in the human conversation.

## Alternatives considered

### Route every product input through Session

Group, Heartbeat, Trigger, A2A, Child Run, and Task Tool Call facts have different owners and are not human-authored direct conversation. Routing them through Session would turn it into a shared lifecycle and event bus.

### Resume the latest active Main Run for every new message

This serializes or ambiguously merges unrelated user work. Explicit wait relations are the only deterministic resume route; otherwise a new Session Input starts a new Main Run.

### Infer initial reply ownership from message text

Semantic inference cannot provide deterministic initial start/resume routing. Ordinary input starts a new Main Run; only an explicit Waiting reply resumes at intake. The new Main may subsequently select a work-control target, but Session validates the explicit operation and records its attribution.

### Require users to create or select tasks

The accepted interaction is conversation. Internal execution references support model Tools and validation, not a required task-management workflow for the user.

### Let the new Main take over existing Children

The original Main owns the work context, Child coordination and final result. Forwarding a supplement or cancelling the original Main preserves that responsibility; reassignment would introduce an unnecessary lifecycle transfer.

### Let active Runs observe all later Session facts

Implicitly expanding Context makes one Run's behavior depend on concurrent completion timing and prevents exact reconstruction. Each started Main Run keeps a fixed Session-history cutoff.

### Hold replies until earlier Runs finish

This preserves a superficial order by delaying independently completed work. Replies commit when ready and retain explicit originating Input relations.

## Acceptance criteria

- A direct Session belongs to one User and one Main Agent.
- Only an authenticated human input explicitly submitted to the direct Session creates Session Input.
- Direct Channel Adapters normalize and route messages but do not own Session Input or Agent behavior.
- Group, Heartbeat, Trigger, A2A, Subagent Run Result, and automatic Goal continuation facts do not create Session Input; the original human `/goal` command does.
- Session Input is recorded before execution and remains distinct from Run Input.
- An explicit human reply resumes the exact related Waiting Main Run; every other Session Input starts a new Main Run.
- Every started Main Run receives a fixed Session-history cutoff through its originating input.
- Automatic Goal-mode Main Runs reuse the original `/goal` Session Input relation and cutoff; committed Goal progress enters through explicit Product Input rather than later Session history.
- Later Session facts do not enter an active Run implicitly.
- A new Main can discover bounded same-Session work and explicitly supplement or cancel a validated target Main without becoming its parent or taking over its Children.
- Work-control observations and accepted inputs retain source attribution; operation retries are idempotent, terminal races are explicit, and accepted input is not reported as completed work.
- Ambiguous targets are clarified in conversation; denied or terminal targets are not silently replaced, revived or reported as successfully modified.
- Ending the addressing Main leaves the independent target active; cancelling a target affects only that target and its Child family.
- Every committed Agent Reply remains explicitly related to its originating Session Input and source Run; one Run may produce multiple replies.
- Final settles execution without automatically sending a reply; message acceptance, terminal settlement and Channel delivery remain separate facts.
- Goal Need Input uses ordinary Run Status Waiting and explicit reply; Goal `wait` instead completes the iteration and delays creation of a new Run until a future condition is satisfied.
- Goal terminal dispositions do not automatically create Agent Replies; visible messages use the common outlet and original `/goal` input relation.
- Concurrent replies commit when ready and do not wait for earlier Runs.
- Delegated-work and Run projections remain distinct from Session Input and Agent Reply.
- Channel delivery failure does not undo a committed Session Reply.
- Session does not own Run History, Task Tool Calls, Child Run facts, product events, Workspace content, streaming, or delivery state.

## Risks and open questions

Exact input, reply, relation, and projection fields; storage ordering; reconnect cursors; Channel delivery APIs; attachment reference formats; and UI presentation remain implementation decisions.
