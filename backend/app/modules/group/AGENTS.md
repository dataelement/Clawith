# Group owner

Run-created message attachments verify the captured Main Tool call and never fabricate a human Principal. Their source-input binding retains bytes but does not backdate readability: other Runs use the first accepted reply position or explicit Run references. Delivery reads only the exact accepted reply's immutable references; publication without acceptance remains cleanup-eligible.

`public.py` owns Group configuration, active human membership, immutable events and per-Agent input/execution/result links. Other owners use typed public ports, including the narrowly scoped Channel reply lookup. The caller owns commits and post-commit notifications.

Group also owns the Agent roster, named conversations and human read watermarks. Conversations share membership and Workspace, but filter history, head positions and unread counts independently. Every event and Run link names its conversation. Agent roster membership never grants Agent visibility; candidates and execution targets retain the caller's captured access. Human mentions are metadata without dispatch. See [Group ownership](../../../../.agents/notes/implemented/architecture/2026-09-09-group-input-and-message-ownership.md).

Group row locking allocates event positions and deduplicates immutable human/message sources. Target selection uses captured Agent access; Group context never acquires a member's private Workspace. Main messages validate actual Tool origin, and their acceptance is independent of later Tool Result or Final persistence.

Run callbacks acquire Run before Group/link locks. Need Input question publication shares the Waiting transaction; answers persist their explicit Run/wait relation with related input atomically. Final stores the execution outcome without another reply. Members may create Groups, edit metadata and invite; the creator retains the minimum management relationship for removal. No old Participant hierarchy is restored.

The [product contract](../../../../specs/backend-product-inputs.md) controls this slice. Product API, Channel transport and full G006 acceptance require application wiring beyond owner tests.

`attachments.py` keeps Group upload metadata separate from Session. Other members cannot claim or read an unsubmitted upload; publication does not share it until it is bound to its uploader's event. Submitted files obey each Main's fixed event cutoff or explicit Run input references. Cleanup commits an expired-unbound claim under the same row lock as binding; subsequent publication, binding and reads reject the claim. Physical I/O uses the application storage port and per-object guard, with matching claim/publication/revision checks before metadata removal. No stored object is imported into Workspace automatically. See [input attachments](../../../../.agents/notes/implemented/architecture/2026-09-09-product-input-attachments.md).

Run-created files have a real `created_by_run_id` and bind to their accepted reply, not a fabricated human uploader or unrelated input. Message binding enforces at most eight files and sixteen MiB total. Cleanup requires both input and message bindings to be absent. External Trigger/Heartbeat publication requires the injected frozen-destination verifier; destination metadata does not grant Workspace access.
