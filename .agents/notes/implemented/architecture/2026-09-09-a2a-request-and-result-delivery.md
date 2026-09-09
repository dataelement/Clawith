# Agent Note: Separate A2A acceptance from independent result delivery

Status: implemented — owner services persist requests and target callbacks; application delivery integration is verified separately.

## Problem

A target Main may finish or need information after its source Tool has returned. Keeping the Tool call open would couple independent execution, while locking the source from target settlement introduces reciprocal lock cycles.

## Decision

A2A validates the source Main's captured message Tool call and target visibility, then records an idempotent request. Notify, consult and task_delegate retain their product intents; only the latter two require correlated source delivery. Target startup and terminal results use Run's transactional consumers. The target owns its authorization and receives explicit input, not the source Workspace or implicit personal credentials.

Attachment references require an application-injected source-owner verifier before a new request is accepted. An ordinary reference string cannot authorize delegation. Target reads validate the request's actual target Run/Agent association and exact accepted reference subset through A2A's public verifier; the attachment owner still enforces Tenant and published/bound storage facts. No sender Workspace access, implicit file enumeration or boolean authorization bypass is introduced.

An explicit answer may add attachments after the same source-owner authorization under the answering Main. It appends ordinary related input with source kind `a2a_answer` and the exact request ID; the original accepted request input never changes. Target attachment access recognizes only its original explicit subset or references in that target's supported Run input History with both this source kind and this request owner. Another request's answer or an unrelated input source cannot grant access. Missing or rejected source authorization prevents the answer from resuming the target.

Trusted application intake may attach the target's exact personal-connection references selected in the source's original human Session/Group input. Those selections were validated by Tool under the human Principal and remain in versioned input metadata, not model arguments. A2A persists only the requested target's references and receiver capture resolves them with that Agent's own grants. A receiver cannot reuse these references to authorize a later A2A target; it has no originating human input selection for that next request. Empty selection uses the target's Agent account and never inherits the sender's account choice.

`input_visibility` gives downstream message-trigger intake source visibility metadata without granting Workspace access. It validates immutable request/source Run snapshots, not the mutable delivery recipient. Non-Session/Group/A2A products first resolve their own frozen provenance through an injected typed resolver, keeping Trigger and Heartbeat imports out of A2A and preserving origin conversation metadata independently of output Workspace. Without such a resolved origin, a captured Membership or Group output supplies its subject, with the Group Main's exact conversation when available. An Agent output with one captured Membership Credential binding retains that Membership as its private visibility owner; multiple Membership owners fail explicitly. Otherwise an A2A receiver traces its validated parent request. When either captured shared-write permission is false, an unresolved Agent-output origin fails closed rather than becoming public merely because the intermediate Run writes to Agent Workspace. A genuinely Agent-owned source retains its original Agent visibility. The trace permits at most sixteen requests, rejects cycles or mismatched receiver associations and never guesses public visibility at its limit. The port reads no Workspace files or Secret bytes and does not change the receiving Agent's Workspace scope.

Target callbacks record pending delivery without touching the source Run. A separate source-input transaction acknowledges only the exact observed delivery key and recipient Run. A late acknowledgement cannot hide a later target result or consume delivery to a newly selected recipient. Omitting the acknowledgement recipient means the original source, never whichever Main most recently took over. An obsolete undelivered waiting question may be replaced by the target's terminal outcome; its original Waiting remains Run History. Answers lock both independent Main Runs in UUID order, then the request.

`source_run_id` retains original request attribution. An optional `delivery_run_id` names the Main that explicitly takes responsibility for later questions and results; absent means the original source. A new Main may inspect a request only when its actual persisted association has the same source Agent and direct Session, or the same source Agent, Group and conversation. Tenant equality or knowing a request ID is insufficient. Autonomous sources without a shared conversation retain their original-Run boundary. These checks use Session and Group public association readers, not cross-owner tables or fabricated human principals.

An explicit `takeover`, `wait` or `answer` may replace the delivery recipient only after the previous recipient is terminal. An active recipient is not displaced. Source and recipient Run identities are locked in UUID order before the request; answers include the target in that ordering. The target keeps its existing Run and authorization. A successful answer clears the current Waiting delivery projection while preserving its Run History fact, so waiting again observes the next target result rather than the answered question.

The `wait` Tool accepts only consult or task_delegate requests and returns immediately. An already available result is returned or delivered without entering Waiting. An unfinished request returns an owner-validated marker for application composition; Runner consumes a generic related-input wait only after the Model Step's Tool Results commit, checks unseen input before suspending, and resumes through its existing related-input path. It does not require a Child or manufacture a human question. Explicit takeover re-registers only that request in the bounded current-process delivery monitor. Registration identity prevents an older terminal scan from dropping a newly registered recipient; it is not another durable queue or recovery mechanism.

## Alternatives considered

A synchronous Tool waiting for target completion would occupy execution capacity and contradict immediate acceptance. Target-to-source nested settlement would create a reverse lock path; independent pending delivery removes that dependency without another execution state machine.

Reviving the source, automatically starting a replacement Main, or transferring the target into a Task tree would change the approved independent lifecycles. Explicit same-conversation takeover changes only result responsibility. Inferring access from Tenant or Agent identity alone would expose requests from another private Session.

## Consequences

Terminal delivery and authorized inspection include the bounded safe file projection defined by [temporary-file ownership](2026-09-09-a2a-temporary-files.md). The request result's closed persisted shape does not duplicate that manifest. Delivery records the projected names and revisions in related Run input; no storage coordinates or save-intent metadata enter the model context.

The source can end while the target continues. Source-terminal delivery is recorded without reviving it. Accepted requests and target outcomes survive independently of Tool Result persistence. External transport, explicit Credential delegation and application scheduling must be validated through their owning composition, not inferred from these service ports.

## Verification

Real PostgreSQL service tests cover concurrent acceptance, all three intents, target independence, terminal source delivery, Waiting rollback, stale delivery acknowledgements, visibility and actual Tool-call validation. These do not establish Channel transport or live model behavior.

Takeover tests additionally cover same-Session and same-Group-topic access, cross-conversation denial, refusal to displace an active recipient, immutable source attribution, delivery to the explicitly selected new Main, ready-result handling and rejection of waits on one-way notifications. Generic Run suspension and product Tool composition require their separate integration checks.

Application E2E uses actual Runtime, Tool execution, PostgreSQL and controlled Model HTTP to exercise unfinished A2A work suspending its source without a human question, result-driven resume of that same Run, and a result committed before the wait Tool returning without an empty wait. A separate flow ends the original source, accepts a new ordinary Session input, and has its new Main explicitly answer the existing Waiting target and receive its result. The target Run ID remains unchanged and the original source remains terminal. This does not establish hosted Model behavior or automatic recovery.

The additional-file E2E uploads two real Session attachments, delegates only the first, observes a denied target read of the second, and then has the source answer the target's question with that second reference through the actual A2A Tool. The target reads its bytes after resume, and History retains the request-scoped answer while the initial request input remains unchanged. Owner tests reject missing/denied authorization and references attributed to another request or source kind.

Visibility tests cover private Membership ancestry through an Agent-output A2A receiver, exact Group topic metadata, genuine Agent origin, captured personal bindings, wrong-Tenant denial and the sixteen/seventeen-hop boundary. Receiver snapshots remain unchanged; downstream trigger publication authorization is verified by its own application consumer.

A PostgreSQL regression exercises User input through an A2A receiver and an on-message Trigger occurrence into another A2A request. The intermediate Trigger's Agent output does not erase its frozen Membership provenance: absent or unresolved product-origin resolution rejects visibility, and an injected resolver reads that origin through Trigger's public service. The existing receiver Run remains running. Optional application hooks must isolate this resolution failure from the already accepted A2A execution.
