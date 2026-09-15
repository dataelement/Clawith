# Agent Note: User messages and Run completion

Status: proposed — user-confirmed architecture; G006 implementation contracts, owner amendments and execution verification remain pending.

## Problem

One human request may require an acknowledgement, several useful updates and later delivery of results. Treating each visible message as Final would end the responsible Run and its Children too early. Sending progress through one path and automatically turning Final into another chat reply would also create two message authorities and risk duplicate delivery.

## Proposal

### One message outlet, separate execution settlement

A Main Run may produce zero or more user-visible messages and exactly one terminal outcome. All user-visible Agent messages, including acknowledgement, progress, questions and delivery of finished work, use one logical message outlet. Their wording does not choose Run Status. Final settles execution and does not automatically create or resend a chat message. It may carry structured execution results or references to already committed messages and artifacts for the initiating owner; these are not a second copy of a user reply.

Main decides what to communicate. The initiating product owner records the message and its source Run/input association; Session owns direct-conversation messages, while other product owners retain their own destinations and records. Channel owns external transport and delivery acknowledgement. Destination comes from the trusted initiating relationship rather than an arbitrary model-selected recipient. Runner owns lifecycle and History, not message routing or Channel delivery.

The first release expresses the message outlet as a Main-only Tool, reusing the existing Tool execution contract rather than adding Provider-native output encoding. Destination is injected from the initiating relationship. Subagents return their execution results to Parent through the existing Run contract; they do not gain a direct user-message outlet. Exact Tool names and bounded parameter fields remain implementation details.

### Execution and context

Sending a message neither creates a Run nor resumes one, enters Waiting or ends execution. Tool execution, explicit waiting, related input and Final retain their own control paths. One Need Input operation commits its visible question, waiting fact and reply relation in the same database transaction, then publishes the committed question through the common message outlet. It never requires the model to send a question and enter Waiting in separate calls. Merely sending a question is not a Waiting transition.

The message content and its actual send outcome remain attributable to the producing Run. Run History stores the owning model/output or Tool facts; product message records are not re-injected as fresh human inputs or duplicated in the same Run's Context. Other active Runs do not automatically consume later Session messages. New Runs retain the fixed Session-history cutoff and explicit source rules. Assistant messages cannot trigger self-replies through the human-input entry path.

A message saying “done” does not itself complete the Run or prove that the work succeeded. Final may still be premature: ordinary Main termination, including Completed, cancels active Children. The existing decision accepts this execution error and introduces no Child-completion or message-delivery completion gate. A Main still needing Child results remains Running or Waiting; Waiting releases execution capacity and Child results resume the same non-terminal Main.

### Commit, failure and interruption

Product message acceptance and external delivery are separate outcomes. Message acceptance uses source Run and Tool Call correlation for idempotency; retries cannot create another copy of the same accepted message. Publish only after commit. External delivery deduplication depends on the Channel protocol and is not an unconditional exactly-once guarantee. Never report successful external delivery solely from local acceptance, and never re-execute the Agent or its work Tools merely to retry message delivery. Exact persistence, streaming and delivery retry behavior belong to the G006 implementation contract.

Run terminal Status/History and the same-database initiating owner's execution result still commit atomically through OutcomeConsumer. This transaction no longer requires creation of a user-visible reply. A result-recording failure retries settlement from the produced outcome without repeating Model, message sending or work Tools. Final is not evidence that a message reached its recipient; send success is not evidence of a terminal Run.

Explicit cancellation yields Cancelled; unrecoverable failure yields Failed. Ordinary Main terminal settlement cancels its active Children. Service-wide shutdown or restart interrupts all remaining Running and Waiting Main/Child Runs without normal Parent wakeup; abrupt process loss leaves the interruption sweep to startup before readiness. Existing terminal outcomes remain unchanged. Committed messages, History, Snapshots and artifacts survive; old execution is never automatically resumed or replayed.

A message committed before a crash remains committed even if Final never commits and the Run later becomes Interrupted. A terminal result may coexist with pending or failed Channel delivery. Neither condition justifies inventing the missing outcome or reversing an existing one. Product-owned failure notification policy remains a module implementation decision and must use the same message outlet without fabricating a model-authored answer.

### Related contracts and scope

This decision supersedes the automatic Final-to-Agent-Reply clauses in [Direct Session](2026-08-27-direct-session-input-history-and-concurrency.md), [Main/Task](2026-08-27-session-main-agent-parallel-task-model.md) and [Product Output](2026-08-28-product-input-main-run-and-output-boundaries.md). It preserves [Runner](2026-08-27-agent-runner-lifecycle-and-history.md) terminal transactions and [service interruption](2026-09-07-service-wide-run-interruption.md). Existing approved G003–G005 artifacts and receipts remain historical bindings; implementation must review and bind affected amendments rather than rewriting their hashes.

Goal terminal dispositions remain Session-owned control/results. They do not themselves send chat messages. Goal result delivery uses the common outlet and original goal-input relation, without a new Goal-specific message type. This decision does not determine whether Goal, Trigger or Heartbeat schedules new work after restart.

## Alternatives considered

### Send progress separately and let Final send the answer

This retains two message-producing paths and can duplicate an answer already delivered before settlement. One outlet for all user-visible messages preserves a single delivery contract.

### Make message sending end execution

A progress update would cancel useful Child work or require another execution lifecycle to keep it alive. Message publication must not imply completion.

### Require all Children or a delivered message before accepting Final

The accepted lifecycle deliberately has no completion gate. Model guidance and task evaluation address premature completion; platform checks do not redefine business completion.

## Verification required

Verify multiple messages from one Main, continued execution after sending, final settlement without an extra reply, Need Input correlation and no assistant-message self-wakeup or duplicate Context injection. Exercise message acceptance failures, repeated submissions, Channel failure, terminal transaction retry, and crashes on either side of message and terminal commits. Preserve ordinary Child cancellation and service-wide all-Interrupted behavior. No runtime, Provider or delivery verification is claimed by this Note.
