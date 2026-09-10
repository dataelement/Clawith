# Agent Note: Compose independent Group and A2A execution

Status: implemented — admission, product HTTP/Tool routing and current-process delivery have controlled integration tests; cumulative G006 acceptance and external qualification remain separate.

## Problem

Group events may select several independent Agents. A2A requests must launch the receiver under its own authority and return results without holding the sender's Tool call open or taking sender locks inside receiver settlement. Reusing a private sender's output as the receiver's own input must not permit publication into shared Agent Memory.

## Decision

`OtherProductInputs` composes Group, A2A, Agent, Workspace, Model and Run public services under the [G006 contract](../../../../specs/backend-product-inputs.md). Group input and selected targets commit before startup. Each target captures its own Agent configuration and the Group output Workspace; preparation uses eight bounded concurrent lanes, outside database locks. One target admission failure remains its own outcome. Group announcement content enters a labelled Snapshot source rather than being prepended to the bounded initial input.

Group also captures recent history through its public owner port, within the accepted input's conversation and strictly before its position. The labelled `product_context` Snapshot section records the conversation and cutoff; the current input is not duplicated in that section. The window selects at most 20 recent entries within 16 KiB, preserves full references on included entries and names oversized entries by their event ID and position instead of silently truncating their text. Private account-selection metadata is excluded. Human Waiting replies bind uploaded attachments in the same transaction as their accepted event and related Run input, before post-commit resume scheduling.

A2A input commits through its owning service before receiver startup. The receiver is a separate Main with its own Agent Workspace and Tool authorization; the sender's Workspace and grants are never copied. The receiver disables shared Memory writes when the sender has Membership/Group output or already carries private-input provenance. This flag is captured in the receiver Snapshot and propagates through later delegation. Plain Memory file writes and distillation use the same Workspace-owned restriction.

The [continuation amendment](../../../../specs/backend-product-input-continuations.md) additionally denies ordinary shared-file writes for A2A receivers and their Children. The application exposes [request-owned temporary-file Tools](2026-09-09-a2a-temporary-files.md), explicit return selection and revision-aware saves into the current authorized source's output scope; it does not create another Workspace or copy the sender's file authority. A2A waiting and same-origin takeover use the [request owner's existing delivery relation](2026-09-09-a2a-request-and-result-delivery.md).

Run startup, Waiting and terminal callbacks record Group/A2A owner facts inside the original transaction. A2A receiver callbacks never acquire sender Run locks or perform delivery. A separately owned application task monitors only requests accepted by this process, using batches of at most 100 metadata-only delivery-state reads. It performs idempotent sender input acceptance and delivery recording in another transaction, then invokes Run's post-commit scheduling port. A failed delivery retains its request for retry and does not stop unrelated requests. Source termination does not cancel the receiver or reopen the source.

The monitor has a 1024-entry in-memory admission bound including intake reservations. This is bounded current-process bookkeeping, not another durable queue or request lifecycle. Application starts the monitor after Runtime is ready and closes it before Runtime shutdown, preventing late delivery from admitting work during termination. Shared HTTP, storage and database resources close after Runtime. Restart does not repopulate this registry or replay old execution.

## Alternatives considered

Reusing sender execution scope for the receiver would transfer private Workspace access and Tool authority. Waiting synchronously inside the A2A Tool would retain an execution slot until remote work ended. Delivering to the sender inside receiver settlement would introduce cross-Run lock ordering and couple two independent outcomes. The approved design uses explicit input, independent startup and post-commit correlated delivery instead.

## Consequences

Group and A2A completion remain execution facts; visible Group messages use the message outlet, not Final. A2A previews and result references are supplied by its owner. The monitor does not promise recovery after process loss or exactly-once external effects. Human input account selections are validated and retained by Session/Group. Composition looks up the current or explicitly addressed target's exact connections from the original input, persists only the receiver's selected A2A connections, and never forwards that authority to another A2A hop. Default capture uses Agent accounts.

## Verification

The metadata lookup has a real PostgreSQL test proving one bounded query without request payload materialization and checking Tenant/size limits. Product E2E exercises actual application resources, owner transactions, Runtime, Tool execution and controlled Provider HTTP for multiple Group targets, isolated target failure, A2A source termination and private-source shared-Memory denial. Group and A2A owner tests plus these product E2E scenarios passed 23 tests; scoped Ruff and Pyright passed. Hosted Provider behavior, frontend delivery and formal 50-Agent qualification remain separate evidence.

Personal-account E2E verifies real Credential decryption and MCP Authorization headers for a human input's current Agent and directly addressed A2A target. A subsequent A2A hop uses its own default account even when the original human input named another target's personal connection. Wrong-Agent and wrong-Membership selections are rejected before input/execution. Goal continuation retains its original personal account after logout; a source disabled after input acceptance makes later A2A/Goal capture fail without fallback. Model requests and Session history fragments exclude account-selection metadata.

Real ASGI Group tests exercise authenticated creation, Agent invitation, candidate and roster reads, conversation history, Model-observed topic isolation, work results, read watermarks and topic removal. Remote Model HTTP is controlled; these checks are not live-provider or frontend acceptance.

Actual application tests also cover same-Run A2A wait/resume, an already-ready result without an empty wait, explicit new-Main takeover, and additional answer-file delegation. The temporary-file E2E exercises receiver/Child processing, returned-file discovery, shared Workspace write denial and saving exact returned bytes through the source scope. These focused results do not claim cumulative G006 acceptance or formal fifty-Agent qualification.
