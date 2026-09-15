# G006 unattended delivery and A2A continuation amendment

Status: user-confirmed implementation amendment — implementation and independent verification remain required.

## Scope and unchanged authority

This extends [product inputs](backend-product-inputs.md), [Session work control](backend-session-work-control.md) and [Workspace Memory scope](backend-workspace-memory-scope.md). Historical artifacts and receipts remain immutable. Run remains the sole lifecycle owner; A2A, Trigger and Heartbeat retain their existing product records. No new Agent, Workspace type, Task/Goal state machine, generic Artifact store or crash-recovery engine is introduced. G006 provides Backend queries; Frontend implementation and formal platform qualification remain later work.

## Unattended execution and explicit delivery

Trigger and Heartbeat execute unattended and report one-way. A destination must be explicitly configured; a creation Session is not an implicit destination. Human configuration may select an authorized same-Agent Session or an authorized Group conversation containing the Agent. A native Main may select only its already-authorized originating conversation; knowing another destination ID does not grant access. Configuration validation resolves and verifies the target before saving it, and each occurrence freezes its own destination.

With no destination, execution is still valid and results remain queryable through the occurrence and referenced Run History. With a destination, explicit messages use the same message outlet; the Run source remains Trigger or Heartbeat. A delivered message names its real source Run and does not fabricate a human Session Input or borrow an unrelated input as its origin. An unavailable destination produces a delivery failure, not deletion of execution results, a different recipient or a newly created Session. Final continues to settle execution only; it never automatically duplicates an already sent message.

Queryability does not make private input or results public to every user who can see the Agent. Occurrences retain authorized User/Group provenance, including explicitly selected personal-account provenance. Result queries filter that scope before loading bodies. A configured destination cannot authorize forwarding another user's private incoming message; delivery must also match the occurrence's private source scope. Neither a destination nor a private Credential grants access to an entire User Workspace.

An unattended Main cannot wait for human input. If essential information is unavailable, it reports the limitation and ends the occurrence. The resolved Run capability enforces that boundary in both Tool execution and Run's wait mutation. A Child may still ask its Parent for information; the Parent answers if possible or reports that it cannot finish. No platform-side fake question or indefinitely unanswerable human Waiting is introduced.

## A2A waiting and explicit takeover

New A2A requests start independent Main Runs. Source Tools return acceptance immediately. A source Main may explicitly wait for an outstanding request without holding its Tool call or asking a human. Run records an ordinary Waiting fact for related input, preserving the existing unseen-input race check and releasing execution resources. A2A delivery appends correlated input and wakes that wait. An already available result must not create a wait that no later event can satisfy.

Original source Run identity remains immutable. A2A separately records the current explicitly selected delivery Run. The original Main may operate on its request. A new Main of the same Agent may explicitly inspect or take over a request only through the same original Session, or the same original Group conversation, validated by those owners. Same Tenant or Agent alone does not grant access to private requests. An active delivery Main cannot be displaced. Autonomous sources lacking such a persistent conversation relation remain restricted to their original Main until an equivalent owner authorization exists.

An explicit takeover can read retained results, answer the existing target's Waiting request or wait for its result. It does not restart the target, change the original source, revive a terminal Run or automatically create a new Main. A target continues independently when its caller ends. Late results remain stored even without an active recipient. Only explicit activity reattaches current-process result delivery; startup does not scan old requests to restart execution.

## Temporary files and return to A

The receiving A2A Agent B processes delegated content and produces results as temporary files, not in B's shared Workspace files. B and its Children retain their own allowed reads, but their captured Workspace scope rejects ordinary shared-file writes. This applies at the Workspace mutation boundary, not only in prompts or by hiding a Tool. Existing Memory restrictions remain unchanged.

A2A owns a bounded temporary-file manifest on its existing request. Infrastructure supplies file storage mechanics through an injected owner-defined port; it does not become an Artifact or Workspace owner. Temporary files have logical names, current revisions, byte sizes and content hashes. A single request permits at most eight files, four MiB per file and sixteen MiB in total; the manifest is bounded to 64 KiB. Conditional publication records intent before external storage and publishes only a verified matching revision. Cancellation or failure cannot silently turn an unconfirmed write into a completed file.

B explicitly marks selected files for return through the original request, freezing their exact revisions, content hashes and byte sizes. A returned revision cannot be changed or deleted before A confirms its save; another result uses a distinct logical file. The current authorized A-side Main reads only those returned files and saves them through Workspace's existing revision-aware mutation. A's actual output scope determines the destination: the original User or Group Workspace for that conversation, or Agent Workspace for authorized Agent-owned work. B never acquires A's complete Workspace access. A repeated confirmed save returns its original receipt; a conflict cannot overwrite another writer's content.

Returned bytes must remain available until A's save is confirmed. B's Run ending is not permission to delete undelivered results. Unreturned temporary files can be cleaned after their producing family terminates; returned files can be cleaned after confirmed delivery. Cleanup must verify recorded revisions and publication state. Process restart does not resume either Agent's old Run. Retaining undelivered bytes is not execution recovery.

Additional attachments in an explicit A2A answer require source-owner authorization and request-scoped delegation just like initial attachments. They append related input rather than changing the original accepted input or exposing an entire Workspace. Implementation must trace input, temporary publication, return and destination save end to end.

## Verification

Require real PostgreSQL and actual assembled Tool/API paths for unattended denial, explicit destination validation and freezing, queryable no-destination results, and failed delivery without lost execution outcomes. Verify Child-to-Parent questions remain possible.

Verify A2A result arrival before and after wait commit, readiness without an empty wait, explicit same-conversation takeover, cross-conversation denial, no takeover of a live recipient, and target independence. No polling Model loop or fake human question substitutes for the wait path.

Verify temporary-file read/write/import/return through Model-visible Tools, denial of ordinary writes into B's shared files, exact source delegation, save into A's true Workspace, CAS conflicts, idempotent save, partial publication, cancellation and cleanup after confirmed transfer. After return selection, B's rewrite or deletion must be rejected and A must still receive the selected bytes. Maintain closed versioned persistence and stable default Snapshot encoding. Existing G006 regressions and independent code/architecture reviews remain required; document unverified external and formal-load surfaces separately.
