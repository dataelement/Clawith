# Agent Note: Preserve Group events independently of Agent execution

Status: implemented — Group owner services persist membership, inputs, messages and execution associations; product wiring is a separate verification surface.

## Problem

A human Group event can address several Agents. Admission or execution failure of one target must not remove the event, rewrite other results or turn Agent replies into new human input.

## Decision

Group stores one immutable input with independently selected Agent links. Active members may read and write Group conversation, using their captured Agent visibility to select targets. Ordinary members retain Group creation, metadata editing and invitation. A creator Membership supplies the minimum retained manager relationship; removing it is prohibited while it is the only manager. Group context excludes private member Workspace content.

Human Membership and Agent roster entries are separate Group-owned relations, not generic Participants. Agent invitation requires captured Agent access; an Agent target must also be in the active Group roster. Joining a Group does not grant any human access to that Agent. Paginated Agent candidates and roster views respect the caller's captured Agent IDs before pagination. The human invitation directory exposes only same-Tenant active membership IDs and display names, without granting access to administrator membership management.

Each Group has an atomically created default conversation and may create additional named conversations. These are Group-owned topics, not direct Sessions: all topics share Group membership and one Group Workspace. Inputs, replies and execution links retain their conversation identity; positions remain monotonic across the Group. History selection, latest position and unread counts are conversation-scoped. A human's read watermark advances monotonically only to a real event in that conversation; unread counts exclude that human's own messages.

Only the creator or administrator may remove a topic. Removal first commits a short Group-locked transaction that closes new admission and fails pending links. Removing the default selects another active topic or creates an empty default in that same transaction. The caller then pages linked Runs and cancels each Main family in separate Run-before-Group transactions, notifying Runtime after each commit. A repeated removal can finish an interrupted cancellation sweep without reopening admission or creating another default. Independent A2A Runs and Trigger configurations are not part of this sweep. Startup callbacks reject closed topics; terminal consumers still settle their preserved historical links. Removed topics cannot be reopened.

Human mentions are validated Group member IDs in immutable event metadata and do not start executions. Explicit Agent targets remain the only human-message dispatch list. Group work lists expose bounded execution references and terminal indexes, while Run remains authoritative for status and execution history. Cancellation authorizes the actual linked Main, acquires its Run lock before product locks, records the Group result in the same transaction, and requires post-commit scheduling by the caller.

The Group WebSocket replays committed events from an explicit position through the same bounded public history projection. Initial authorization pins a canonical conversation, including when the caller selects the current default. Later polls recheck Group membership and that topic's availability without switching to a replacement topic. The topic-specific head prevents other topics' newer positions from creating an empty-page busy loop. The shared product WebSocket transport checks the fixed login's expiration/logout, bounds writes and releases its disconnect task on close. Group does not subscribe to transient raw Model deltas: the retained legacy Group transport published committed `message.created` facts, and no new Group stream authority is introduced.

Human input may additionally carry versioned account selections per target Agent. Tool validates them under that input's human Principal before they commit. Selection alone does not start another Agent. Current target capture and A2A requests use only the exact connections assigned to their target in the originating event, not another member's historical choice. Account metadata is absent from model-visible Group message content; it remains separately readable through the owning input relation.

Group message files record the actual producing Main in `created_by_run_id`, with no fabricated human uploader or unrelated originating input. Ordinary Group Runs use their persisted Group/conversation relation; Trigger and Heartbeat producers additionally require the injected frozen-destination authorizer. Published file content binds to the actual accepted reply through `bound_message_id`. Human uploads retain their separate Membership and input-event association. Cleanup selects only files lacking both bindings, and its committed claim blocks later publication or binding. Channel delivery checks both the explicit accepted-message reference and the sending Run's original read authority; inserting an opaque reference into a message does not itself grant file access.

Run startup links and terminal results use caller-owned transactions. Group messages use actual Main Tool-call correlation, commit before Tool acceptance, and do not imply Final. Need Input records a question through the same message storage in the Waiting transaction. Human answers store their explicit Run/wait relation while appending Run input. Final never manufactures an additional reply. Channel reads a reply through a Tenant/Agent/source-validated public lookup.

## Alternatives considered

Restricting Group creation and invitation to Tenant administrators would remove supported human collaboration. Restoring the old generic Participant system would carry obsolete architecture into the rewrite. A creator relation and Membership records preserve the required behavior without that hierarchy.

Flattening all Group topics into one history would remove the retained Group Session capability. Reusing direct Session would mix two product owners. Group-owned topics preserve that capability without another execution lifecycle. Old Group Runtime tools and mention planning are not restored: Agent messages remain main-only accepted replies without automatic target dispatch, Agent-to-Agent work uses A2A, and shared Memory and Files use the current Workspace owner rather than per-Agent Group Memory or human write reconciliation.

## Consequences

Committed messages remain visible if Tool Result is lost and the Run is later Interrupted. Human input and each target's admission/result remain separate. Group positions serialize short owner writes only; Run operations precede Group locks and no external I/O holds them.

## Verification

Real PostgreSQL tests cover ordinary-member creation and invitation, outsider/target denial, deduplicated input positions, fixed cutoffs, message-before-ToolResult interruption, question rollback and idempotent explicit answers. Full API, websocket and multi-provider Channel acceptance remain application-level tests.

Owner tests additionally cover roster visibility and explicit invitation, topic-separated history and work, human mention metadata without dispatch, independent unread counts, concurrent monotonic read positions, forbidden cross-topic read positions, default-topic replacement and authorized Main/Child cancellation. A paused real Run startup exercises removal committing before its start consumer: admission fails and the uncommitted Run rolls back. These owner checks do not establish frontend replacement or live provider behavior.

Real ASGI WebSocket tests verify committed topic replay, absence of another topic's content, no publication of an uncommitted event, delivery after commit, logout closure and rejection of a non-member. A separate owner test proves that newer positions in another topic neither advance this stream's cursor nor leave `has_more` set on an empty page.

Run-file owner tests use actual Main and Group reply records with an injected exact destination verifier. They cover unattended publication without a human input, retention after message binding, normal Group Run creator attribution, cross-Run read/publication/binding denial, immutable revision rejection and cleanup claims blocking binding even with an old timestamp. Actual scheduled destination configuration and physical storage publication require application-level tests.
