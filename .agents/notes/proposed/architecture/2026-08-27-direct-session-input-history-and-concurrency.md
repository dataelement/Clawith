# Agent Note: Direct Session Input, History, and Concurrency

Status: proposed — the direct Session input, Run-routing, history-cutoff, and reply model is agreed but not implemented

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

Session does not infer reply ownership from message text, the latest active Run, Tool names, or a global current Run. A new input therefore does not block on or silently enter another concurrent Main Run.

### Fixed Session history cutoff

Every Session Input has an authoritative position in Session history. A Main Run started by that input receives a fixed Session-history cutoff containing the committed visible Session facts through that input.

```text
1. Human Input A
2. Agent Reply A
3. Human Input B  <---- Main Run B cutoff
4. Human Input C  <---- Main Run C cutoff
```

Main Run B may read facts 1 through 3; Main Run C may read facts 1 through 4. Context may rebuild a model view repeatedly from the same cutoff, but it cannot enlarge the cutoff merely because another Run later commits a reply, Child result, or other Session projection.

Later facts enter an existing Run only through an explicit owned path. A Subagent Run Result becomes a correlated Child Result Input after the Task Tool Call has already returned acceptance; it resumes a Waiting Main Run or enters the next Model Step of a Running Main Run. An explicit human reply to a Waiting Main Run enters as related input. Other new human input starts another Main Run.

### Replies and interleaved completion

Every Main Run initiated by Session remains related to an existing Session Input. An ordinary direct Main Run relates to its current human input. Automatic Goal-mode Main Runs relate to the original `/goal` Session Input without creating new inputs.

For an ordinary direct Main Run, Session commits its Agent Reply related to the current input in the same database transaction as Run terminal Status and outcome History. If Reply persistence fails, terminal settlement rolls back without re-executing Model or Tool. For Goal mode, `continue` and `wait` are terminal iteration outputs consumed in that terminal transaction to update the existing Goal fields and do not create Agent Replies. `continue` starts the next ordinary Main Run immediately; `wait` starts it only after the stored wake condition is satisfied. `achieved` and final stopped failure produce ordinary Agent Replies related to the original `/goal` input. No Goal-specific Reply or projection type is introduced.

Goal mode has no separate `require_user` output. When a Goal Main Run produces ordinary Need Input, it remains Waiting. A human answer is an ordinary Session Input explicitly related to that wait and resumes the same Main Run under the standard Session rule. Other Session messages remain independent.

```text
Session Input A ----> Main Run A ----> Agent Reply A
Session Input B ----> Main Run B ----> Agent Reply B
```

Concurrent replies enter Session when their outcomes commit. Session does not delay Reply B merely because Main Run A started earlier and remains active.

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

### Infer reply ownership from message text

Semantic inference can route the same message differently and cannot provide a stable concurrency contract. Reply ownership must be explicit.

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
- Every committed Agent Reply remains explicitly related to its originating Session Input.
- Goal Need Input uses ordinary Run Status Waiting and explicit reply; Goal `wait` instead completes the iteration and delays creation of a new Run until a future condition is satisfied.
- Goal `continue` and `wait` create no Agent Reply, while `achieved` and final stopped failure use ordinary Agent Reply related to the original `/goal` input.
- Concurrent replies commit when ready and do not wait for earlier Runs.
- Delegated-work and Run projections remain distinct from Session Input and Agent Reply.
- Channel delivery failure does not undo a committed Session Reply.
- Session does not own Run History, Task Tool Calls, Child Run facts, product events, Workspace content, streaming, or delivery state.

## Risks and open questions

Exact input, reply, relation, and projection fields; storage ordering; reconnect cursors; Channel delivery APIs; attachment reference formats; and UI presentation remain implementation decisions.
