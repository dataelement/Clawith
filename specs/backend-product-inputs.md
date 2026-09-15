# G006 product input implementation contract

Status: approved implementation contract — user-confirmed behavior and independent code/architecture preflight; owner amendment receipts bind implementation scope, not completed execution.

## Authority and scope

This contract extends [execution dependencies](backend-execution-dependencies.md), [core Runtime](backend-core-runtime.md), [Session work control](backend-session-work-control.md) and the approved foundation outside the changes below. It implements Session, A2A, Group, Trigger, Heartbeat and Channel services, minimal existing-login HTTP and WebSocket transport, Goal continuation and product-facing Run composition. It does not implement registration, password recovery, SSO, frontend replacement, Sandbox activation or crash recovery. Existing schemas may be corrected before the G008 migration baseline; historical approved artifacts remain unchanged.

Each module owns its existing input/configuration/result tables. Cross-owner operations use typed public ports and caller-owned transactions. No generic Product Event, Task/Goal table, recovery lease, global database lock or second Runner is introduced. Input, admission, message acceptance, terminal result and external delivery are distinct facts.

## Run transactional ports

`StartConsumer.record_started(transaction, *, run: RunView)` runs only for a newly created Main, inside the transaction containing Run, Snapshot and initial History, before scheduling. The consumer resolves the initiating owner from the trusted Run source; source deduplication does not repeat the callback. Failure rolls back startup and releases reserved admission through existing reconciliation. Child creation is unchanged.

`WaitingConsumer.record_waiting(transaction, *, run: RunView, waiting: WaitingPayload)` runs only after a new Main human-question Waiting fact is accepted. It shares Tool-result settlement and Waiting transactions; unseen input, duplicate wait, Child waits and empty task-result waits do not create user questions. Consumer failure retries retained settlement without repeating Tool execution. OutcomeConsumer continues to record only terminal execution results, never automatic replies.

Run retains all lifecycle authority. Any operation acquiring both Run and product locks orders them Run before product; no product row lock spans Snapshot preparation, Model/Tool I/O or another independently opened transaction. Application composition owns consumer routing and resource lifecycle, not business tables.

## Session and messages

Session belongs to one Tenant Membership and Agent. Public operations create/list/read authorized Sessions, accept immutable input, page committed history, accept Main messages, associate started Runs, record Waiting questions and terminal results, and discover/supplement/cancel same-Session work. Members access only their own Sessions and currently captured Agent scope. Known IDs never bypass Tenant, Membership, Agent or Session relations. Transport authenticates before invoking typed services.

Input and pending admission commit first. Stable client source keys deduplicate acceptance; content of an accepted key never changes. Main startup links commit through StartConsumer. Admission failure preserves input and exposes an unstarted outcome. Pending input left before startup is not automatically executed after restart; explicit same-source retry is available. Explicit Waiting replies append to that exact Run; other inputs create new Main Runs with a fixed Session-history cutoff.

Use existing Session entries for messages and retain source Run, step and call correlation plus originating input. Adjust source uniqueness/constraints to permit multiple idempotent replies from one Run without a separate message state machine. The Main-only message Tool commits through Session before returning accepted/message identity. Destination is injected, not model-selected. Final writes only the existing link's execution result. A message committed before loss of Tool Result remains valid; neither startup nor delivery retries recreate the Model or Tool call.

Need Input uses the waiting consumer to atomically record its question and wait association. Its question uses the same message storage and delivery path as ordinary messages. Mere question text cannot enter Waiting. Subagents retain parent-only results and no direct message outlet.

Bound history/work lists with cursor pagination, fixed input cutoffs and at most 100 items per request; bound text/normalized payloads at intake to at most 256 KiB and source keys to 512 characters. Attachments retain explicit input ownership, authorized access and bounded references; uploading does not itself start work or require Workspace import. No arbitrary URL retrieval or private-to-shared Memory publication occurs through attachment handling.

Work-control Tools use Session's validated same-Session Main associations, then Run's public related-input/cancel methods. Accepted supplements retain human-input and Tool correlation; acceptance is not completion. Terminal targets remain terminal. New Main completion does not cancel an independently addressed Main. Unknown/ambiguous targets return bounded information or use ordinary Need Input.

## Login and WebSocket

Expose the existing local login/logout/authenticate workflow only; no public identity bootstrap. Product login passes a fixed 24-hour absolute TTL to Auth, with no sliding extension or automatic renewal. Authenticated HTTP controls and Waiting replies require a valid current captured Principal. Existing Runs and autonomous configurations do not depend on human online status.

WebSocket streams only authorized Session data, closes at login expiry/logout or disconnect, and releases its tasks. Reconnect reads committed message positions; bounded transient delta queues may request resynchronization instead of growing without bound. A notification is not authoritative message storage. Run attempts have explicit stream attempt/discard signals; failed attempt text is not persisted as a completed reply.

## Goal

Session's existing Goal configuration stores objective, progress, original input/cutoff, wait condition and enabled flag. Successful `continue` starts a new ordinary Main; `wait` waits for its condition before a new Main; `achieved` disables continuation. Need Input preserves the existing Waiting Run. Failed stops automatic continuation; Interrupted work is not restarted. Cancellation disables future continuation and cancels the active family. No automatic new iteration bypasses Run's three-attempt Model policy. Terminal iteration and Goal progress update are atomic; new Run admission occurs after that commit using stable source correlation.

## Other product owners

A2A keeps `notify`, `consult`, `task_delegate` intent over immediate one-way acceptance or asynchronous correlated result delivery. Validate source Main and target visibility through public owners. Target has independent Agent authorization and only explicit input/delegation, never sender Workspace access. Persist target linkage through start consumer and target result through outcome consumer. Delivery to non-terminal source is idempotent; terminal source does not cancel the target or revive itself. No held Tool call waits for target completion.

Group owns membership, announcement, immutable human events, selected Agent targets, messages and results. Only explicit selected/mentioned authorized Agents receive independent Main Runs. Enforce active Group membership and Group Workspace direction, not implicit private member context. Per-Agent admission/result links remain separate; one target failure does not erase the event or other results.

Trigger and Heartbeat retain separate configurations and occurrence tables. Implement explicit future schedules and accepted occurrence intake with stable deduplication, bounded due scans and application-owned cancellable dispatch. Heartbeat never creates a Trigger. Normal scheduling does not require human login. No catch-up replay or restoration of interrupted executions is introduced. Explicit personal account delegation remains bounded, owner-validated and attached to the exact configuration/request; defaults use Agent accounts.

Channel owns enabled configuration, external identity mapping and inbound authentication/normalization plus outbound delivery attempts. Direct human events enter Session; group events enter Group. Assistant deliveries cannot loop into human input. Persist message acceptance before external I/O, reuse delivery correlation on retry and keep ambiguous external outcomes distinct from success. Credential access uses its public owner-bound service. Retained provider coverage must be traced from the rewrite inventory before claiming complete replacement; a generic adapter interface alone is not Channel implementation.

## Verification and completion

A2A target settlement records only its result and pending delivery in the target transaction, without acquiring the source Run lock. After commit, a separate transaction locks source Run before the A2A delivery record and atomically appends correlated input and marks delivery. Channel persistence explicitly represents uncertain external send outcomes; a timeout with unknown external effect must not become a safely retryable failure. Add that representation through the existing Channel schema amendment, not a second delivery owner.

Use real PostgreSQL owner services and application entry paths, replacing only external Provider/Channel peers and clocks. Required direct Session E2E includes ordinary concurrency, message-before-Final, no duplicate Final reply, same-source input/message retries, Waiting question rollback, unseen-input suppression, explicit wait reply, fast startup association, fixed cutoffs, expiry/relogin/reconnect and authorized work control. Exercise message/input commit crash windows without automatic work replay.

Product-input E2E covers Group isolation and multiple targets, independent A2A target/results and source termination, Trigger/Heartbeat deduplication and normal scheduling, Goal continue/wait/achieved/failure/cancel, delegated account isolation, Channel delivery failure without Run replay, and full lifespan task cleanup. Owner tests cover invalid versions, bounds and concurrent constraints. Cumulative G000–G005 checks remain applicable.

Formal performance evidence remains separately reported. Do not change frozen benchmark thresholds or report skipped/unsupported measurement as a pass. No G007 work begins as part of this contract.
